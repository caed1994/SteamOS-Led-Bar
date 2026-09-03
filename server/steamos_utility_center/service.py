# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""The entry point of the service: shim, renderer, USB serial, ESP."""

from __future__ import annotations

import argparse
import errno
import itertools
import logging
import os
import select
import signal
import subprocess
import sys
import time

from . import config as config_module
from . import desktop, elf, load, notify, phone, render, serialport, shim
from . import steamworks
from . import temperature
from .link import EspLink

LOG = logging.getLogger("steamos-utility-center")

PROGRAM = "steamos-utility-center"
DEVICE_RETRY_DELAY = 5.0

# The exit code for a configuration that the service refuses. A restart cannot
# change a file, so the unit names this code in RestartPreventExitStatus.
#
# Without that, the service retries at each RestartSec, and the one line with
# the reason is lost in the log.
CONFIG_REFUSED_EXIT = 2

# What the strip shows while the machine is in suspend. It is fixed, and that
# is deliberate. It is not a notification and not an effect. It is the
# appearance of "the machine is off and alive", and it must look the same on
# each machine.
#
# It is here and not in the firmware. The ESP must draw it, because during a
# suspend there is no host to render anything. But a change to its appearance
# must not need a new flash of each board.
#
# It is deliberately dim: a dark room at night, and not a night light. With the
# 5% minimum of the breath, this covers approximately 2 to 30 of 255.
#
# It is not lower. A WS2812 has large steps at the low end, and white there
# takes a colour.
STANDBY_COLOR = (30, 30, 30)
STANDBY_PERIOD_MS = 6000            # one slow breath, calmer than the waiting one

# Steam did not write to the LEDs after the load of the module. See shim. A
# correct render of that state is a black strip, and the start-up breath of the
# ESP stops at the handshake for it.
UNTOUCHED_SEQ = shim.UNTOUCHED_SEQ

# The bar thus continues the breath, in the amber colour and at the rate of the
# firmware.
#
# This module sends it and does not leave it to the firmware, because the link
# is already open. A later connection resets the board at the moment when Steam
# takes the bar.
STARTUP_COLOR = (40, 16, 0)
STARTUP_PERIOD_MS = 3000

# The words on the trigger pipe that are not flashes. The main loop already
# reads the pipe, and each user can write to it. The sleep hook thus has a
# place to report this, and this project needs no second channel.
STANDBY_WORD = "standby"
RESUME_WORD = "resume"

# The maximum length of the standby state while this process runs.
#
# The clock is the important part. time.monotonic() does not advance during a
# suspend, so this counts only the seconds in which the machine was awake.
#
# If the resume hook does not run, the bar thus repairs itself in 30 seconds. A
# machine in suspend for three days still returns to a breathing strip.
STANDBY_MAX_AWAKE = 30.0


class _Stopped(Exception):
    """A signal asked this process to stop."""


def anything_shows(config, shows):
    """Returns whether either mode puts `shows` on the bar on this machine.

    Two settings can ask for the same effect, and they are independent.
    RAINBOW_SHOWS selects the effect in the rainbow slot of Steam in Game Mode.
    DESKTOP_SCENE names an effect directly on the desktop, where there is no
    menu to share.

    A gauge is thus necessary here if *either* setting asks for it. The sources
    below are what a gauge reads.

    A user reported the defect. With DESKTOP_SCENE=load and RAINBOW_SHOWS at
    the rainbow, nothing built the counters. The load gauge thus had nothing to
    read, and render._substitute gave the slot back to the rainbow of Steam.

    A selection of the load gauge on the desktop thus showed a rainbow, which
    is the one effect that it replaced. The temperature gauge had the same
    defect, from the same two lines.

    Only the two effects that read hardware need this question. Fire and the
    aurora are arithmetic, and they draw wherever a setting names them.
    """
    return (config["RAINBOW_SHOWS"] == shows
            or desktop.scene_shows(config["DESKTOP_SCENE"]) == shows)


