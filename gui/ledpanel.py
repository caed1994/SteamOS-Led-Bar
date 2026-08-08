"""What the control panel knows, minus the widgets.

Kept free of tkinter on purpose: the interesting parts are "what is broken"
and "what fixes it", and those are worth testing without a display.
"""

from __future__ import annotations

import os
import platform
import subprocess

INSTALL_DIR = "/var/lib/steamos-led-serial"
BINARY = os.path.join(INSTALL_DIR, "steamos-led-serial")
CONFIG_PATH = "/etc/steamos-led-serial.conf"
UNIT_PATH = "/etc/systemd/system/steamos-led-serial.service"
UDEV_PATH = "/etc/udev/rules.d/99-steamos-led-serial.rules"
SHIM_DEVICE = "/dev/valve-leds-shim"
MODULE_NAME = "leds-valve-shim"
SERVICE = "steamos-led-serial.service"
WATCHER = "steamos-led-achievements.service"


def module_path(release=None):
    """Where the built kernel module lives, for the running kernel."""
    return "/usr/lib/modules/%s/updates/%s.ko" % (
        release or platform.uname().release, MODULE_NAME)


class Check:
    """One thing that is either in order or not, and what to do if not."""

    def __init__(self, name, ok, detail="", repairable=False):
        self.name = name
        self.ok = ok
        self.detail = detail
        # Whether re-running the installer would put this right. A missing
        # kernel module would; an unplugged ESP would not.
        self.repairable = repairable

    def __repr__(self):                                     # pragma: no cover
        return "<Check %s %s>" % (self.name, "ok" if self.ok else "broken")


class Probe:
    """Reads the state of the installation. Every lookup goes through here so
    the tests can answer them instead of the machine."""

    def exists(self, path):
        return os.path.exists(path)

    def is_fifo(self, path):
        try:
            import stat
            return stat.S_ISFIFO(os.stat(path).st_mode)
        except OSError:
            return False

    def unit_active(self, unit, user=False):
        command = ["systemctl"]
        if user:
            command.append("--user")
        command += ["is-active", "--quiet", unit]
        try:
            return subprocess.call(command, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL) == 0
        except OSError:
            return False

    def kernel_release(self):
        return platform.uname().release


def run_checks(probe=None, config=None):
    """The state of the installation, worst news first."""
    probe = probe or Probe()
    checks = []

    checks.append(Check(
        "Service files installed", probe.exists(BINARY),
        "%s is missing - the installer has not run, or an update removed it"
        % BINARY, repairable=True))

    checks.append(Check(
        "systemd unit installed", probe.exists(UNIT_PATH),
        "%s is missing" % UNIT_PATH, repairable=True))

    checks.append(Check(
        "Configuration present", probe.exists(CONFIG_PATH),
        "%s is missing" % CONFIG_PATH, repairable=True))

    checks.append(Check(
        "udev rule installed", probe.exists(UDEV_PATH),
        "%s is missing, so the stable /dev/steamos-led-esp name is gone"
        % UDEV_PATH, repairable=True))

    # The one that a SteamOS update reliably breaks: the module is built for
    # one kernel version and a system update brings a new one.
    release = probe.kernel_release()
    built = module_path(release)
    checks.append(Check(
        "Kernel module built for %s" % release, probe.exists(built),
        "no %s - a SteamOS update usually means a new kernel, and the module "
        "has to be rebuilt for it" % built, repairable=True))

    checks.append(Check(
        "LED device present", probe.exists(SHIM_DEVICE),
        "%s is missing, so there is no LED state to read" % SHIM_DEVICE,
        repairable=True))

    checks.append(Check(
        "Service running", probe.unit_active(SERVICE),
        "systemctl status %s says why" % SERVICE, repairable=True))

    fifo = (config or {}).get("NOTIFY_FIFO") or "/run/steamos-led-serial/notify"
    notify_on = (config or {}).get("NOTIFY", True)
    if notify_on:
        checks.append(Check(
            "Notification pipe ready", probe.is_fifo(fifo),
            "%s does not exist, so nothing can flash the bar" % fifo,
            repairable=True))

    checks.append(Check(
        "Achievement watcher running", probe.unit_active(WATCHER, user=True),
        "journalctl --user -u %s says why" % WATCHER, repairable=True))

    return checks


