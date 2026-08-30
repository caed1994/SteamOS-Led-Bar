# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""Service entry point: shim -> renderer -> USB serial -> ESP."""

from __future__ import annotations

import argparse
import errno
import glob
import itertools
import logging
import os
import select
import signal
import subprocess
import sys
import time

from . import config as config_module
from . import cec as cec_module
from . import desktop, elf, load, notify, phone, render, serialport, shim
from . import steamworks
from . import temperature
from .link import EspLink

LOG = logging.getLogger("steamos-utility-center")

PROGRAM = "steamos-utility-center"
DEVICE_RETRY_DELAY = 5.0

# A configuration the service will not accept. Restarting cannot rewrite a
# file, so the unit names this code in RestartPreventExitStatus rather than
# retrying every RestartSec and burying the one line that says what is wrong.
CONFIG_REFUSED_EXIT = 2

# What the strip does while the machine is asleep. Fixed on purpose - it is
# not a notification and not an effect, it is what "the machine is off but
# alive" looks like, and it should look the same on every machine.
#
# Here rather than in the firmware all the same: the ESP has to draw it,
# because during a suspend there is no host to render anything, but changing
# how it looks should not mean reflashing every board.
#
# Barely there on purpose: a dark room at night, not a night light. With the
# breath's 5% floor this sweeps roughly 2..30 of 255. Not lower - a WS2812
# quantises badly in the bottom few steps, and white starts to tint.
STANDBY_COLOR = (30, 30, 30)
STANDBY_PERIOD_MS = 6000            # one slow breath, calmer than the waiting one

# Steam has not touched the LEDs since the module loaded - see shim. Rendering
# that faithfully is a black strip, and the ESP's own startup breath dies at
# the handshake to make way for it.
UNTOUCHED_SEQ = shim.UNTOUCHED_SEQ

# So the bar keeps breathing instead, in the firmware's own startup amber and
# at its rhythm. Sent from here rather than left to the firmware because the
# link is already up by then: connecting later would reset the board at
# exactly the moment Steam takes over.
STARTUP_COLOR = (40, 16, 0)
STARTUP_PERIOD_MS = 3000

# Words on the trigger pipe that are not flashes. The pipe is already read in
# the main loop and is world-writable, so the sleep hook has somewhere to say
# this without a second channel to keep alive.
STANDBY_WORD = "standby"
RESUME_WORD = "resume"

# How long standby may last while the process is actually running. The point
# is the clock: time.monotonic() does not advance across a suspend, so this
# only counts seconds the machine spent awake. If the resume hook never fires
# the bar puts itself right within half a minute; a machine asleep for three
# days still comes back to a breathing strip.
STANDBY_MAX_AWAKE = 30.0


class _Stopped(Exception):
    """Raised internally when a signal asks us to shut down."""


def anything_shows(config, shows):
    """Whether either mode puts `shows` on the bar on this machine.

    Two settings can ask for the same effect, independently. RAINBOW_SHOWS is
    which of them holds Steam's rainbow slot in Game Mode; DESKTOP_SCENE names
    one outright on the desktop, where there is no menu to share. So a gauge
    is wanted here if *either* asks for it - and the sources below are what a
    gauge has to read.

    Reported, and this is the whole of it: with DESKTOP_SCENE=load and
    RAINBOW_SHOWS left at the rainbow, nothing built the counters, the load
    gauge had nothing to read, and render._substitute handed the slot back to
    Steam's rainbow. So picking the load gauge on the desktop showed a
    rainbow - the one effect it was chosen instead of. Same for the
    temperature gauge, by the same two lines.

    Only the two that read hardware need asking about. Fire and the aurora
    are arithmetic and draw wherever they are named.
    """
    return (config["RAINBOW_SHOWS"] == shows
            or desktop.scene_shows(config["DESKTOP_SCENE"]) == shows)


def shown_where(config, shows, name):
    """Two lines saying which of the modes puts `name` on the bar here.

    Both, always. What the two diagnostics said before was "the rainbow slot
    shows %r, set RAINBOW_SHOWS=load to put this there" - true, and half the
    answer: it names the Game Mode setting to someone who may well be asking
    because their *desktop* is showing the wrong thing, and following it puts
    the gauge in the one mode they were not looking at.
    """
    lines = []
    if config["RAINBOW_SHOWS"] == shows:
        lines.append("In Game Mode the rainbow slot holds the %s - pick "
                     "\"Rainbow\" in Steam's LED menu." % name)
    else:
        lines.append("In Game Mode the rainbow slot shows %r; set "
                     "RAINBOW_SHOWS=%s for the %s there."
                     % (config["RAINBOW_SHOWS"], shows, name))
    if desktop.scene_shows(config["DESKTOP_SCENE"]) == shows:
        lines.append("On the desktop DESKTOP_SCENE=%s, so the %s is on the "
                     "bar whenever Steam is not." % (shows, name))
    else:
        lines.append("On the desktop DESKTOP_SCENE=%s; set DESKTOP_SCENE=%s "
                     "for the %s there."
                     % (config["DESKTOP_SCENE"], shows, name))
    return lines


def build_temperature_source(config):
    """A sensor to read, or None unless something on this machine shows it."""
    if not anything_shows(config, render.SHOWS_TEMPERATURE):
        return None
    return temperature.TemperatureSource(path=config["TEMPERATURE_SENSOR"])


def build_load_source(config):
    """Counters to read, or None unless something on this machine shows them."""
    if not anything_shows(config, render.SHOWS_LOAD):
        return None
    return load.LoadSource()


def build_overheat_watch(config):
    """A watch over every sensor, or None when warnings are switched off.

    Nothing to do with the gauge: that shows one sensor you picked, this looks
    at all of them, and either works with the other switched off.
    """
    if not (config["NOTIFY"] and config["NOTIFY_WARNING"]):
        return None
    return temperature.OverheatWatch()


def build_renderer(config):
    return render.Renderer(
        led_count=config["LED_COUNT"],
        mapping=config["MAPPING"],
        reverse=config["REVERSE"],
        max_brightness=config["MAX_BRIGHTNESS"],
        min_brightness=config["MIN_BRIGHTNESS"],
        gamma=config["GAMMA"],
        speed_scale=config["SPEED"],
        patrol_dots=config["PATROL_DOTS"],
        temperature=build_temperature_source(config),
        temperature_range=(config["TEMPERATURE_MIN"],
                           config["TEMPERATURE_MAX"]),
        load=build_load_source(config),
        rainbow_shows=config["RAINBOW_SHOWS"],
        # Parsed here rather than in the renderer: notify owns the spelling of
        # a colour, and it imports from render, so render cannot ask it.
        load_cpu_colour=notify.parse_color(config["LOAD_CPU_COLOR"]),
        load_gpu_colour=notify.parse_color(config["LOAD_GPU_COLOR"]),
        load_swap=config["LOAD_SWAP"],
    )


def build_scene(config):
    """The snapshot to show in Desktop Mode, or None to leave the bar alone."""
    return desktop.scene_snapshot(config["DESKTOP_SCENE"],
                                  config["DESKTOP_COLOR"],
                                  config["DESKTOP_BRIGHTNESS"],
                                  config["DESKTOP_SPEED"])


def warn_scene_split(config):
    """Say so once if a config file used to mean something else here.

    Desktop Mode's rainbow scene used to be the same slot as Game Mode's, so
    DESKTOP_SCENE=rainbow with RAINBOW_SHOWS=fire put fire on the desktop. Now
    that the desktop has a fire scene of its own, that pair means the rainbow
    - which is what it says, but not what the bar was doing yesterday.

    Not migrated silently. Either reading of that file is defensible and only
    the person who wrote it knows which they meant, and a setting that changes
    itself is worse than one that says what it now means. Logged rather than
    refused for the same reason: nothing here is broken.
    """
    if (config["DESKTOP_SCENE"] != desktop.SCENE_RAINBOW
            or config["RAINBOW_SHOWS"] == render.SHOWS_RAINBOW):
        return
    LOG.info("DESKTOP_SCENE=rainbow now means Steam's rainbow, not the %s "
             "the rainbow slot holds in Game Mode; set DESKTOP_SCENE=%s for "
             "that on the desktop", config["RAINBOW_SHOWS"],
             config["RAINBOW_SHOWS"])


def notification_colors(config):
    """The named triggers whose colour the configuration can change."""
    return {kind: notify.parse_color(config[prefix + "_COLOR"])
            for kind, prefix in config_module.CONFIGURABLE_KINDS}


def notification_styles(config):
    """The triggers that flash in a shape of their own.

    Anything left out follows NOTIFY_STYLE, which is what the default means -
    so the general setting stays the one knob for "all of them look like
    this", and a kind only leaves it when someone says so.
    """
    return {kind: config[prefix + "_STYLE"]
            for kind, prefix in config_module.CONFIGURABLE_KINDS
            if config[prefix + "_STYLE"] != notify.STYLE_INHERIT}