def shown_where(config, shows, name):
    """Returns two lines about which mode puts `name` on the bar here.

    It always returns both lines. The two reports said "the rainbow slot shows
    %r, set RAINBOW_SHOWS=load to put this there".

    That was correct and it was half of the answer. It names the Game Mode
    setting to a person who asks because the *desktop* shows the wrong effect.
    That person then puts the gauge in the one mode that they did not look at.
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
    """Returns a sensor to read, or None if no mode shows it here."""
    if not anything_shows(config, render.SHOWS_TEMPERATURE):
        return None
    return temperature.TemperatureSource(path=config["TEMPERATURE_SENSOR"])


def build_load_source(config):
    """Returns counters to read, or None if no mode shows them here."""
    if not anything_shows(config, render.SHOWS_LOAD):
        return None
    return load.LoadSource()


def build_overheat_watch(config):
    """Returns a watch over each sensor, or None when the warnings are off.

    This is separate from the gauge. The gauge shows one sensor that a person
    selected. This reads each sensor. Each of the two operates with the other
    one off.
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
        # This module parses the colour and the renderer does not. notify owns
        # the form of a colour, and notify imports from render. render thus
        # cannot ask notify.
        load_cpu_colour=notify.parse_color(config["LOAD_CPU_COLOR"]),
        load_gpu_colour=notify.parse_color(config["LOAD_GPU_COLOR"]),
        load_swap=config["LOAD_SWAP"],
    )


def build_scene(config):
    """Returns the snapshot for Desktop Mode, or None to leave the bar."""
    return desktop.scene_snapshot(config["DESKTOP_SCENE"],
                                  config["DESKTOP_COLOR"],
                                  config["DESKTOP_BRIGHTNESS"],
                                  config["DESKTOP_SPEED"])


def warn_scene_split(config):
    """Reports one time when a configuration file had a different meaning.

    The rainbow scene of Desktop Mode was the same slot as the slot of Game
    Mode. DESKTOP_SCENE=rainbow with RAINBOW_SHOWS=fire thus put fire on the
    desktop.

    The desktop now has a fire scene of its own. That pair of settings thus
    means the rainbow, which is what it says. It is not what the bar did
    before.

    This function does not migrate the file. Both readings of that file are
    correct, and only the person who wrote it knows which one they meant. A
    setting that changes itself is worse than a setting that reports its
    meaning.

    It writes a line to the log and does not refuse the file, for the same
    reason: nothing here is defective.
    """
    if (config["DESKTOP_SCENE"] != desktop.SCENE_RAINBOW
            or config["RAINBOW_SHOWS"] == render.SHOWS_RAINBOW):
        return
    LOG.info("DESKTOP_SCENE=rainbow now means Steam's rainbow, not the %s "
             "the rainbow slot holds in Game Mode; set DESKTOP_SCENE=%s for "
             "that on the desktop", config["RAINBOW_SHOWS"],
             config["RAINBOW_SHOWS"])


def notification_colors(config):
    """Returns the named triggers whose colour the configuration changes."""
    return {kind: notify.parse_color(config[prefix + "_COLOR"])
            for kind, prefix in config_module.CONFIGURABLE_KINDS}


