"""Service entry point: shim -> renderer -> USB serial -> ESP."""

from __future__ import annotations

import argparse
import errno
import itertools
import logging
import select
import signal
import sys
import time

from . import config as config_module
from . import elf, notify, render, serialport, shim, steamworks, temperature
from .link import EspLink

LOG = logging.getLogger("steamos-led")

PROGRAM = "steamos-led-serial"
DEVICE_RETRY_DELAY = 5.0


class _Stopped(Exception):
    """Raised internally when a signal asks us to shut down."""


def build_temperature_source(config):
    """A sensor to read, or None when the gauge is switched off."""
    if not config["TEMPERATURE_GAUGE"]:
        return None
    return temperature.TemperatureSource(path=config["TEMPERATURE_SENSOR"])


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
    )


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
        )
        self.trigger = None
        self.source = None

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
            self.overlay.trigger(word, now)

    def _loop(self):
        started = time.monotonic()
        snapshot = None
        last_key = None

        while self.running:
            connected = self.link.connect()
            self.link.poll()

            interval = 1.0 / self.config["FPS"]
            if (snapshot is not None and not snapshot.is_animated
                    and not self.overlay.active):
                interval = 1.0 / self.config["IDLE_FPS"]

            try:
                changed, triggered = self._wait(interval)
            except OSError as exc:
                self._recover("poll on %s failed: %s",
                              self.config["DEVICE"], exc)
                continue

            if triggered:
                self._poll_trigger(time.monotonic())

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
            # A flash covers the whole bar; nothing underneath is worth drawing.
            payload = self.overlay.frame(now)
            if payload is None:
                payload = self.renderer.render(snapshot, now - started)
            # Idle heartbeat too: the firmware blanks the strip if we go quiet.
            self.link.send_frame(payload, self.config["LED_COUNT"])


# -- alternative modes ----------------------------------------------------