def build_link(config):
    return EspLink(
        port=config["SERIAL_PORT"],
        baudrate=config["BAUD"],
        led_count=config["LED_COUNT"],
        reconnect_delay=config["RECONNECT_DELAY"],
        autodetect_baud=config["BAUD_AUTODETECT"],
    )


class Runner:
    def __init__(self, config):
        self.config = config
        self.running = True
        self.renderer = build_renderer(config)
        self.link = build_link(config)
        self.overlay = notify.NotificationOverlay(
            enabled=config["NOTIFY"],
            duration=config["NOTIFY_DURATION"],
            led_count=config["LED_COUNT"],
            style=config["NOTIFY_STYLE"],
            colors=notification_colors(config),
            styles=notification_styles(config),
            reverse=config["REVERSE"],
            max_brightness=config["MAX_BRIGHTNESS"],
            repeat_gap=config["NOTIFY_REPEAT_GAP"],
        )
        self.overheat = build_overheat_watch(config)
        # What to show while Steam is not driving the bar, and who decides
        # whether it is. Both None when DESKTOP_SCENE says to leave it alone,
        # which is what keeps the loop's ordinary path exactly as it was.
        self.scene = build_scene(config)
        # And, for the scenes drawn out of the renderer's substitutable slot,
        # which of them this one is. Game Mode's answer is a setting on the
        # renderer; the desktop's is the scene itself, so it travels with the
        # snapshot - see _shows.
        self.scene_shows = desktop.scene_shows(config["DESKTOP_SCENE"])
        warn_scene_split(config)
        self.ownership = None if self.scene is None else desktop.Ownership()
        self.trigger = None
        self.source = None
        # Set while the machine is going to sleep: the ESP is breathing on its
        # own and the loop must stay quiet, or the next rendered frame would
        # end the standby a millisecond after it started.
        self.standby_since = None
        # Whether the ESP has been told to keep breathing until Steam turns up.
        self._breathing_for_steam = False

    def stop(self, *_args):
        self.running = False

    def install_signal_handlers(self):
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, self.stop)

    # -- shim device ------------------------------------------------------

    def _open_source(self):
        """Open the shim device, waiting for it to appear if necessary."""
        device = self.config["DEVICE"]
        warned = False
        while self.running:
            source = shim.ShimSource(device)
            try:
                source.open()
                LOG.info("reading LED state from %s", device)
                return source
            except OSError as exc:
                if not warned:
                    if exc.errno == errno.ENOENT:
                        LOG.error(
                            "%s not found - is the leds-valve-shim kernel module "
                            "loaded? (sudo modprobe leds-valve-shim)", device)
                    else:
                        LOG.error("cannot open %s: %s", device, exc)
                    warned = True
                self._sleep(DEVICE_RETRY_DELAY)
        raise _Stopped()

    def _sleep(self, seconds):
        deadline = time.monotonic() + seconds
        while self.running:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(remaining, 0.25))
        raise _Stopped()

    def _recover(self, message, *args):
        """Wait, then reopen the device after it stopped making sense.

        The pause is the point. Every way this device can misbehave leaves it
        readable, so poll() keeps returning at once and the loop would reopen
        and re-read as fast as the CPU allows - a burnt core, and a warning per
        turn. Backing off makes a misconfigured DEVICE one message every few
        seconds instead.
        """
        LOG.warning(message, *args)
        self._sleep(DEVICE_RETRY_DELAY)
        self.source.close()
        self.source = self._open_source()

    # -- main loop --------------------------------------------------------

    def run(self):
        self.install_signal_handlers()
        try:
            self._open_trigger()
            self.source = self._open_source()
            self._loop()
        except _Stopped:
            pass
        finally:
            LOG.info("shutting down, clearing strip")
            self.link.shutdown()
            if self.source is not None:
                self.source.close()
            if self.trigger is not None:
                self.trigger.unlink()
        return 0

    def _open_trigger(self):
        if not self.config["NOTIFY"]:
            return
        trigger = notify.FifoTrigger(self.config["NOTIFY_FIFO"])
        try:
            trigger.open()
        except OSError as exc:
            # Not fatal: without the pipe the bar simply never flashes.
            LOG.warning("notifications disabled, cannot use %s: %s",
                        self.config["NOTIFY_FIFO"], exc)
            return
        self.trigger = trigger

    def _wait(self, interval):
        """Block until the LED state changes, a trigger arrives, or timeout.

        Returns (state changed, trigger ready). Waiting on both at once keeps a
        notification from sitting in the pipe while the bar is idle.
        """
        sources = [self.source.fd]
        if self.trigger is not None and self.trigger.fd >= 0:
            sources.append(self.trigger.fd)
        readable, _, _ = select.select(sources, [], [], interval)
        return (self.source.fd in readable,
                self.trigger is not None and self.trigger.fd in readable)

    def _poll_trigger(self, now):
        if self.trigger is None:
            return
        try:
            words = self.trigger.read()
        except OSError as exc:
            LOG.warning("notification trigger failed: %s", exc)
            self.trigger.close()
            self.trigger = None
            return
        for word in words:
            if word.lower() == STANDBY_WORD:
                self._enter_standby()
            elif word.lower() == RESUME_WORD:
                self._leave_standby("asked to")
            else:
                self.overlay.trigger(word, now)

    def _enter_standby(self):
        if not self.config["STANDBY_PULSE"]:
            LOG.info("standby pulse is switched off, leaving the strip dark")
            return
        colour = tuple(int(channel * self.config["MAX_BRIGHTNESS"] / 255)
                       for channel in STANDBY_COLOR)
        if self.link.send_standby(colour, STANDBY_PERIOD_MS):
            self.standby_since = time.monotonic()
            LOG.info("standby: the ESP has the strip until we are back")
        else:
            LOG.warning("could not hand the strip over for standby")

    def _hold_for_steam(self):
        """Leave the strip to the ESP while Steam has still said nothing.

        Once told, it keeps breathing on its own until the next frame - so
        this only has to speak up again after something interrupted it, which
        a notification flash does.
        """
        if not self.link.connected:
            self._breathing_for_steam = False
            return
        if self._breathing_for_steam:
            return
        if self.link.send_standby(STARTUP_COLOR, STARTUP_PERIOD_MS):
            self._breathing_for_steam = True
            LOG.info("Steam has not set the LEDs yet - leaving the startup "
                     "breath to the ESP until it does")

    def _leave_standby(self, why):
        if self.standby_since is None:
            return
        self.standby_since = None
        LOG.info("standby over (%s)", why)

    def _showing(self, snapshot, now):
        """Which snapshot to draw: Steam's, or the desktop scene.

        Steam's whenever Steam has any claim on the bar, which is the safe way
        round: a scene that held the bar through a Game Mode session would be
        this ignoring the LED settings somebody just changed, where a scene
        that gives way too readily is only a scene you do not see.
        """
        if self.scene is None or self.ownership.steam_has_it(snapshot, now):
            return snapshot
        return self.scene

    def _shows(self, showing):
        """What the frame about to be drawn puts in the rainbow's slot.

        None whenever Steam has the bar, which is what leaves the renderer on
        RAINBOW_SHOWS: that setting is Steam's menu's, and Steam's menu is
        still what a Game Mode rainbow was picked from.
        """
        return None if showing is not self.scene else self.scene_shows

    def _loop(self):
        started = time.monotonic()
        snapshot = None
        showing = None
        last_key = None
        # The soonest the next frame may go out. FPS is meant to be a ceiling
        # and was only ever a timeout: the loop wakes on every write to the
        # device, so the frame rate was Steam's write rate rather than the one
        # asked for. Measured on a Steam Machine: during a download Steam
        # writes the progress bar four hundred times a second, and every one
        # of them was rendered and pushed down a link that carries about
        # sixty. The state is still read every time; only the drawing and the
        # sending are held to the rate in the config file.
        due = 0.0
        # Whether a frame is being held back, which is the only time the wait
        # below has to be cut short. Without it the loop woke at the frame
        # rate even when nothing was changing, and the idle heartbeat - the
        # whole of IDLE_FPS - stopped happening.
        pending = False

        while self.running:
            connected = self.link.connect()
            self.link.poll()

            interval = 1.0 / self.config["FPS"]
            # The renderer decides, not the snapshot: the temperature gauge
            # occupies the rainbow's slot, and Steam still calls that animated.
            # Asked about what is on the bar rather than about what Steam last
            # said, which are not the same thing while a scene is up.
            if (showing is not None
                    and not self.renderer.is_animated(showing,
                                                      self._shows(showing))
                    and not self.overlay.active):
                interval = 1.0 / self.config["IDLE_FPS"]
            # Never wait past the moment a frame is due. Without this the wait
            # would run to the idle heartbeat and the frame rate would halve
            # whenever Steam writes a little faster than the frames go out.
            if pending:
                waiting = time.monotonic()
                if waiting < due:
                    interval = min(interval, due - waiting)

            try:
                changed, triggered = self._wait(interval)
            except OSError as exc:
                self._recover("poll on %s failed: %s",
                              self.config["DEVICE"], exc)
                continue

            if triggered:
                self._poll_trigger(time.monotonic())

            if self.overheat is not None:
                # Cheap on most turns - it reads nothing until its own
                # interval is up - so it can sit in the loop unguarded.
                reason = self.overheat.poll(time.monotonic())
                if reason is not None:
                    LOG.warning("overheating: %s", reason)
                    self.overlay.trigger("warning", time.monotonic())

            if changed or snapshot is None:
                try:
                    new_snapshot = self.source.read()
                except (OSError, shim.SnapshotError) as exc:
                    snapshot = None
                    self._recover("reading %s failed: %s",
                                  self.config["DEVICE"], exc)
                    continue
                if new_snapshot is None:
                    # Readable but empty. The shim answers every read with a
                    # whole snapshot or an error, so this is a different
                    # device - and one that would spin the loop in silence.
                    snapshot = None
                    self._recover("%s is readable but returns no snapshot - "
                                  "is DEVICE pointing at the shim?",
                                  self.config["DEVICE"])
                    continue
                snapshot = new_snapshot
                key = snapshot.key()
                if key != last_key:
                    LOG.debug("state change: %s", snapshot)
                    last_key = key

            if snapshot is None or not connected:
                continue

            now = time.monotonic()
            if self.standby_since is not None:
                # Silence is the point: the ESP is breathing on its own, and
                # one frame from here would end it. The machine is about to
                # suspend, so this loop is about to stop turning anyway.
                if now - self.standby_since > STANDBY_MAX_AWAKE:
                    # Half a minute of running time means we never went to
                    # sleep, or came back without being told. Take the strip
                    # back rather than leave it breathing at an awake machine.
                    self._leave_standby("still awake")
                else:
                    continue

            # Read every write, draw at the rate that was asked for. Skipping
            # here rather than earlier keeps the trigger pipe and the overheat
            # watch above answering at whatever rate the loop turns.
            if now < due:
                pending = True
                continue
            pending = False

            showing = self._showing(snapshot, now)

            # A flash covers the whole bar; nothing underneath is worth drawing.
            payload = self.overlay.frame(now)
            if (payload is None and showing is snapshot
                    and snapshot.seq <= UNTOUCHED_SEQ):
                # Nothing to show yet, and black is not an improvement on the
                # breath the ESP is already running. A flash still gets
                # through - it is the branch above - and lands us back here
                # afterwards, which is when the breath is asked for again.
                # A scene is something to show, so it comes up on a machine
                # that has not been in Game Mode since it booted rather than
                # waiting for a session that may never happen.
                self._hold_for_steam()
                continue
            if payload is None:
                payload = self.renderer.render(showing, now - started,
                                               self._shows(showing))
            if self._breathing_for_steam:
                LOG.info("%s; taking the strip back",
                         "Steam set the LEDs" if showing is snapshot
                         else "there is a desktop scene to show")
                self._breathing_for_steam = False
            # Idle heartbeat too: the firmware blanks the strip if we go quiet.
            self.link.send_frame(payload, self.config["LED_COUNT"])
            # From FPS rather than from the interval above: that one is how
            # long to wait before resending an unchanged frame, which is a
            # different question from how fast a changing one may go out. A
            # download's progress bar is a static effect that changes every
            # write, and holding it to the idle rate would make it a slideshow.
            due = now + 1.0 / self.config["FPS"]