def notification_styles(config):
    """Returns the triggers that flash in a shape of their own.

    Each trigger that this omits uses NOTIFY_STYLE, and that is the meaning of
    the default. The general setting thus stays the one control for "each
    trigger looks like this". A kind leaves it only when a person says so.
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
        # What to show while Steam does not drive the bar, and what decides
        # whether Steam drives it. Both are None when DESKTOP_SCENE says to
        # leave the bar to Steam. The normal path of the loop is thus
        # unchanged.
        self.scene = build_scene(config)
        # And, for a scene from the slot of the renderer, which effect this
        # one is. In Game Mode a setting on the renderer answers. On the
        # desktop the scene answers, so the answer travels with the snapshot.
        # See _shows.
        self.scene_shows = desktop.scene_shows(config["DESKTOP_SCENE"])
        warn_scene_split(config)
        self.ownership = None if self.scene is None else desktop.Ownership()
        self.trigger = None
        self.source = None
        # This is set while the machine goes into suspend. The ESP breathes by
        # itself, and the loop must stay quiet. Without that, the next frame
        # ends the standby one millisecond after its start.
        self.standby_since = None
        # Whether this told the ESP to breathe until Steam writes.
        self._breathing_for_steam = False

    def stop(self, *_args):
        self.running = False

    def install_signal_handlers(self):
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, self.stop)

    # -- shim device ------------------------------------------------------

    def _open_source(self):
        """Opens the shim device. It waits for the device if it is not there."""
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
        """Waits, then opens the device again after its answers stopped.

        The wait is the important part. Each fault of this device leaves it
        readable, so poll() returns immediately.

        Without the wait, the loop thus opens and reads again at the speed of the
        CPU. That uses one core fully and writes one warning at each turn. With the
        wait, a wrong DEVICE gives one message in some seconds.
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
            # This is not a failure. With no pipe, the bar does not flash.
            LOG.warning("notifications disabled, cannot use %s: %s",
                        self.config["NOTIFY_FIFO"], exc)
            return
        self.trigger = trigger

    def _wait(self, interval):
        """Waits for a change of the LED state, a trigger, or the timeout.

        It returns (state changed, trigger ready). It waits for both at the same
        time. A notification thus does not stay in the pipe while the bar is idle.
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
        """Gives the strip to the ESP while Steam writes nothing.

        After the message, the ESP breathes until the next frame. This method
        thus sends the message again only after an interruption. A notification
        flash is such an interruption.
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
        """Returns the snapshot to draw: the snapshot of Steam, or the scene.

        It returns the snapshot of Steam whenever Steam has a claim on the bar,
        and that is the safe direction.

        A scene that held the bar through a Game Mode session ignores the LED
        settings that a person set a moment before. A scene that gives the bar up
        too easily is only a scene that a person does not see.
        """
        if self.scene is None or self.ownership.steam_has_it(snapshot, now):
            return snapshot
        return self.scene

    def _shows(self, showing):
        """Returns what the next frame puts in the slot of the rainbow.

        It returns None whenever Steam has the bar. The renderer then uses
        RAINBOW_SHOWS. That setting belongs to the menu of Steam, and a person
        selected a Game Mode rainbow from that menu.
        """
        return None if showing is not self.scene else self.scene_shows

    def _loop(self):
        started = time.monotonic()
        snapshot = None
        showing = None
        last_key = None
        # The earliest time of the next frame.
        #
        # FPS is a limit, and it was only a timeout. The loop wakes at each write
        # to the device, so the frame rate was the write rate of Steam and not the
        # rate in the configuration.
        #
        # The measurement on a Steam Machine: during a download, Steam writes the
        # progress bar four hundred times each second. This service rendered each
        # write and sent it down a link that carries approximately sixty.
        #
        # This service still reads the state at each write. The limit applies to
        # the render and to the send only.
        due = 0.0
        # Whether this holds a frame. That is the one case in which the wait
        # below must be shorter.
        #
        # Without this value, the loop woke at the frame rate while nothing
        # changed. The idle frames, which are the purpose of IDLE_FPS, thus
        # stopped.
        pending = False

        while self.running:
            connected = self.link.connect()
            self.link.poll()

            interval = 1.0 / self.config["FPS"]
            # The renderer decides this and the snapshot does not. The
            # temperature gauge is in the slot of the rainbow, and Steam still
            # calls that effect animated.
            #
            # This asks about the effect on the bar and not about the last write
            # of Steam. The two are different while a scene is on the bar.
            if (showing is not None
                    and not self.renderer.is_animated(showing,
                                                      self._shows(showing))
                    and not self.overlay.active):
                interval = 1.0 / self.config["IDLE_FPS"]
            # Never wait past the time of the next frame. Without this, the wait
            # runs to the idle interval, and the frame rate is half of the
            # correct rate whenever Steam writes faster than the frames go
            # out.
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
                # This costs little at most turns: it reads nothing until
                # its own interval ends. It thus needs no guard here.
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
                    # The device is readable and empty. The shim answers each
                    # read with a full snapshot or with an error. This is thus
                    # a different device, and it makes the loop turn with no
                    # message.
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
                # Silence is correct here. The ESP breathes by itself, and
                # one frame from this loop ends the breath. The machine goes
                # into suspend, so this loop also stops.
                if now - self.standby_since > STANDBY_MAX_AWAKE:
                    # 30 seconds of run time means that the machine did not go
                    # into suspend, or that it returned with no message. Take
                    # the strip back. Do not leave it breathing at an awake
                    # machine.
                    self._leave_standby("still awake")
                else:
                    continue

            # Read each write, and draw at the rate in the configuration. The
            # skip is here and not earlier, so that the trigger pipe and the
            # temperature watch above answer at the rate of the loop.
            if now < due:
                pending = True
                continue
            pending = False

            showing = self._showing(snapshot, now)

            # A flash covers the full bar. A frame below it is discarded.
            payload = self.overlay.frame(now)
            if (payload is None and showing is snapshot
                    and snapshot.seq <= UNTOUCHED_SEQ):
                # There is nothing to show, and black is not better than the
                # breath that the ESP runs.
                #
                # A flash still reaches the bar: it is the branch above. It
                # returns to this branch, and this branch asks for the breath
                # again.
                #
                # A scene is something to show. It thus appears on a machine
                # with no Game Mode session after the boot, and it does not
                # wait for a session that can never start.
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
            # The idle frames also: the firmware makes the strip dark when
            # this service is quiet.
            self.link.send_frame(payload, self.config["LED_COUNT"])
            # This uses FPS and not the interval above. That interval is the
            # wait before this sends an unchanged frame again. This is the
            # maximum rate of a frame that changes.
            #
            # The progress bar of a download is a static effect that changes
            # at each write. At the idle rate it thus moves in large steps.
            due = now + 1.0 / self.config["FPS"]