def _interrupt_on_sigterm():
    """Let the interactive modes run their cleanup when systemd stops them."""
    def handler(_signum, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, handler)


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

    Which sensor is the right one is a per-machine question - a laptop reports
    a dozen and most of them measure something nobody means by "how hot is it".
    So every one is listed with its current reading, and the chosen one is
    marked, which is what a TEMPERATURE_SENSOR line needs to be written by hand.
    """
    sensors = temperature.find_sensors()
    if not sensors:
        print("This machine reports no temperature sensors at all under %s."
              % temperature.HWMON_ROOT)
        print("The gauge cannot work here; the rainbow is shown instead.")
        return 1

    chosen = temperature.pick_sensor(sensors)
    print("Temperature sensors on this machine:")
    for sensor in sorted(sensors, key=lambda entry: entry["rank"]):
        celsius = temperature.read_celsius(sensor["path"])
        print("  [%s] %-12s %-12s %6s  %s"
              % ("use " if sensor is chosen else "    ",
                 sensor["chip"], sensor["label"] or "-",
                 "%.1f C" % celsius if celsius is not None else "-",
                 sensor["path"]))

    print()
    source = build_temperature_source(config)
    if source is None:
        print("The gauge is off (TEMPERATURE_GAUGE=0), so the rainbow effect")
        print("is shown as usual. Set TEMPERATURE_GAUGE=1 to swap it for this.")
        return 0

    celsius = source.celsius()
    print("Reading %s: %s"
          % (source.path,
             "%.1f C" % celsius if celsius is not None else "nothing"))
    low, high = config["TEMPERATURE_MIN"], config["TEMPERATURE_MAX"]
    print("Gauge: empty at or below %g C, full at %g C." % (low, high))
    if celsius is not None:
        renderer = render.Renderer(led_count=shim.LOGICAL_LEDS,
                                   temperature=source,
                                   temperature_range=(low, high))
        frame = renderer.render_logical(
            shim.make_snapshot(shim.EFFECT_RAINBOW), 0.0)
        lit = sum(1 for pixel in frame if max(pixel) > 0.5)
        print("Right now: %d of %d LEDs lit, colour #%02x%02x%02x"
              % ((lit, shim.LOGICAL_LEDS)
                 + tuple(int(value) for value in max(frame, key=max))))
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
    print("Realtime detection works here: steamos-led-serial --watch-achievements")
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

    print("Libraries, and what each one offers for friend messages:")
    usable = []
    for path in candidates:
        support = steamworks.message_support(path)
        if support.get("error"):
            print("  [ ?  ] %s (%s)" % (path, support["error"]))
            continue
        ok = steamworks.usable_for_messages(support)
        if ok:
            usable.append(path)
        print("  [%s] %s" % ("use " if ok else "skip", path))
        print("         manual dispatch: %-5s  listen: %-5s  read: %s"
              % (support["manual_dispatch"], support["listen"],
                 support["read_message"]))
        print("         ISteamFriends via: %s"
              % (", ".join(support["accessors"]
                           + (["FindOrCreateUserInterface"]
                              if support["find_or_create"] else [])
                           + (["ISteamClient"] if support["via_client"]
                              else [])) or "nothing found"))
    print()
    if not usable:
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
        listener = steamworks.FriendMessageListener(stats)
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


def _open_message_listener(stats):
    """Start listening for friend chat, or None if Steam will not have it."""
    try:
        listener = steamworks.FriendMessageListener(stats)
        listener.open()
        return listener
    except steamworks.SteamworksError as exc:
        # Not fatal: achievements are the main event and still work.
        LOG.warning("friend messages unavailable: %s", exc)
        return None


def run_watch_achievements(config, interval=1.0):
    """Flash the bar whenever an achievement unlocks in the running game.

    Runs as your normal user next to Steam, not as the sandboxed service, and
    only writes trigger words into the notification pipe.
    """
    _interrupt_on_sigterm()

    fifo = config["NOTIFY_FIFO"]
    watcher = None
    listener = None
    current_app = None
    stats = None

    print("Watching for achievements; flashes go to %s" % fifo)
    print("Press Ctrl-C to stop.")

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
                        route = config["STEAM_ROUTE"]
                        library = steamworks.find_library(
                            config["STEAM_LIBRARY"])
                        if not route or route == "auto":
                            # Probes in child processes; a bad route segfaults.
                            route, _count = steamworks.select_route(
                                app_id, library)
                            if route is None:
                                raise steamworks.SteamworksError(
                                    "no working route for app %d - run "
                                    "--steam-check for details" % app_id)
                            LOG.info("using route %s", route)
                        # Friend messages need a manual-dispatch session, and
                        # only a new enough library can open one.
                        manual = (config["NOTIFY_MESSAGES"]
                                  and steamworks.usable_for_messages(
                                      steamworks.message_support(library)))
                        if config["NOTIFY_MESSAGES"] and not manual:
                            LOG.info("%s is too old to deliver friend "
                                     "messages; achievements only", library)
                        stats = steamworks.UserStats(app_id, library,
                                                     route=route,
                                                     manual_dispatch=manual)
                        stats.open()
                        watcher = steamworks.AchievementWatcher(stats)
                        listener = _open_message_listener(stats) if manual \
                            else None
                        LOG.info("attached to app %d", app_id)
                    except steamworks.SteamworksError as exc:
                        # current_app is set, so no retry until another game.
                        LOG.warning("cannot attach to app %s: %s", app_id, exc)
                        stats, watcher = None, None
                else:
                    LOG.info("no game running")

            if watcher is not None:
                try:
                    for name in watcher.poll():
                        LOG.info("achievement unlocked: %s",
                                 stats.display_name(name))
                        _flash(fifo, "achievement")
                    if listener is not None:
                        # One flash however many arrived: a retrigger restarts
                        # the animation, so a burst would hold the bar lit.
                        messages = listener.messages()
                        if messages:
                            LOG.info("%d friend message(s)", len(messages))
                            _flash(fifo, "message")
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


def run_dump(config):
    """Print decoded snapshots without touching the serial port."""
    _interrupt_on_sigterm()
    with shim.ShimSource(config["DEVICE"]) as source:
        last = None
        print("watching %s, press Ctrl-C to stop" % config["DEVICE"])
        while True:
            snapshot = source.read()
            if snapshot is not None and snapshot.key() != last:
                last = snapshot.key()
                print(snapshot)
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
                            "message, friend, warning or a colour like '#00ff88'")
    modes.add_argument("--watch-achievements", action="store_true",
                       dest="watch_achievements",
                       help="flash on every achievement unlocked in the running "
                            "game (run as your normal user, not with sudo)")
    modes.add_argument("--check-config", action="store_true",
                       dest="check_config",
                       help="load and validate the configuration and exit; "
                            "prints the effective settings")
    modes.add_argument("--temperature", action="store_true",
                       help="list the machine's temperature sensors and show "
                            "what the gauge would display right now")
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
        return 2

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
        if args.temperature:
            return run_temperature(config)
        if args.steam_check:
            return run_steam_check(config)
        if args.probe_messages is not None:
            return run_probe_messages(config, args.probe_messages or None)
        if args.watch_achievements:
            return run_watch_achievements(config)
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