# -- alternative modes ----------------------------------------------------


def _interrupt_on_sigterm():
    """Let the interactive modes run their cleanup when systemd stops them."""
    def handler(_signum, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, handler)


def run_desktop(config):
    """Report the desktop scene, and who has the bar - a Desktop Mode command.

    Which is not a limitation to work around but the shape of the thing: there
    is no terminal in Game Mode, so this can only ever be run on one side of
    the question it is about. What it finds live is therefore always the
    desktop half.

    The other half is what the service *recorded* while nobody could watch.
    It logs which mode it saw at startup and on every change, so the journal
    is where a Game Mode session can be looked at afterwards - and this reads
    it rather than printing a journalctl line to be typed, because the answer
    to "was Game Mode ever recognised on this machine" should not be two more
    steps away.

    Got wrong in the direction that matters, a scene would sit on the bar
    through a game and ignore everything Steam asked for, with nothing on
    screen to say why. That is the failure this exists to make visible.
    """
    scene = build_scene(config)
    print("DESKTOP_SCENE=%s" % config["DESKTOP_SCENE"])
    if scene is None:
        print("  The bar mirrors Steam in both modes, which is the default.")
        print("  Pick a scene on the panel's Desktop mode page to change it.")
    else:
        print("  In Desktop Mode the bar shows: %s"
              % desktop.describe(config["DESKTOP_SCENE"],
                                 config["DESKTOP_COLOR"],
                                 config["DESKTOP_BRIGHTNESS"],
                                 config["DESKTOP_SPEED"]))

    found = desktop.running_game_mode()
    print("Right now: %s"
          % ("Game Mode - %s is running" % found if found
             else "Desktop Mode, no %s process running"
             % " or ".join(desktop.GAME_MODE_PROCESSES)))

    now = time.monotonic()
    ago = None
    try:
        with shim.ShimSource(config["DEVICE"]) as source:
            snapshot = source.read()
    except OSError as exc:
        print("Steam's last LED write: cannot tell, %s is not readable (%s)"
              % (config["DEVICE"], exc))
    else:
        ago = desktop.steam_wrote_ago(snapshot, now)
        print("Steam's last LED write: %s"
              % ("never since the module loaded" if ago is None
                 else "%.0f seconds ago (%s)" % (ago, snapshot.effect_name)))

    # The rule the service goes by, spelled out from the two answers above
    # rather than asked of an Ownership: that one would scan /proc a second
    # time and log its own version of this into the middle of the report.
    steams = bool(found) or (ago is not None and ago < desktop.GRACE_SECONDS)
    print("So the bar is %s."
          % ("Steam's" if scene is None or steams
             else "the desktop's, showing your scene"))
    if scene is None:
        return 0

    # And the Game Mode half, which cannot be looked at while it is happening.
    print("")
    lines, why_not = desktop.journal_ownership()
    if why_not:
        print("What it saw before now: %s." % why_not)
        print("  Try it with sudo - that record is the only way to see what "
              "happens in Game Mode.")
    elif not lines:
        print("What it saw before now: nothing in the last two days.")
        print("  The service says which mode it is in when it starts and "
              "whenever that changes, so an empty list means it has not run "
              "since this was installed. Restart it, then switch to Game Mode "
              "and back:")
        print("    sudo systemctl restart steamos-utility-center")
    else:
        print("What it saw before now:")
        for line in lines:
            print("  %s" % line)
        if not any(desktop.GAME_MODE_MARK + " is running" in line
                   for line in lines):
            # The whole point of reading this. Every line saying "desktop"
            # and none saying Game Mode, on a machine that has been in one,
            # is detection that is not working - and the symptom without this
            # is a bar that quietly ignores Steam during a game.
            print("  Nothing there recognised a Game Mode session. If you "
                  "have been in one since the last restart, that is the bug "
                  "to report - the bar would be keeping your scene through "
                  "games.")
    print("")
    print("The plainest check needs no terminal at all: switch to Game Mode "
          "and look.")
    print("The bar should go back to Steam's own setting rather than staying "
          "on the scene.")
    return 0


ROUTE_MARKS = {"ok": "WORKS ", "crashed": "CRASH ", "failed": "no    "}


def _report_route(route, status, detail):
    """One probed route per line, as select_route() works through them."""
    print("  [%s] %-52s %s" % (ROUTE_MARKS[status], route, detail), flush=True)


def run_check_config(config):
    """Report the settings this configuration adds up to.

    Reaching here at all means the file parsed and passed validate(), because
    main() loads it before dispatching - so this is also the answer to "would
    the service accept this file?", which is what anything replacing a working
    config wants to know before it does.
    """
    for key in sorted(config):
        print("%-18s %s" % (key, config[key]))
    return 0


