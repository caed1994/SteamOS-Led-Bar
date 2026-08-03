"""Service entry point: shim -> renderer -> USB serial -> ESP."""

from __future__ import annotations

import argparse
import errno
import logging
import os
import select
import signal
import sys
import time

from . import config as config_module
from . import notify, render, serialport, shim, steamworks
from .link import EspLink

LOG = logging.getLogger("steamos-led")

PROGRAM = "steamos-led-serial"
DEVICE_RETRY_DELAY = 5.0


class _Stopped(Exception):
    """Raised internally when a signal asks us to shut down."""


class Runner:
    def __init__(self, config):
        self.config = config
        self.running = True
        self.renderer = render.Renderer(
            led_count=config["LED_COUNT"],
            mapping=config["MAPPING"],
            reverse=config["REVERSE"],
            max_brightness=config["MAX_BRIGHTNESS"],
            min_brightness=config["MIN_BRIGHTNESS"],
            gamma=config["GAMMA"],
            speed_scale=config["SPEED"],
            patrol_dots=config["PATROL_DOTS"],
        )
        self.link = EspLink(
            port=config["SERIAL_PORT"],
            baudrate=config["BAUD"],
            led_count=config["LED_COUNT"],
            reconnect_delay=config["RECONNECT_DELAY"],
            autodetect_baud=config["BAUD_AUTODETECT"],
        )
        self.overlay = notify.NotificationOverlay(
            enabled=config["NOTIFY"],
            duration=config["NOTIFY_DURATION"],
            led_count=config["LED_COUNT"],
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

        Waiting on both at once keeps a notification from sitting in the pipe
        for up to a quarter second while the bar is idle.
        """
        sources = [self.source.fd]
        if self.trigger is not None and self.trigger.fd >= 0:
            sources.append(self.trigger.fd)
        readable, _, _ = select.select(sources, [], [], interval)
        return self.source.fd in readable

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

            self._poll_trigger(time.monotonic())

            interval = 1.0 / self.config["FPS"]
            if (snapshot is not None and not snapshot.is_animated
                    and not self.overlay.active):
                interval = 1.0 / self.config["IDLE_FPS"]

            changed = False
            try:
                changed = self._wait(interval)
            except OSError as exc:
                LOG.warning("poll on %s failed: %s", self.config["DEVICE"], exc)
                self.source.close()
                self.source = self._open_source()
                continue

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
            self._poll_trigger(now)
            payload = self.renderer.render(snapshot, now - started)
            # A notification takes the whole bar for its duration and then
            # hands it straight back to whatever Steam is showing.
            payload = self.overlay.apply(payload, now)
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


STEAM_ROOTS = (
    "~/.local/share/Steam",
    "~/.steam/steam",
    "~/.steam/root",
    "/home/deck/.local/share/Steam",
)
# Directories worth watching for an achievement. Game installs are excluded on
# purpose - only Steam's own bookkeeping is small enough to scan every second.
STEAM_WATCH_SUBDIRS = ("userdata", "appcache/stats", "logs")


def _steam_root():
    for candidate in STEAM_ROOTS:
        path = os.path.expanduser(candidate)
        if os.path.isdir(path):
            return path
    return None


def _scan_steam_files(root):
    """mtime of every file under the interesting Steam subdirectories."""
    seen = {}
    for subdir in STEAM_WATCH_SUBDIRS:
        base = os.path.join(root, subdir)
        for dirpath, _dirnames, filenames in os.walk(base):
            for name in filenames:
                path = os.path.join(dirpath, name)
                try:
                    seen[path] = os.stat(path).st_mtime
                except OSError:
                    continue
    return seen


def run_probe(config, seconds=None):
    """Watch Steam's own files and report what changes when you unlock one.

    Steam publishes no documented local signal for achievements, so rather
    than guess, this records what actually moves on your machine. Run it, earn
    an achievement, and read off which file Steam touched.
    """
    _interrupt_on_sigterm()
    del config

    root = _steam_root()
    if root is None:
        LOG.error("no Steam directory found, looked in: %s",
                  ", ".join(STEAM_ROOTS))
        return 1

    print("Watching %s" % root)
    print("  subdirectories: %s" % ", ".join(STEAM_WATCH_SUBDIRS))
    print()
    print("Now go and unlock an achievement. Every file Steam touches shows up")
    print("below; the useful ones are those that appear the moment it pops.")
    print("Press Ctrl-C when done.")
    print()

    baseline = _scan_steam_files(root)
    print("baseline: %d files" % len(baseline))

    deadline = time.monotonic() + seconds if seconds else None
    try:
        while deadline is None or time.monotonic() < deadline:
            time.sleep(1.0)
            current = _scan_steam_files(root)
            for path, mtime in sorted(current.items()):
                if baseline.get(path) != mtime:
                    stamp = time.strftime("%H:%M:%S")
                    kind = "new  " if path not in baseline else "write"
                    print("%s  %s  %s" % (stamp, kind, path))
            baseline = current
    except KeyboardInterrupt:
        pass

    print()
    print("Report the paths that appeared exactly when the achievement popped,")
    print("and the watcher can be pointed at them.")
    return 0


def run_steam_check(config):
    """Report what the Steamworks path can and cannot find on this machine."""
    del config
    print("Steam directory:   %s" % (steamworks.steam_root() or "NOT FOUND"))

    try:
        library = steamworks.find_library()
        print("libsteam_api.so:   %s" % library)
    except steamworks.SteamworksError as exc:
        print("libsteam_api.so:   NOT FOUND (%s)" % exc)
        library = None

    from_registry = steamworks._app_id_from_registry()
    from_processes = steamworks._app_id_from_processes()
    print("running app (registry.vdf): %s" % (from_registry or "none"))
    print("running app (process env):  %s" % (from_processes or "none"))

    app_id = from_registry or from_processes
    if library is None:
        print()
        print("Without the library nothing else can be tried.")
        return 1
    if not app_id:
        print()
        print("No game is running, so the API cannot be initialised as one.")
        print("Start a game and run this again.")
        return 1

    print()
    print("Initialising Steamworks as app %d ..." % app_id)
    stats = steamworks.UserStats(app_id, library)
    try:
        stats.open()
    except steamworks.SteamworksError as exc:
        print("  FAILED: %s" % exc)
        return 1

    try:
        achievements = stats.achievements()
        unlocked = sum(1 for value in achievements.values() if value)
        print("  OK - %d achievements, %d unlocked" % (len(achievements), unlocked))
        for name, is_unlocked in sorted(achievements.items())[:5]:
            print("    [%s] %s" % ("x" if is_unlocked else " ",
                                   stats.display_name(name)))
        if len(achievements) > 5:
            print("    ... and %d more" % (len(achievements) - 5))
    finally:
        stats.close()

    print()
    print("This machine can do realtime detection: steamos-led-serial "
          "--watch-achievements")
    return 0


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
        while True:
            app_id = steamworks.running_app_id()

            if app_id != current_app:
                if stats is not None:
                    stats.close()
                    stats, watcher = None, None
                current_app = app_id
                if app_id:
                    try:
                        stats = steamworks.UserStats(app_id)
                        stats.open()
                        watcher = steamworks.AchievementWatcher(stats)
                        LOG.info("attached to app %d", app_id)
                    except steamworks.SteamworksError as exc:
                        LOG.warning("cannot attach to app %s: %s", app_id, exc)
                        stats, watcher = None, None
                        # Do not hammer a game that refuses us.
                        current_app = app_id
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
    renderer = render.Renderer(
        led_count=config["LED_COUNT"],
        mapping=config["MAPPING"],
        reverse=config["REVERSE"],
        max_brightness=config["MAX_BRIGHTNESS"],
        min_brightness=config["MIN_BRIGHTNESS"],
        gamma=config["GAMMA"],
        speed_scale=config["SPEED"],
        patrol_dots=config["PATROL_DOTS"],
    )
    link = EspLink(
        port=config["SERIAL_PORT"],
        baudrate=config["BAUD"],
        led_count=config["LED_COUNT"],
        reconnect_delay=config["RECONNECT_DELAY"],
        autodetect_baud=config["BAUD_AUTODETECT"],
    )

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
    renderer = render.Renderer(
        led_count=config["LED_COUNT"],
        mapping=config["MAPPING"],
        reverse=config["REVERSE"],
        max_brightness=config["MAX_BRIGHTNESS"],
        min_brightness=config["MIN_BRIGHTNESS"],
        gamma=config["GAMMA"],
        speed_scale=config["SPEED"],
        patrol_dots=config["PATROL_DOTS"],
    )
    link = EspLink(
        port=config["SERIAL_PORT"],
        baudrate=config["BAUD"],
        led_count=config["LED_COUNT"],
        reconnect_delay=config["RECONNECT_DELAY"],
        autodetect_baud=config["BAUD_AUTODETECT"],
    )
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
    modes.add_argument("--probe-achievements", nargs="?", const=0.0, type=float,
                       metavar="SECONDS", dest="probe",
                       help="watch which Steam files change when an achievement "
                            "unlocks, to find a signal worth listening to")
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
        if args.probe is not None:
            return run_probe(config, args.probe or None)
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