def broken(checks):
    return [check for check in checks if not check.ok]


def repair_summary(checks):
    """One sentence for the top of the window."""
    problems = broken(checks)
    if not problems:
        return "Everything is in order."
    if any(check.name.startswith("Kernel module") for check in problems):
        return ("%d problem(s). The kernel module is missing for the running "
                "kernel - that is what a SteamOS update does, and reinstalling "
                "puts it back." % len(problems))
    return "%d problem(s) found." % len(problems)


# What Steam's Game Mode session looks like from inside a program it started.
GAME_MODE_MARKERS = ("GAMESCOPE_WAYLAND_DISPLAY", "STEAM_GAMESCOPE_VRR_ENABLED")

NO_AGENT_SIGNS = ("authentication agent", "/dev/tty",
                  "polkit-agent-helper", "No such device or address")


def in_game_mode(environ=None):
    """Whether this is running inside Steam's Game Mode session.

    Told apart by gamescope's own variables rather than by asking Steam:
    gamescope is the compositor Game Mode runs under, and it is what makes the
    difference that matters here.
    """
    environ = os.environ if environ is None else environ
    if any(marker in environ for marker in GAME_MODE_MARKERS):
        return True
    return "gamescope" in environ.get("XDG_CURRENT_DESKTOP", "").lower()


def looks_like_no_auth_agent(output, exit_code):
    """Whether a privileged command failed for want of a password prompt.

    pkexec needs a polkit agent to ask with. Game Mode runs none, and pkexec's
    fallback wants a controlling terminal, which a program Steam launched does
    not have - so it exits 127 complaining about /dev/tty, which explains
    nothing to anyone who has not met it before.
    """
    if exit_code == 0:
        return False
    lowered = (output or "").lower()
    return any(sign.lower() in lowered for sign in NO_AGENT_SIGNS)


NO_AGENT_ADVICE = (
    "That needs administrator rights, and Game Mode has nothing that can ask "
    "for your password - there is no polkit agent running in it, and no "
    "terminal to fall back on.\n"
    "Switch to Desktop Mode and do it there. Everything on the Test tab works "
    "here, though: flashing the bar needs no rights at all.")


def reinstall_command(source_dir, rebuild_module=True):
    """How to put an installation back together, unattended.

    Runs the same installer as a first install - it keeps an existing config,
    so nothing the user set is lost. --flash 0 is explicit: repairing after a
    system update must never touch the board.
    """
    command = ["pkexec", os.path.join(source_dir, "install.sh"), "--yes",
               "--flash", "0"]
    if rebuild_module:
        command.append("--rebuild-module")
    return command


def apply_config_command(source_dir, staged_path):
    """Install a prepared config file and restart the service, in one prompt."""
    return ["pkexec", os.path.join(source_dir, "scripts", "apply-config.sh"),
            staged_path]


def notify_command(kind):
    """Flash the bar. Deliberately unprivileged: the FIFO is world-writable."""
    return [BINARY, "--notify", kind]


def self_test_command(source_dir, seconds=12):
    """Drive test patterns on the strip.

    Privileged, and not only because of the serial port: the service holds
    that port exclusively, so the helper has to stop it, run the test and
    start it again.
    """
    return ["pkexec", os.path.join(source_dir, "scripts", "self-test.sh"),
            str(seconds)]


def steam_check_command():
    """Must run as the user: Steamworks talks to their Steam client."""
    return [BINARY, "--steam-check"]


def probe_messages_command():
    return [BINARY, "--probe-messages"]