def run_temperature(config):
    """List the machine's temperature sensors and show what the gauge does.

    Which one is right is a per-machine question, so all of them are listed
    with their readings and the chosen one marked - which is what writing a
    TEMPERATURE_SENSOR line by hand needs.
    """
    sensors = temperature.find_sensors()
    if not sensors:
        print("This machine reports no temperature sensors at all under %s."
              % temperature.HWMON_ROOT)
        print("The gauge cannot work here; the rainbow is shown instead.")
        return 1

    chosen = temperature.pick_sensor(sensors)
    # What the overheat warning would do here, whether or not it is on: the
    # thresholds come from the machine, so this is the only place anyone can
    # see them before switching it on.
    watch = temperature.OverheatWatch()
    watched = {sensor["path"]: threshold for sensor, threshold in watch.resolve()}

    print("Temperature sensors on this machine:")
    # Nine spaces, which is what "  [use ] " takes on the rows below.
    print("         %-12s %-12s %6s  %-24s %-10s %s"
          % ("chip", "label", "now", "limits it publishes", "warns at",
             "path"))
    for sensor in sorted(sensors, key=lambda entry: entry["rank"]):
        celsius = temperature.read_celsius(sensor["path"])
        limits = temperature.read_limits(sensor["path"])
        alarms = temperature.read_alarms(sensor["path"])
        threshold = watched.get(sensor["path"])
        print("  [%s] %-12s %-12s %6s  %-24s %-10s %s"
              % ("use " if sensor is chosen else "    ",
                 sensor["chip"], sensor["label"] or "-",
                 "%.1f C" % celsius if celsius is not None else "-",
                 ", ".join("%s %g" % (name, limits[name])
                           for name in temperature.LIMIT_FILES
                           if name in limits) or "-",
                 "%.1f C" % threshold if threshold is not None else "-",
                 sensor["path"]))
        if alarms:
            # The driver's own opinion. Not what the warning acts on - a
            # latched flag would then warn about weather from an hour ago -
            # but worth seeing when you are looking for trouble.
            print("        ^ the driver reports %s raised" % ", ".join(alarms))

    print()
    if watched:
        print("%d of %d sensors are watched for overheating, each against the "
              "limit it" % (len(watched), len(sensors)))
        print("publishes itself, less %g degrees. One warning needs %d seconds "
              "above that" % (watch.margin, int(watch.dwell)))
        print("line - a chip touches its limit whenever it boosts, and that is "
              "not a fault.")
    else:
        print("No sensor here publishes a limit of its own, so there is "
              "nothing to compare")
        print("against and overheat warnings stay off whatever "
              "NOTIFY_WARNING says.")
    print("Sensors without a limit are left alone on purpose: what is hot "
          "depends on")
    print("what is being measured, and guessing for an unknown part is how "
          "false alarms")
    print("are made.")

    print()
    source = build_temperature_source(config)
    if source is None:
        print("Nothing here shows the temperature gauge.")
        for line in shown_where(config, render.SHOWS_TEMPERATURE,
                                "temperature gauge"):
            print(line)
        return 0

    celsius = source.celsius()
    print("Reading %s: %s"
          % (source.path,
             "%.1f C" % celsius if celsius is not None else "nothing"))
    print("The whole bar takes one colour, from green when cool to red when "
          "hot:")
    stops = render.temperature_stops(config["TEMPERATURE_MIN"],
                                     config["TEMPERATURE_MAX"])
    last = len(stops) - 1
    for index, (mark, colour) in enumerate(stops):
        note = "and below" if index == 0 else (
            "and above" if index == last else "")
        print("  %5.1f C %-9s #%02x%02x%02x"
              % (mark, note,
                 int(colour[0]), int(colour[1]), int(colour[2])))
    print("Between the marks the colour is mixed, so it moves as the machine "
          "does.")
    print("Read every %g s and averaged over %g s, because a CPU sensor jumps "
          "a degree" % (source.interval, source.smoothing))
    print("or two between readings and the colour would twitch with it.")
    if celsius is not None:
        red, green, blue = render.temperature_colour(
            celsius, config["TEMPERATURE_MIN"], config["TEMPERATURE_MAX"])
        print("Right now: #%02x%02x%02x across all %d LEDs"
              % (int(red), int(green), int(blue), shim.LOGICAL_LEDS))

    print()
    for line in shown_where(config, render.SHOWS_TEMPERATURE,
                            "temperature gauge"):
        print(line)
    return 0


def run_load(config):
    """Show what the load gauge can read here, and what it would draw.

    Whether the GPU half works at all is a driver question - amdgpu answers,
    most others do not - so this says which counters were found before anyone
    wonders why half the bar is mirrored.
    """
    source = load.LoadSource()
    gpu_path = source.resolve()

    print("CPU: %s" % ("counters in " + load.CPU_STAT
                       if load.read_cpu_totals() is not None
                       else "NOT FOUND at " + load.CPU_STAT))
    print("GPU: %s" % (gpu_path if gpu_path else
                       "no gpu_busy_percent - this driver does not publish one"))
    print()

    if load.read_cpu_totals() is None and gpu_path is None:
        print("Neither can be read here, so the gauge falls back to the "
              "rainbow.")
        return 1

    # One interval, so the CPU has two readings to subtract - the first can
    # only ever be a baseline.
    source.fractions()
    time.sleep(source.interval * 2)
    cpu, gpu = source.fractions()

    print("Over %.2f s: CPU %s, GPU %s"
          % (source.interval * 2,
             "%.0f%%" % (cpu * 100) if cpu is not None else "-",
             "%.0f%%" % (gpu * 100) if gpu is not None else "-"))
    if gpu is None:
        print("With no GPU counter the CPU is drawn on both halves, so the "
              "bar stays")
        print("symmetric rather than leaving one side dark.")
    print("Two bars grow out of the middle: CPU to the left in amber, GPU to "
          "the")
    print("right in blue. Read every %g s, averaged over %g s."
          % (source.interval, source.smoothing))

    print()
    for line in shown_where(config, render.SHOWS_LOAD, "load gauge"):
        print(line)
    return 0


def run_steam_check(config):
    """Report what the Steamworks path can and cannot find on this machine."""
    print("Steam directory:   %s" % (steamworks.steam_root() or "NOT FOUND"),
          flush=True)

    bits = elf.class_name(steamworks.WANTED_ELF_CLASS)
    print("This Python:       %s, so it needs a %s library" % (bits, bits))

    candidates = steamworks.find_libraries()
    if candidates:
        print("libsteam_api.so candidates:")
        for path in candidates:
            found = elf.elf_class(path)
            label = elf.class_name(found)
            mark = "use " if found == steamworks.WANTED_ELF_CLASS else "skip"
            print("  [%s] %-7s %s" % (mark, label, path))

    try:
        library = steamworks.find_library(config["STEAM_LIBRARY"])
        print("chosen library:    %s" % library, flush=True)
    except steamworks.SteamworksError as exc:
        print("chosen library:    NONE (%s)" % exc)
        library = None

    if library:
        # The route into ISteamUserStats differs most between SDK generations,
        # so show it up front.
        try:
            symbols = elf.exported_symbols(library)
        except (OSError, elf.ElfError) as exc:
            print("symbols:           unreadable (%s)" % exc)
        else:
            relevant = steamworks.interesting_symbols(symbols)
            print("symbols:           %d exported, %d relevant"
                  % (len(symbols), len(relevant)))
            for symbol in relevant:
                print("  %s" % symbol)

    sources = steamworks.app_id_sources()
    for label, value in sources:
        print("%-28s%s" % ("running app (%s):" % label, value or "none"))

    app_id = next((value for _label, value in sources if value), None)
    if library is None:
        print()
        print("Without the library nothing else can be tried.")
        return 1
    if not app_id:
        print()
        print("No game is running, so the API cannot be initialised as one.")
        print("Start a game and run this again.")
        return 1

    print(flush=True)
    print("Trying each way into ISteamUserStats as app %d." % app_id)
    print("Each one runs in a child process, so a crash costs a fork and not")
    print("the diagnosis - the flat wrappers are built against one interface")
    print("version, and the wrong one segfaults rather than failing politely.")
    print(flush=True)

    route, count = steamworks.select_route(app_id, library,
                                           reporter=_report_route)
    print(flush=True)

    if route is None:
        print("No route worked. Please report the list above.")
        return 1

    print("Working route: %s (%d achievements)" % (route, count))

    stats = steamworks.UserStats(app_id, library, route=route)
    sys.stdout.flush()
    try:
        stats.open()
        achievements = stats.achievements()
        unlocked = sum(1 for value in achievements.values() if value)
        print("%d achievements, %d unlocked" % (len(achievements), unlocked))
        for name, is_unlocked in sorted(achievements.items())[:5]:
            print("  [%s] %s" % ("x" if is_unlocked else " ",
                                 stats.display_name(name)))
        if len(achievements) > 5:
            print("  ... and %d more" % (len(achievements) - 5))
    except steamworks.SteamworksError as exc:
        print("Re-opening the working route failed: %s" % exc)
        return 1
    finally:
        stats.close()

    print()
    print("Realtime detection works here: steamos-utility-center --watch-achievements")
    print("To skip the search next time, put this in the config:")
    print("  STEAM_ROUTE=%s" % route)
    return 0