# -- alternative modes ----------------------------------------------------


def _interrupt_on_sigterm():
    """Let the interactive modes run their cleanup when systemd stops them."""
    def handler(_signum, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, handler)


def run_desktop(config):
    """Reports the desktop scene and the owner of the bar. A desktop command.

    That is not a limit to remove. It is the shape of the question. Game Mode
    has no terminal, so a person can run this on one side of the question only.
    What it reads live is thus always the desktop half.

    The other half is what the service *recorded* while nobody could watch. The
    service writes the mode to the log at its start and at each change. The
    journal is thus where a person reads a Game Mode session afterwards.

    This function reads the journal. It does not print a journalctl line for a
    person to type. The answer to "did this machine ever recognise Game Mode"
    must not be two steps away.

    The important failure is a scene that stays on the bar through a game and
    ignores each write of Steam, with nothing on the screen to give the reason.
    This function makes that failure visible.
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

    # The rule of the service, from the two answers above. This does not
    # ask an Ownership: that class reads /proc a second time and writes its
    # own version of this into the middle of the report.
    steams = bool(found) or (ago is not None and ago < desktop.GRACE_SECONDS)
    print("So the bar is %s."
          % ("Steam's" if scene is None or steams
             else "the desktop's, showing your scene"))
    if scene is None:
        return 0

    # And the Game Mode half, which nobody can watch while it occurs.
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
            # This is the purpose of the report. Each line says "desktop" and
            # no line says Game Mode, on a machine with a Game Mode session.
            # That is a detection that fails.
            #
            # Without this report, the symptom is a bar that ignores Steam
            # during a game and reports nothing.
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
    """Prints one route on each line, as select_route() tries them."""
    print("  [%s] %-52s %s" % (ROUTE_MARKS[status], route, detail), flush=True)


def run_check_config(config):
    """Reports the settings of this configuration.

    A call to this function means that the file parsed and passed validate(),
    because main() loads the file before it dispatches.

    This is thus also the answer to "does the service accept this file". A
    program that replaces a working configuration needs that answer first.
    """
    for key in sorted(config):
        print("%-18s %s" % (key, config[key]))
    return 0


def run_temperature(config):
    """Lists the temperature sensors and reports what the gauge does.

    The correct sensor is different on each machine. This function thus lists
    each sensor with its reading and marks the selected one. That is what a
    person needs to write a TEMPERATURE_SENSOR line.
    """
    sensors = temperature.find_sensors()
    if not sensors:
        print("This machine reports no temperature sensors at all under %s."
              % temperature.HWMON_ROOT)
        print("The gauge cannot work here; the rainbow is shown instead.")
        return 1

    chosen = temperature.pick_sensor(sensors)
    # What the temperature warning does here, whether or not it is on. The
    # thresholds come from the machine, so this is the one place where a
    # person can see them before the decision.
    watch = temperature.OverheatWatch()
    watched = {sensor["path"]: threshold for sensor, threshold in watch.resolve()}

    print("Temperature sensors on this machine:")
    # Nine spaces, which is the width of "  [use ] " on the rows below.
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
            # The statement of the driver. The warning does not use it: a
            # flag that stays set warns about a temperature from one hour
            # before. It is still worth a report during a search for a
            # fault.
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
    """Reports what the load gauge reads here, and what it draws.

    The driver decides whether the GPU half operates. amdgpu answers and most
    other drivers do not.

    This function thus names the counters that it found. Without it, a person
    asks why one half of the bar is a copy of the other.
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

    # One interval, so that the CPU has two readings to subtract. The first
    # reading is always a baseline.
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
    """Reports what the Steamworks path finds and does not find here."""
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
        # The route into ISteamUserStats is the value that is most different
        # between SDK generations, so this reports it first.
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
    """Reports whether Steam forwards a friend message here, and how.

    There are two unknown values. The first is whether the library can deliver
    a callback to a ctypes binding: that needs manual dispatch, which is SDK
    1.51 or newer. The second is the callback number of a chat message.

    This function thus prints each callback that arrives, and not the expected
    one only.
    """
    _interrupt_on_sigterm()

    # A library in the configuration can be outside the search, so this
    # also reads that one.
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
            # The two are different tests. Chat also needs the permission
            # of the client. On this machine, that difference decides
            # whether the bar flashes.
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


