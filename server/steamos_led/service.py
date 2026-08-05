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
from . import elf, notify, render, serialport, shim, steamworks
from .link import EspLink

LOG = logging.getLogger("steamos-led")

PROGRAM = "steamos-led-serial"
DEVICE_RETRY_DELAY = 5.0


class _Stopped(Exception):
    """Raised internally when a signal asks us to shut down."""


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
        notification from sitting in the pipe for up to a quarter second while
        the bar is idle - and reporting which one woke us means the pipe is
        read exactly when it has something in it.
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
                LOG.warning("poll on %s failed: %s", self.config["DEVICE"], exc)
                self.source.close()
                self.source = self._open_source()
                continue

            if triggered:
                self._poll_trigger(time.monotonic())

            if changed or snapshot is None:
                try:
                    new_snapshot = self.source.read()
                except (OSError, shim.SnapshotError) as exc:
                    LOG.warning("reading %s failed: %s", self.config["DEVICE"], exc)
                    self.source.close()
                    self.source = self._open_source()
                    snapshot = None
                    continue
                if new_snapshot is not None:
                    snapshot = new_snapshot
                    key = snapshot.key()
                    if key != last_key:
                        LOG.debug("state change: %s", snapshot)
                        last_key = key

            if snapshot is None or not connected:
                continue

            now = time.monotonic()
            # A notification takes the whole bar for its duration and then
            # hands it straight back to whatever Steam is showing - so while
            # one runs there is nothing underneath worth rendering.
            payload = self.overlay.frame(now)
            if payload is None:
                payload = self.renderer.render(snapshot, now - started)
            # Static scenes still need a periodic frame: the firmware blanks the
            # strip when the link goes quiet, so an idle heartbeat is what tells
            # it we are still alive.
            self.link.send_frame(payload, self.config["LED_COUNT"])


# -- alternative modes ----------------------------------------------------


def _interrupt_on_sigterm():
    """Let the interactive modes run their cleanup when systemd stops them."""
    def handler(_signum, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, handler)


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
        # Which route into ISteamUserStats this library offers is the thing
        # most likely to differ between SDK generations, so show it up front.
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

    def report(route, status, detail):
        mark = {"ok": "WORKS ", "crashed": "CRASH ", "failed": "no    "}[status]
        print("  [%s] %-52s %s" % (mark, route, detail), flush=True)

    route, count = steamworks.select_route(app_id, library, reporter=report)
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


# How often the loop below falls back to scanning every process for the
# running app. The registry answers most of the time and costs one small file
# read; the scan reads the environment block of every process the user owns,
# which is not something to do every second for the whole login.
PROCESS_SCAN_EVERY = 5          # ticks


def run_watch_achievements(config, interval=1.0):
    """Flash the bar whenever an achievement unlocks in the running game.

    Runs as your normal user, next to Steam - not as the service, which is
    sandboxed away from your home directory. It only writes trigger words into
    the notification pipe, so the service stays untouched.
    """
    _interrupt_on_sigterm()

    fifo = config["NOTIFY_FIFO"]
    watcher = None
    current_app = None
    stats = None

    print("Watching for achievements; flashes go to %s" % fifo)
    print("Press Ctrl-C to stop.")

    try:
        for tick in itertools.count():
            app_id = steamworks.running_app_id(
                scan_processes=tick % PROCESS_SCAN_EVERY == 0)

            if app_id != current_app:
                if stats is not None:
                    stats.close()
                    stats, watcher = None, None
                current_app = app_id
                if app_id:
                    try:
                        route = config["STEAM_ROUTE"]
                        library = steamworks.find_library(
                            config["STEAM_LIBRARY"])
                        if not route or route == "auto":
                            # Probe in child processes first: picking the wrong
                            # interface version would take the watcher down
                            # with a segfault rather than an exception.
                            route, _count = steamworks.select_route(
                                app_id, library)
                            if route is None:
                                raise steamworks.SteamworksError(
                                    "no working route for app %d - run "
                                    "--steam-check for details" % app_id)
                            LOG.info("using route %s", route)
                        stats = steamworks.UserStats(app_id, library, route=route)
                        stats.open()
                        watcher = steamworks.AchievementWatcher(stats)
                        LOG.info("attached to app %d", app_id)
                    except steamworks.SteamworksError as exc:
                        # current_app is already this app, so the loop will
                        # not try again until a different game starts.
                        LOG.warning("cannot attach to app %s: %s", app_id, exc)
                        stats, watcher = None, None
                else:
                    LOG.info("no game running")

            if watcher is not None:
                try:
                    for name in watcher.poll():
                        display = stats.display_name(name)
                        LOG.info("achievement unlocked: %s", display)
                        try:
                            notify.send(fifo, "achievement")
                        except OSError as exc:
                            LOG.warning("could not flash the bar: %s", exc)
                except OSError as exc:
                    LOG.warning("lost the Steamworks connection: %s", exc)
                    stats.close()
                    stats, watcher, current_app = None, None, None

            time.sleep(interval)
    except KeyboardInterrupt:
        pass
    finally:
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
    modes.add_argument("--steam-check", action="store_true", dest="steam_check",
                       help="report whether realtime achievement detection can "
                            "work on this machine")
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
        if args.steam_check:
            return run_steam_check(config)
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