def run_probe_messages(config, seconds=None):
    """Find out whether Steam will forward friend messages to us, and how.

    Two unknowns: whether the borrowed library can deliver callbacks to a
    ctypes binding at all (manual dispatch is SDK 1.51+), and which callback
    number carries a chat message. So every callback that arrives is printed
    rather than only the expected one.
    """
    _interrupt_on_sigterm()

    # A configured library may sit outside the search, so survey it too.
    candidates = list(steamworks.find_libraries())
    explicit = config["STEAM_LIBRARY"]
    if explicit and explicit != "auto" and explicit not in candidates:
        candidates.insert(0, explicit)

    print("Libraries, and what each one offers for friend activity:")
    usable = []
    arrivals = []
    for path in candidates:
        support = steamworks.message_support(path)
        if support.get("error"):
            print("  [ ?  ] %s (%s)" % (path, support["error"]))
            continue
        ok = steamworks.usable_for_messages(support)
        if ok:
            usable.append(path)
        if steamworks.usable_for_friends(support):
            arrivals.append(path)
        print("  [%s] %s" % ("use " if ok else "skip", path))
        print("         manual dispatch: %-5s  listen: %-5s  read: %s"
              % (support["manual_dispatch"], support["listen"],
                 support["read_message"]))
        print("         friends coming online: %-5s  relationship: %s"
              % (steamworks.usable_for_friends(support),
                 support["relationship"]))
        print("         ISteamFriends via: %s"
              % (", ".join(support["accessors"]
                           + (["FindOrCreateUserInterface"]
                              if support["find_or_create"] else [])
                           + (["ISteamClient"] if support["via_client"]
                              else [])) or "nothing found"))
    print()
    if not usable:
        if arrivals:
            # The two are not the same test: chat needs the client's
            # permission on top, and this is exactly the machine where the
            # difference decides whether anything flashes at all.
            print("No library here can be told to forward chat, but %d can"
                  % len(arrivals))
            print("still report friends coming online. Leave")
            print("NOTIFY_FRIEND_ONLINE on and NOTIFY_MESSAGES off.")
            return 1
        print("No library here can deliver callbacks to us. Friend messages")
        print("cannot be read this way on this machine - which is worth")
        print("knowing before anything is built on top of it.")
        return 1

    app_id = steamworks.running_app_id()
    if not app_id:
        print("Now start a game and run this again: Steamworks has to be")
        print("initialised as a specific app, so there is nothing to attach")
        print("to while none is running.")
        return 1

    library = steamworks.find_library(config["STEAM_LIBRARY"])
    if not steamworks.usable_for_messages(steamworks.message_support(library)):
        print("The library that would be chosen (%s)" % library)
        print("cannot do this. Set STEAM_LIBRARY to one of the usable ones")
        print("above and run this again.")
        return 1

    route = config["STEAM_ROUTE"]
    if not route or route == "auto":
        print("Finding a way into Steamworks with this library:")
        route, _count = steamworks.select_route(app_id, library,
                                                reporter=_report_route)
        print(flush=True)
        if route is None:
            print("None of them worked, so there is no session to listen on.")
            print("The lines above say why each one was turned down - a")
            print("library that cannot reach ISteamUserStats may still be")
            print("fine for messages, which is worth knowing before giving up.")
            return 1

    stats = steamworks.UserStats(app_id, library, route=route,
                                 manual_dispatch=True)
    listener = None
    try:
        stats.open()
        listener = steamworks.FriendListener(stats)
        listener.open()
    except steamworks.SteamworksError as exc:
        print("Cannot listen: %s" % exc)
        stats.close()
        return 1

    print("Attached to app %d. Now have someone send you a Steam message." % app_id)
    print("Every callback that arrives is listed below, with its number and")
    print("payload size - the one that appears exactly when the message does")
    print("is the one worth acting on. Press Ctrl-C when done.")
    print(flush=True)

    counts = {}
    deadline = time.monotonic() + seconds if seconds else None
    try:
        while deadline is None or time.monotonic() < deadline:
            for number, payload in listener.callbacks():
                counts[number] = counts.get(number, 0) + 1
                print("%s  callback %-6d %3d bytes  %s"
                      % (time.strftime("%H:%M:%S"), number, len(payload),
                         payload[:24].hex() or "-"), flush=True)
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        listener.close()
        stats.close()

    print()
    if not counts:
        print("Nothing arrived at all. Either Steam does not forward chat to")
        print("this app, or no message came in while it was listening.")
        return 1
    print("Totals: %s" % ", ".join("callback %d x%d" % (number, count)
                                   for number, count in sorted(counts.items())))
    print("Report the number that lined up with the message.")
    return 0


# How often to scan every process while still searching for a game: the scan is
# expensive and there is no hurry to notice one.
PROCESS_SCAN_EVERY = 5          # ticks

# Told to watch for nothing at all. The watcher normally exits 0 after one
# game and is restarted, so this needs an exit systemd can tell apart or the
# unit respawns forever. RestartPreventExitStatus names it.
NOTHING_TO_WATCH_EXIT = 3


def _should_scan_processes(tick, attached):
    """Whether this tick should pay for the full process scan.

    Always yes while attached: on some machines registry.vdf never names the
    running app, so a skipped scan would read as "no game" and detach a game
    that is still running.
    """
    return attached or tick % PROCESS_SCAN_EVERY == 0


def _flash(fifo, kind):
    """Trigger the bar, and keep going if the service is not listening."""
    try:
        notify.send(fifo, kind)
    except OSError as exc:
        LOG.warning("could not flash the bar: %s", exc)


def _open_friend_listener(stats, want_messages):
    """Start listening for friend activity, or None if Steam will not have it."""
    try:
        listener = steamworks.FriendListener(stats, want_messages=want_messages)
        listener.open()
        return listener
    except steamworks.SteamworksError as exc:
        # Not fatal: achievements are the main event and still work.
        LOG.warning("friend activity unavailable: %s", exc)
        return None


def run_watch_achievements(config, interval=1.0):
    """Flash the bar on achievements and friend activity in the running game.

    Runs as your normal user next to Steam, not as the sandboxed service, and
    only writes trigger words into the notification pipe.
    """
    _interrupt_on_sigterm()

    achievements_on = config["NOTIFY_ACHIEVEMENTS"]
    messages_on = config["NOTIFY_MESSAGES"]
    friends_on = config["NOTIFY_FRIEND_ONLINE"]
    if not (achievements_on or messages_on or friends_on):
        # Attaching would open a Steamworks session as the running game for
        # nothing - and that registration is what keeps Steam on "Stopping".
        print("NOTIFY_ACHIEVEMENTS, NOTIFY_MESSAGES and NOTIFY_FRIEND_ONLINE "
              "are all off, so there is nothing to watch for.", flush=True)
        return NOTHING_TO_WATCH_EXIT

    fifo = config["NOTIFY_FIFO"]
    watcher = None
    listener = None
    current_app = None
    stats = None

    # flush, because this runs as a service: Python block-buffers a piped
    # stdout, so these lines would sit there until the process stopped and
    # then land in the journal describing a run that is already over.
    print("Watching for %s; flashes go to %s"
          % (" and ".join(filter(None, [
              "achievements" if achievements_on else "",
              "friend messages" if messages_on else "",
              "friends coming online" if friends_on else ""])), fifo),
          flush=True)
    print("Press Ctrl-C to stop.", flush=True)

    try:
        for tick in itertools.count():
            app_id = steamworks.running_app_id(
                scan_processes=_should_scan_processes(
                    tick, attached=current_app is not None))

            if app_id != current_app:
                if stats is not None:
                    if listener is not None:
                        listener.close()
                        listener = None
                    stats.close()
                    # And then go away entirely: this process is registered
                    # with Steam as an instance of the game, and only exiting
                    # clears that - SteamAPI_Shutdown does not. systemd starts
                    # the next watcher (Restart=always).
                    LOG.info("game ended - exiting so Steam can finish "
                             "stopping it; systemd restarts the watcher")
                    return 0
                current_app = app_id
                if app_id:
                    try:
                        library = steamworks.find_library(
                            config["STEAM_LIBRARY"])
                        # Friend activity needs a manual-dispatch session, and
                        # only a new enough library can open one. Chat needs
                        # more of it than "who came online" does: that one
                        # arrives without asking Steam to forward anything.
                        support = steamworks.message_support(library)
                        chat = messages_on and steamworks.usable_for_messages(
                            support)
                        comings = friends_on and steamworks.usable_for_friends(
                            support)
                        manual = chat or comings
                        if messages_on and not chat:
                            LOG.info("%s cannot deliver friend messages",
                                     library)
                        if friends_on and not comings:
                            LOG.info("%s cannot deliver friend state changes",
                                     library)
                        if not achievements_on and not manual:
                            # Decided before opening anything, because a
                            # session is not free: it registers this process
                            # as the game, and only the process ending clears
                            # that. Attaching to poll nothing would hold the
                            # game on "Stopping" for no flash at all.
                            print("Achievements are switched off and %s cannot "
                                  "deliver friend activity, so there is "
                                  "nothing to watch for." % library, flush=True)
                            return NOTHING_TO_WATCH_EXIT

                        route = config["STEAM_ROUTE"]
                        if not route or route == "auto":
                            # Probes in child processes; a bad route segfaults.
                            route, _count = steamworks.select_route(
                                app_id, library)
                            if route is None:
                                raise steamworks.SteamworksError(
                                    "no working route for app %d - run "
                                    "--steam-check for details" % app_id)
                            LOG.info("using route %s", route)
                        stats = steamworks.UserStats(app_id, library,
                                                     route=route,
                                                     manual_dispatch=manual)
                        stats.open()
                        watcher = (steamworks.AchievementWatcher(stats)
                                   if achievements_on else None)
                        listener = (_open_friend_listener(stats, chat)
                                    if manual else None)
                        if watcher is None and listener is None:
                            # The library could have done it, but Steam
                            # declined - and achievements are off, so this
                            # session has nothing left to report. Ending the
                            # process is also what releases the registration
                            # it just took out.
                            print("Steam will not forward friend activity to "
                                  "this app and achievements are switched "
                                  "off, so there is nothing to watch for.",
                                  flush=True)
                            return NOTHING_TO_WATCH_EXIT
                        LOG.info("attached to app %d", app_id)
                    except steamworks.SteamworksError as exc:
                        # current_app is set, so no retry until another game.
                        LOG.warning("cannot attach to app %s: %s", app_id, exc)
                        stats, watcher = None, None
                else:
                    LOG.info("no game running")

            if stats is not None:
                try:
                    if watcher is not None:
                        for name in watcher.poll():
                            LOG.info("achievement unlocked: %s",
                                     stats.display_name(name))
                            _flash(fifo, "achievement")
                    if listener is not None:
                        # One flash however many arrived, per kind: the queue
                        # drops a repeat of what it is already showing, so a
                        # burst would only be told once anyway.
                        messages, online = listener.poll()
                        if messages and messages_on:
                            LOG.info("%d friend message(s)", len(messages))
                            _flash(fifo, "message")
                        if online and friends_on:
                            # No names in the log: who you play with is
                            # nobody's business but yours.
                            LOG.info("%d friend(s) came online", len(online))
                            _flash(fifo, "friend")
                except OSError as exc:
                    LOG.warning("lost the Steamworks connection: %s", exc)
                    if listener is not None:
                        listener.close()
                    stats.close()
                    stats, watcher, listener = None, None, None
                    current_app = None

            time.sleep(interval)
    except KeyboardInterrupt:
        pass
    finally:
        if listener is not None:
            listener.close()
        if stats is not None:
            stats.close()
    return 0