# How often to read each process during the search for a game. The read is
# expensive, and a fast answer is not necessary.
PROCESS_SCAN_EVERY = 5          # ticks

# The exit code for "each switch is off". The watcher normally exits 0 after
# one game, and systemd starts it again.
#
# This state thus needs an exit code that systemd can separate from that one.
# Without it, the unit starts again without a limit. RestartPreventExitStatus
# names this code.
NOTHING_TO_WATCH_EXIT = 3


def _should_scan_processes(tick, attached):
    """Returns whether this turn does the full read of the processes.

    It returns True while this is attached to a game. On some machines,
    registry.vdf never names the running app. A turn with no read thus reads as
    "no game", and this detaches from a game that still runs.
    """
    return attached or tick % PROCESS_SCAN_EVERY == 0


def _flash(fifo, kind):
    """Writes a trigger, and continues when the service does not read it."""
    try:
        notify.send(fifo, kind)
    except OSError as exc:
        LOG.warning("could not flash the bar: %s", exc)


def _open_friend_listener(stats, want_messages):
    """Starts a read of the friend activity, or None if Steam refuses."""
    try:
        listener = steamworks.FriendListener(stats, want_messages=want_messages)
        listener.open()
        return listener
    except steamworks.SteamworksError as exc:
        # This is not a failure. The achievements are the main feature and
        # they still operate.
        LOG.warning("friend activity unavailable: %s", exc)
        return None