MONITOR_MISSING_EXIT = 4


def run_watch_phone(config, print_only=False):
    """Flash the bar on your phone's notifications, by way of KDE Connect.

    Runs as your normal user next to the desktop, not as the sandboxed
    service: the notifications are on the session bus, which only the session
    can read. Like the achievement watcher, all it ever does to this project
    is write trigger words into the pipe.

    `print_only` is the thing to run first. It reports every notification it
    sees and what it would have flashed, and flashes nothing - so whether KDE
    Connect answers on this machine, and what the apps on it are actually
    called, are things you find out by looking rather than by guessing at a
    rule.
    """
    _interrupt_on_sigterm()

    if not print_only and not config["NOTIFY_PHONE"]:
        print("NOTIFY_PHONE is off, so there is nothing to watch for.",
              flush=True)
        return NOTHING_TO_WATCH_EXIT

    rules = phone.parse_rules(config["PHONE_APPS"])
    fifo = config["NOTIFY_FIFO"]

    # Ask for KDE Connect before listening to it. A monitor attaches to a name
    # rather than asking for it, so with nothing behind that name it waits in
    # silence - which is what Game Mode looks like, there being no desktop
    # session there to have started it.
    # Never started from a dry run: --print reports on the machine, and a
    # report that starts a daemon has changed the thing it was describing.
    woken = phone.wake_kdeconnect(revive=not print_only)

    def report(sighting, trigger):
        print("  %-40s -> %s" % (sighting.describe(), trigger or "ignored"),
              flush=True)
        if sighting.where and not (sighting.title or sighting.body):
            # The id was all the signal gave, and asking the object about it
            # added nothing. Said here rather than swallowed: it is the
            # difference between a rule that can name the app and one that
            # cannot, and the path is what anybody looking into it would need
            # next.
            print("     (no app name at %s)" % sighting.where, flush=True)

    bridge = phone.Bridge(
        rules, listed_only=config["PHONE_APPS_ONLY"],
        send=None if print_only else lambda trigger: notify.send(fifo, trigger),
        report=report if print_only else None,
        details=phone.look_up,
        # Nothing to settle in a dry run: it flashes nothing anyway, and its
        # job is to show what the bus said.
        settle=0.0 if print_only else phone.BACKLOG_SETTLE)

    # flush, because this runs as a service: Python block-buffers a piped
    # stdout, so these lines would sit in it until the process stopped.
    print("Reading the phone's notifications from KDE Connect", flush=True)
    # Said rather than left to be inferred from nothing ever flashing. These
    # are also the lines to look for in the journal after a spell in Game
    # Mode, where there is no terminal to watch it live.
    if woken is None:
        found = phone.kdeconnectd_path()
        print("  note: KDE Connect is not answering. It is what carries the "
              "phone's notifications over, and the desktop session it "
              "belongs to does not exist in Game Mode.", flush=True)
        print("        %s" % ("This would have started %s; --print does not."
                              % found if print_only and found
                              else "It is not installed on this machine."
                              if not found
                              else "Started it; it should answer shortly."),
              flush=True)
    elif woken == []:
        print("  note: KDE Connect is running but is not paired with any "
              "phone. Nothing will arrive until it is - pair them in KDE "
              "Connect's own settings, in Desktop Mode.", flush=True)
    elif woken:
        print("  KDE Connect is paired with: %s" % ", ".join(woken), flush=True)
    if rules:
        print("Apps with a look of their own: %s"
              % ", ".join(rule.app for rule in rules), flush=True)
    if config["PHONE_APPS_ONLY"]:
        print("Only those apps flash; everything else is ignored.", flush=True)
    print("Watching. %s" % ("Nothing will flash - this is --print."
                            if print_only else "Flashes go to %s" % fifo),
          flush=True)
    # Whatever would stop the real thing from lighting the bar, said here
    # rather than left to be discovered by it not happening.
    for complaint in phone.obstacles(config["NOTIFY"], config["NOTIFY_PHONE"],
                                     os.path.exists(fifo)):
        print("  note: %s" % complaint, flush=True)

    try:
        monitor = phone.open_monitor()
    except OSError as exc:
        # gdbus comes with glib, so this is a machine with no desktop stack at
        # all - which no amount of restarting will change.
        print("cannot start gdbus: %s" % exc, file=sys.stderr, flush=True)
        return MONITOR_MISSING_EXIT

    known = [woken]

    def look_again():
        """Every so often: is KDE Connect still there, and still paired?

        Because this process is built to outlive the session it started in.
        Checking once at startup says nothing about what happens twenty
        minutes later in Game Mode - and asking is also what starts KDE
        Connect again if it has gone away, the bus activating it exactly as
        it did the first time.
        """
        now = phone.wake_kdeconnect()
        if now == known[0]:
            return
        was, known[0] = known[0], now
        if was is None and now is not None and not print_only:
            # KDE Connect has come back, which means the phone is about to
            # reconnect - and the first thing it does is hand over everything
            # already on it. The same pile as at boot, and the same answer.
            bridge.expect_backlog()
        if now is None:
            # Whether there was anything to start it with, said here rather
            # than nowhere: without it, "cannot find kdeconnectd" and "started
            # it and it still will not answer" read identically - as silence.
            found = phone.kdeconnectd_path()
            LOG.warning("KDE Connect has stopped answering; %s",
                        "started %s, giving it until the next check" % found
                        if found else
                        "kdeconnectd is not in any of %s, so there is nothing "
                        "here to start it with"
                        % ", ".join(phone.KDECONNECTD_PLACES))
        elif not now:
            LOG.warning("KDE Connect is no longer paired with any phone")
        else:
            LOG.info("KDE Connect is paired with: %s", ", ".join(now))

    def how_soon():
        """Often while KDE Connect is missing, seldom while it is not.

        The minute that is right for looking in on something healthy is a
        long time to sit with the phone trying to reconnect - and checking
        every few seconds all day, to catch the few minutes a week when it
        matters, is the other way to get it wrong.
        """
        return (phone.EAGER_SECONDS if known[0] is None
                else phone.TICK_SECONDS)

    try:
        bridge.watch(monitor.stdout, look_again, how_soon)
    except KeyboardInterrupt:
        pass
    finally:
        monitor.terminate()
        try:
            monitor.wait(timeout=5)
        except subprocess.TimeoutExpired:               # pragma: no cover
            monitor.kill()
    # Getting here means the monitor ended by itself - the bus went away with
    # the session, most likely. systemd starts the next one.
    LOG.info("the notification bus stopped talking (%d seen, %d flashed)",
             bridge.seen, bridge.flashed)
    return 0


def run_notify(config, kind):
    """Fire a notification on a running service."""
    try:
        notify.send(config["NOTIFY_FIFO"], kind)
    except OSError as exc:
        LOG.error("%s", exc)
        return 1
    print("sent %r to %s" % (kind, config["NOTIFY_FIFO"]))
    return 0


def run_list_ports():
    ports = serialport.list_ports()
    if not ports:
        print("no USB serial devices found")
        return 1
    for port in ports:
        ids = "%04x:%04x" % (port["vid"], port["pid"]) if port["vid"] else "-"
        print("%-16s %-12s %s" % (port["device"], ids, port["description"]))
        if port["by_id"]:
            print("%-16s %-12s %s" % ("", "", port["by_id"]))
    return 0


# How long to wait for a picture before giving up on registering.
#
# Short on purpose, and it took a second look to see why. The unit holds the
# toolkit's wake service behind it, and the case where there is no picture is
# usually a television that is *off* - which is the very thing the wake was
# going to fix. Waiting there is not thoroughness: it is standing in the way
# of the fix for half a minute and then giving up anyway. So this covers only
# the honest case, a link that is still coming up while the session starts,
# and gets out of the way otherwise.
CEC_LINK_WAIT = 10.0
CEC_LINK_POLL = 1.0


def _read_toolkit_settings(read=None):
    """The CEC toolkit's own settings, or {} where it has none to read."""
    read = read or _read_text
    return cec_module.read_settings(
        read(path) for path in cec_module.settings_paths())


def _read_text(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return ""


def _tell_the_toolkit_where_it_is(device, address, settings, run):
    """Write the physical address into the toolkit's settings, if it has none.

    The other half of the same evening's bug. Knowing the address is what lets
    the wake paths broadcast <Active Source>, which is the message that
    switches the television over - and they all skip it when the setting is
    empty. It ships empty and only `discover-cec` ever fills it in, which the
    toolkit's installer never runs.

    This already has the address in its hand: it read it to decide whether the
    adapter was worth registering. So it is written through the toolkit's own
    set-config, which needs no root and lands in the user file that shadows
    /etc - the same place pressing Discover would put it.

    Only for the adapter the toolkit is set to use, and only when there is no
    value there: a machine with two adapters must not be told about the other
    one, and a value somebody chose is theirs.
    """
    if device != cec_module.configured_device(settings):
        return False
    if not cec_module.wants_physical_address(settings):
        return False
    if not os.path.exists(cec_module.command_path()):
        return False                    # no toolkit, nothing to tell
    answer = run(cec_module.set_config_command(
        {cec_module.PHYSICAL_ADDRESS: address}))
    if getattr(answer, "returncode", 1) != 0:
        print("could not tell the toolkit it is at %s: %s"
              % (address, (getattr(answer, "stdout", "") or "").strip()),
              flush=True)
        return False
    print("Told the CEC toolkit this machine is at %s, so waking can switch "
          "the input over as well." % address, flush=True)
    return True


def run_register_cec(devices=None, run=None, sleep=None, now=None,
                     wait=CEC_LINK_WAIT, settings=None):
    """Claim a logical address for any CEC adapter that has none.

    The step the toolkit assumes has already happened - see the long comment
    in cec.py. Deliberately timid: an adapter that already holds an address is
    left alone, one with no picture is waited for and then left alone, and
    anything cec-ctl says that this does not understand is left alone too.
    `wait` is the whole budget: the adapter appearing and then its picture
    share it, so the toolkit's wake service is never held up for longer than
    that however the session start goes.
    Doing nothing is always a safe answer here; taking the bus off Steam's own
    daemon would not be.

    Everything it uses is injectable because the machine this is tested on has
    no CEC adapter, no television and no cec-ctl.
    """
    run = run or (lambda command: subprocess.run(
        command, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, timeout=15, check=False))
    sleep = sleep or time.sleep
    now = now or time.monotonic
    if settings is None:
        settings = _read_toolkit_settings()
    told = False

    # The device node first, and waited for rather than looked for once.
    #
    # A session start races the adapter's own enumeration, and losing that
    # race silently is how a machine ends up with an adapter nobody ever
    # registers - which is the state an unplug and replug is famous for
    # curing, because the second time round the device appears while
    # everything that cares is already running. Waiting here is honest
    # waiting: an adapter that is coming will come, unlike a picture, which
    # needs somebody to turn the television on.
    deadline = now() + wait
    while devices is None:
        found = sorted(glob.glob(cec_module.ADAPTERS))
        if found or now() >= deadline:
            devices = found
            break
        sleep(CEC_LINK_POLL)

    if not devices:
        print("No CEC adapter on this machine; nothing to register.",
              flush=True)
        return 0

    waiting = list(devices)
    done = 0
    while waiting:
        for device in list(waiting):
            answer = run(cec_module.adapter_state_command(device))
            text = getattr(answer, "stdout", "") or ""
            if getattr(answer, "returncode", 1) != 0:
                print("%s: cec-ctl would not report on it: %s"
                      % (device, text.strip()), flush=True)
                waiting.remove(device)
                continue
            registered = cec_module.adapter_registered(text)
            if registered is None:
                print("%s: cannot tell whether it is on the bus, so leaving "
                      "it alone." % device, flush=True)
                waiting.remove(device)
                continue
            address = cec_module.adapter_physical_address(text)
            if address and not told:
                told = _tell_the_toolkit_where_it_is(
                    device, address, settings, run)
            if registered:
                print("%s: already on the bus; leaving it alone." % device,
                      flush=True)
                waiting.remove(device)
                continue
            if not address:
                continue                # no picture yet - come back to it
            waiting.remove(device)
            result = run(cec_module.register_command(device))
            if getattr(result, "returncode", 1) != 0:
                said = (getattr(result, "stdout", "") or "").strip()
                print("%s: could not claim a logical address: %s"
                      % (device, said), flush=True)
                continue
            done += 1
            print("%s: registered at %s as a playback device."
                  % (device, address), flush=True)
        if waiting and now() >= deadline:
            for device in waiting:
                print("%s: no picture after %gs, so there is nothing to "
                      "register against." % (device, wait), flush=True)
            break
        if waiting:
            sleep(CEC_LINK_POLL)
    if not done and not told:
        # Not a failure: an adapter somebody else already has is the good
        # case, and the unit exiting non-zero for it would paint the session
        # red for doing the right thing.
        print("Nothing needed registering.", flush=True)
    return 0


def dump_line(snapshot, written, seen):
    """One state change, with how long since Steam's previous one.

    The gap is measured between the module's own stamps rather than between
    the moments this noticed them: the module stamps every write, and this
    loop can be a moment behind one. So the column answers "how often does
    Steam write?" - which is the question a download raises, and one nothing
    in this project can answer from the outside.

    And it says when the answer is short of writes. The shim hands out the
    current state rather than a queue, so two writes inside one wait are seen
    as one - the counter is what gives that away, and a gap measured across a
    write nobody saw is not the gap anybody wanted.
    """
    gap = ("" if written is None
           else "+%.2fs" % ((snapshot.monotonic_ns - written) / 1e9))
    missed = ("" if seen is None or snapshot.seq <= seen + 1
              else "  (%d write(s) not seen)" % (snapshot.seq - seen - 1))
    return "%8s %s%s" % (gap, snapshot, missed)


def run_dump(config):
    """Print decoded snapshots without touching the serial port."""
    _interrupt_on_sigterm()
    with shim.ShimSource(config["DEVICE"]) as source:
        last = written = seen = None
        print("watching %s, press Ctrl-C to stop" % config["DEVICE"])
        while True:
            snapshot = source.read()
            if snapshot is not None and snapshot.key() != last:
                last = snapshot.key()
                print(dump_line(snapshot, written, seen), flush=True)
                written, seen = snapshot.monotonic_ns, snapshot.seq
            source.wait(1.0)


def run_self_test(config, duration=None):
    """Drive test patterns without Steam or the kernel module."""
    _interrupt_on_sigterm()
    renderer = build_renderer(config)
    link = build_link(config)

    stages = [
        ("red", shim.make_snapshot(shim.EFFECT_MANUAL, (255, 0, 0)), 2.0),
        ("green", shim.make_snapshot(shim.EFFECT_MANUAL, (0, 255, 0)), 2.0),
        ("blue", shim.make_snapshot(shim.EFFECT_MANUAL, (0, 0, 255)), 2.0),
        ("white", shim.make_snapshot(shim.EFFECT_MANUAL, (255, 255, 255)), 2.0),
        ("patrol", shim.make_snapshot(shim.EFFECT_PATROL, (255, 40, 0)), 5.0),
        ("breath", shim.make_snapshot(shim.EFFECT_BREATH, (0, 120, 255)), 5.0),
        ("rainbow", shim.make_snapshot(shim.EFFECT_RAINBOW), 6.0),
    ]
    if duration:
        scale = duration / sum(stage[2] for stage in stages)
        stages = [(name, snap, length * scale) for name, snap, length in stages]

    deadline = time.monotonic() + 20.0
    while not link.connect():
        if time.monotonic() > deadline:
            LOG.error("no ESP found on %s", config["SERIAL_PORT"])
            return 1
        time.sleep(0.5)

    print("If the colours below do not match what you see, check COLOR_ORDER "
          "in the firmware build flags.")
    started = time.monotonic()
    try:
        for name, snapshot, length in stages:
            print("  -> %s" % name)
            stage_end = time.monotonic() + length
            while time.monotonic() < stage_end:
                payload = renderer.render(snapshot, time.monotonic() - started)
                link.send_frame(payload, config["LED_COUNT"])
                link.poll()
                time.sleep(1.0 / config["FPS"])
    except KeyboardInterrupt:
        pass
    finally:
        link.shutdown()
    print("self test finished")
    return 0


def run_simulate(config, effect_name):
    """Feed a synthetic snapshot to the ESP, as if Steam had set it."""
    effects = {name: value for value, name in shim.EFFECT_NAMES.items()}
    if effect_name not in effects:
        LOG.error("unknown effect %r (known: %s)",
                  effect_name, ", ".join(sorted(effects)))
        return 2

    _interrupt_on_sigterm()
    snapshot = shim.make_snapshot(effects[effect_name], (255, 60, 0))
    renderer = build_renderer(config)
    link = build_link(config)
    started = time.monotonic()
    print("simulating effect %r, press Ctrl-C to stop" % effect_name)
    try:
        while True:
            if link.connect():
                payload = renderer.render(snapshot, time.monotonic() - started)
                link.send_frame(payload, config["LED_COUNT"])
                link.poll()
            time.sleep(1.0 / config["FPS"])
    except KeyboardInterrupt:
        pass
    finally:
        link.shutdown()
    return 0


# -- CLI ------------------------------------------------------------------


def build_parser():
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description="Mirror the SteamOS LED bar onto a WS2812 strip attached "
                    "to an ESP over USB serial.")
    parser.add_argument("--config", default=config_module.DEFAULT_CONFIG_PATH,
                        help="config file (default: %(default)s)")
    parser.add_argument("--device", help="shim character device")
    parser.add_argument("--serial-port", dest="serial_port",
                        help="serial device or 'auto'")
    parser.add_argument("--baud", type=int, help="serial baud rate")
    parser.add_argument("--leds", dest="led_count", type=int,
                        help="number of LEDs on the physical strip")
    parser.add_argument("--mapping", choices=render.MAPPINGS,
                        help="how 17 logical LEDs map onto the strip")
    parser.add_argument("--reverse", action="store_true", default=None,
                        help="flip the strip direction")
    parser.add_argument("--max-brightness", dest="max_brightness", type=int,
                        help="clamp overall brightness (0-255)")
    parser.add_argument("--min-brightness", dest="min_brightness", type=int,
                        help="raise the brightness floor (0-255)")
    parser.add_argument("--gamma", type=float, help="gamma correction, 1.0 = off")
    parser.add_argument("--speed", type=float, help="animation speed multiplier")
    parser.add_argument("--patrol-dots", dest="patrol_dots", type=int,
                        help="dots the patrol effect chases (default 1)")
    parser.add_argument("--notify-fifo", dest="notify_fifo", metavar="PATH",
                        help="named pipe that triggers notifications")
    parser.add_argument("--steam-library", dest="steam_library", metavar="PATH",
                        help="path to libsteam_api.so, or 'auto' to search")
    parser.add_argument("--fps", type=int, help="frame rate for animated effects")
    parser.add_argument("--idle-fps", dest="idle_fps", type=int,
                        help="heartbeat rate for static scenes")
    parser.add_argument("--log-level", dest="log_level",
                        choices=("debug", "info", "warning", "error"))
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="shorthand for --log-level debug")
    # Not a mode of its own: it changes what --watch-phone does with what it
    # finds, which is why it sits with the options rather than below.
    parser.add_argument("--print", action="store_true", dest="print_only",
                        help="with --watch-phone: report what it sees and "
                             "what it would flash, without flashing. Run this "
                             "first, to see what the apps are called here")

    modes = parser.add_argument_group("modes")
    modes.add_argument("--list-ports", action="store_true",
                       help="list USB serial devices and exit")
    modes.add_argument("--dump", action="store_true",
                       help="print decoded snapshots instead of driving LEDs")
    modes.add_argument("--self-test", nargs="?", const=0.0, type=float,
                       metavar="SECONDS", dest="self_test",
                       help="run test patterns without Steam or the kernel module")
    modes.add_argument("--notify", metavar="KIND",
                       help="flash the bar on a running service: achievement, "
                            "message, friend, warning or a colour like "
                            "'#00ff88'. Prefix a shape to try one out without "
                            "configuring it: 'comet:#1a9fff'")
    modes.add_argument("--watch-achievements", action="store_true",
                       dest="watch_achievements",
                       help="flash on every achievement unlocked in the running "
                            "game (run as your normal user, not with sudo)")
    modes.add_argument("--watch-phone", action="store_true",
                       dest="watch_phone",
                       help="flash on your phone's notifications, which KDE "
                            "Connect brings to the desktop (run as your normal "
                            "user, not with sudo)")
    modes.add_argument("--register-cec", action="store_true",
                       dest="register_cec",
                       help="claim a logical address for any CEC adapter that "
                            "has none, so the toolkit's wake commands have an "
                            "address to send from (run as your normal user)")
    modes.add_argument("--check-config", action="store_true",
                       dest="check_config",
                       help="load and validate the configuration and exit; "
                            "prints the effective settings")
    modes.add_argument("--desktop", action="store_true",
                       help="report the Desktop Mode scene and who has the bar "
                            "right now - run it in both modes to check that "
                            "this machine's Game Mode is recognised")
    modes.add_argument("--temperature", action="store_true",
                       help="list the machine's temperature sensors and show "
                            "what the gauge would display right now")
    modes.add_argument("--load", action="store_true",
                       help="show which CPU and GPU load counters this machine "
                            "has, and what the load gauge would display")
    modes.add_argument("--steam-check", action="store_true", dest="steam_check",
                       help="report whether realtime achievement detection can "
                            "work on this machine")
    modes.add_argument("--probe-messages", nargs="?", const=0.0, type=float,
                       metavar="SECONDS", dest="probe_messages",
                       help="with a game running, report every Steamworks "
                            "callback so the one carrying a friend message "
                            "can be identified")
    modes.add_argument("--simulate", metavar="EFFECT",
                       help="render one effect continuously (off, manual, normal, "
                            "rainbow, breath, patrol, factory, demo)")
    return parser