def run_watch_achievements(config, interval=1.0):
    """Flashes the bar for an achievement and for friend activity in a game.

    It runs as the normal user, beside Steam. It does not run as the service in
    its sandbox. It writes trigger words into the notification pipe and does
    nothing else.
    """
    _interrupt_on_sigterm()

    achievements_on = config["NOTIFY_ACHIEVEMENTS"]
    messages_on = config["NOTIFY_MESSAGES"]
    friends_on = config["NOTIFY_FRIEND_ONLINE"]
    if not (achievements_on or messages_on or friends_on):
        # To attach opens a Steamworks session as the running game for no
        # reason. That registration is what keeps Steam at "Stopping".
        print("NOTIFY_ACHIEVEMENTS, NOTIFY_MESSAGES and NOTIFY_FRIEND_ONLINE "
              "are all off, so there is nothing to watch for.", flush=True)
        return NOTHING_TO_WATCH_EXIT

    fifo = config["NOTIFY_FIFO"]
    watcher = None
    listener = None
    current_app = None
    stats = None

    # flush, because this runs as a service. Python buffers a stdout that
    # goes to a pipe. These lines thus stay in the buffer until the
    # process stops, and they then reach the journal and describe a run
    # that is finished.
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
                    # And then exit. Steam has this process registered as an
                    # instance of the game, and only an exit clears that
                    # registration. SteamAPI_Shutdown does not clear it. systemd
                    # starts the next watcher, because it has Restart=always.
                    LOG.info("game ended - exiting so Steam can finish "
                             "stopping it; systemd restarts the watcher")
                    return 0
                current_app = app_id
                if app_id:
                    try:
                        library = steamworks.find_library(
                            config["STEAM_LIBRARY"])
                        # Friend activity needs a session with manual dispatch,
                        # and only a new library can open one.
                        #
                        # Chat needs more than "who came online". That signal
                        # arrives with no request to Steam.
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
                            # This decides before it opens a session,
                            # because a session has a cost: it registers
                            # this process as the game, and only an exit of
                            # the process clears that.
                            #
                            # To attach and read nothing holds the game at
                            # "Stopping" and gives no flash.
                            print("Achievements are switched off and %s cannot "
                                  "deliver friend activity, so there is "
                                  "nothing to watch for." % library, flush=True)
                            return NOTHING_TO_WATCH_EXIT

                        route = config["STEAM_ROUTE"]
                        if not route or route == "auto":
                            # It probes in a child process. A wrong route stops
                            # that process.
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
                            # The library can do it and Steam refused. The
                            # achievements are off, so this session has
                            # nothing to report.
                            #
                            # An exit of the process also releases the
                            # registration that it took.
                            print("Steam will not forward friend activity to "
                                  "this app and achievements are switched "
                                  "off, so there is nothing to watch for.",
                                  flush=True)
                            return NOTHING_TO_WATCH_EXIT
                        LOG.info("attached to app %d", app_id)
                    except steamworks.SteamworksError as exc:
                        # current_app is set, so this tries again at the
                        # next game only.
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
                        # One flash for each kind, whatever the number that
                        # arrived. The queue discards a repeat of the flash
                        # that it shows, so a group gives one flash.
                        messages, online = listener.poll()
                        if messages and messages_on:
                            LOG.info("%d friend message(s)", len(messages))
                            _flash(fifo, "message")
                        if online and friends_on:
                            # No names in the log. The people that a person
                            # plays with are that person's own business.
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

    Run `print_only` first. It reports each notification that it sees and the
    flash that it would make, and it flashes nothing.

    A person thus finds two answers by a look, and not by a guess in a rule.
    The first is whether KDE Connect answers on this machine. The second is the
    names of the apps on it.
    """
    _interrupt_on_sigterm()

    if not print_only and not config["NOTIFY_PHONE"]:
        print("NOTIFY_PHONE is off, so there is nothing to watch for.",
              flush=True)
        return NOTHING_TO_WATCH_EXIT

    rules = phone.parse_rules(config["PHONE_APPS"])
    fifo = config["NOTIFY_FIFO"]

    # Ask for KDE Connect before this reads it. A monitor attaches to a name
    # and does not request it. With nothing behind that name, it thus waits
    # with no message. Game Mode is such a state: it has no desktop session to
    # start the daemon.
    #
    # A dry run never starts it. --print reports on the machine, and a report
    # that starts a daemon changes what it describes.
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
        # gdbus is part of glib. This is thus a machine with no desktop
        # software, and a restart does not change that.
        print("cannot start gdbus: %s" % exc, file=sys.stderr, flush=True)
        return MONITOR_MISSING_EXIT

    known = [woken]

    def look_again():
        """Asks at intervals whether KDE Connect runs and is still paired.

        This process outlives the session that started it. A check at the start
        thus says nothing about the state twenty minutes later in Game Mode.

        The question also starts KDE Connect again after it stops. The bus
        activates it as it did the first time.
        """
        now = phone.wake_kdeconnect()
        if now == known[0]:
            return
        was, known[0] = known[0], now
        if was is None and now is not None and not print_only:
            # KDE Connect returned. The phone thus connects again, and the
            # first data on that connection is each notification that the
            # phone holds. That is the group from the boot, and it needs the
            # same answer.
            bridge.expect_backlog()
        if now is None:
            # Whether there was a program to start. This reports it. Without
            # the report, two states read as one silence: "kdeconnectd is not
            # here" and "this started it and it does not answer".
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
        """Returns a short interval while KDE Connect is absent, else a long one.

        One minute is correct for a check on a program that operates. It is a
        long time to wait while the phone tries to connect again.

        A check every few seconds for the full day, to find the few minutes each
        week that need it, is the other mistake.
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
    # The monitor stopped by itself. The bus most probably stopped with the
    # session. systemd starts the next monitor.
    LOG.info("the notification bus stopped talking (%d seen, %d flashed)",
             bridge.seen, bridge.flashed)
    return 0


def run_notify(config, kind):
    """Sends a notification to a service that runs."""
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


def dump_line(snapshot, written, seen):
    """Returns one state change, with the time from the previous one.

    The interval is between the marks of the module and not between the
    moments at which this loop saw them. The module marks each write, and this
    loop can be a moment behind one.

    The column thus answers "how often does Steam write". A download raises
    that question, and nothing in this project can answer it from outside.

    It also reports when the answer omits a write. The shim gives the current
    state and not a queue, so this loop reads two writes inside one wait as
    one write.

    The counter reports that. An interval that covers a write that nobody saw
    is not the interval that a person wants.
    """
    gap = ("" if written is None
           else "+%.2fs" % ((snapshot.monotonic_ns - written) / 1e9))
    missed = ("" if seen is None or snapshot.seq <= seen + 1
              else "  (%d write(s) not seen)" % (snapshot.seq - seen - 1))
    return "%8s %s%s" % (gap, snapshot, missed)


def run_dump(config):
    """Prints the decoded snapshots. It does not open the serial port."""
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
    """Draws test patterns. It uses neither Steam nor the kernel module."""
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
    """Sends a made snapshot to the ESP, as a snapshot from Steam."""
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
    # This is not a mode of its own. It changes what --watch-phone does with
    # what it finds. It is thus with the options and not below them.
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