def configure_logging(level):
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def main(argv=None):
    args = build_parser().parse_args(argv)

    if args.list_ports:
        configure_logging("warning")
        return run_list_ports()

    # Before the configuration is loaded, like --list-ports: which television
    # this machine is plugged into has nothing to do with the LED bar's
    # settings, and failing for want of a config file it does not read would
    # be a puzzle at session start.
    if args.register_cec:
        configure_logging("warning")
        return run_register_cec()

    overrides = {
        "DEVICE": args.device,
        "SERIAL_PORT": args.serial_port,
        "BAUD": args.baud,
        "LED_COUNT": args.led_count,
        "MAPPING": args.mapping,
        "REVERSE": args.reverse,
        "MAX_BRIGHTNESS": args.max_brightness,
        "MIN_BRIGHTNESS": args.min_brightness,
        "GAMMA": args.gamma,
        "SPEED": args.speed,
        "PATROL_DOTS": args.patrol_dots,
        "NOTIFY_FIFO": args.notify_fifo,
        "STEAM_LIBRARY": args.steam_library,
        "FPS": args.fps,
        "IDLE_FPS": args.idle_fps,
        "LOG_LEVEL": "debug" if args.verbose else args.log_level,
    }

    try:
        config = config_module.load(args.config, overrides)
    except (config_module.ConfigError, OSError) as exc:
        print("%s: %s" % (PROGRAM, exc), file=sys.stderr)
        return CONFIG_REFUSED_EXIT

    configure_logging(config["LOG_LEVEL"])
    LOG.debug("configuration: %s", config)

    try:
        if args.dump:
            return run_dump(config)
        if args.self_test is not None:
            return run_self_test(config, args.self_test or None)
        if args.notify:
            return run_notify(config, args.notify)
        if args.check_config:
            return run_check_config(config)
        if args.desktop:
            return run_desktop(config)
        if args.temperature:
            return run_temperature(config)
        if args.load:
            return run_load(config)
        if args.steam_check:
            return run_steam_check(config)
        if args.probe_messages is not None:
            return run_probe_messages(config, args.probe_messages or None)
        if args.watch_achievements:
            return run_watch_achievements(config)
        if args.watch_phone:
            return run_watch_phone(config, print_only=args.print_only)
        if args.simulate:
            return run_simulate(config, args.simulate.lower())
        return Runner(config).run()
    except KeyboardInterrupt:
        return 0
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            LOG.error("%s", exc)
            return 1
        raise


if __name__ == "__main__":
    sys.exit(main())
