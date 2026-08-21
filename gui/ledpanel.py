# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""What the control panel knows, minus the widgets.

Kept free of tkinter on purpose: the interesting parts are "what is broken"
and "what fixes it", and those are worth testing without a display.
"""

from __future__ import annotations

import os
import platform
import re
import subprocess

from steamos_led import config as config_module
from steamos_led import phone
from steamos_led import temperature

INSTALL_DIR = "/var/lib/steamos-led-serial"
BINARY = os.path.join(INSTALL_DIR, "steamos-led-serial")
CONFIG_PATH = "/etc/steamos-led-serial.conf"
UNIT_PATH = "/etc/systemd/system/steamos-led-serial.service"
UDEV_PATH = "/etc/udev/rules.d/99-steamos-led-serial.rules"
SHIM_DEVICE = "/dev/valve-leds-shim"
MODULE_NAME = "leds-valve-shim"
SERVICE = "steamos-led-serial.service"
WATCHER = "steamos-led-achievements.service"
PHONE_BRIDGE = "steamos-led-phone.service"

# How long to let KDE Connect answer before calling it silent. Shorter than
# the service's own wait, because the panel asks this on the thread that draws
# the window. Both usual answers - it is there, or the bus has no such name -
# come back in milliseconds; the wait is only for a machine where it hangs.
PHONE_ASK_SECONDS = 2.0


# The panel's own icon, next to the panel. A theme icon name is the fallback
# because a missing icon file leaves a menu entry with no picture at all.
ICON_NAME = "steamos-led-panel.png"
FALLBACK_ICON = "preferences-desktop-display"


def panel_icon(source_dir):
    """The icon to use: the file shipped with the panel, or a theme name."""
    path = os.path.join(source_dir, "gui", ICON_NAME)
    return path if os.path.exists(path) else FALLBACK_ICON


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
    """One place for every lookup, so tests can answer them, not the machine."""

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

    def lingering(self, user=None):
        """Whether this user's systemd keeps running with no session open.

        Without it, switching to Game Mode ends the session and every user
        service goes with it - including the two this project installs, both
        of which are written to survive exactly that.

        Asked about a *particular* user, by number. Without one loginctl
        answers about something else entirely and never mentions Linger at
        all - so this reported "no" on a machine where the same question,
        asked with the user named, said yes.
        """
        who = str(os.getuid() if user is None else user)
        command = ["loginctl", "show-user", who, "--property=Linger"]
        try:
            done = subprocess.run(command, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True)
        except OSError:
            return False
        return "Linger=yes" in done.stdout

    def phones(self):
        """The phones KDE Connect names, or None when it says nothing at all.

        Those are two different problems wearing the same face - a bar that
        never flashes - so they are worth telling apart: nothing running to
        forward the notifications, against something running that no phone is
        talking to.

        Asked without reviving it, because a status check reports what is
        there and does not quietly start things.
        """
        return phone.wake_kdeconnect(revive=False, timeout=PHONE_ASK_SECONDS)

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

    # Only when the feature is on: the bridge stops on purpose while
    # NOTIFY_PHONE is off, so checking it unconditionally would report two
    # problems on every machine that simply does not want phone flashes.
    if (config or {}).get("NOTIFY_PHONE"):
        checks.append(Check(
            "Phone bridge running", probe.unit_active(PHONE_BRIDGE, user=True),
            "journalctl --user -u %s says why" % PHONE_BRIDGE,
            repairable=True))

        found = probe.phones()
        if found is None:
            why = ("KDE Connect is not answering - open it once from the "
                   "application menu, and install it if it is not there")
        else:
            why = ("KDE Connect is running, but no phone is paired with it - "
                   "pair this machine in the KDE Connect app on the phone, "
                   "and switch its notification sync on there")
        checks.append(Check(
            "KDE Connect paired with %s"
            % (", ".join(found) if found else "your phone"), bool(found), why,
            # Pairing happens on the phone, which no amount of reinstalling
            # here can do.
            repairable=False))

    # Last, because it is the one that explains the others going quiet rather
    # than failing: without it they are not running to fail.
    checks.append(Check(
        "Services survive Game Mode", probe.lingering(),
        "your systemd stops when the desktop session ends, and both watchers "
        "with it - sudo loginctl enable-linger $USER", repairable=True))

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


# -- settings profiles -----------------------------------------------------
#
# A profile is the same KEY=value file as the configuration, which is what
# makes this cheap: the parser, the validator and the formatter all already
# exist. It holds exactly what the panel can set, so the machine-specific
# lines the panel does not show - serial port, baud rate, device - are never
# in one and cannot arrive from somebody else's machine.
#
# In the clone rather than under ~/.config, because that is where you already
# go for this project, and because it needs no privileges: the panel writes it
# as you, unlike the configuration in /etc.
PROFILE_DIR = "profiles"
PROFILE_SUFFIX = ".conf"


def profiles_dir(source_dir):
    return os.path.join(source_dir, PROFILE_DIR)


def profiles(directory):
    """The profiles in that directory, by name and without the suffix.

    A missing directory is not a problem worth reporting: it means none have
    been saved yet, which is what an empty list already says.
    """
    try:
        names = os.listdir(directory)
    except OSError:
        return ()
    return tuple(sorted(
        name[:-len(PROFILE_SUFFIX)] for name in names
        if name.endswith(PROFILE_SUFFIX) and len(name) > len(PROFILE_SUFFIX)))


def profile_path(directory, name):
    """Where a profile of that name goes, whatever the user typed.

    The suffix is not the user's business - they name a profile, they do not
    name a file - so it is added when it is missing and never twice.
    """
    name = name.strip()
    if not name:
        return None
    if not name.endswith(PROFILE_SUFFIX):
        name += PROFILE_SUFFIX
    return os.path.join(directory, os.path.basename(name))


def profile_text(values):
    """A profile file, ready to write."""
    lines = [
        "# A settings profile for the SteamOS LED bar control panel.",
        "# Load it from the panel with \"Load profile\", or copy the lines",
        "# you want into /etc/steamos-led-serial.conf by hand.",
        "",
    ]
    for key in sorted(values):
        lines.append("%s=%s" % (key, config_module.format_value(values[key])))
    return "\n".join(lines) + "\n"


def read_profile(path):
    """The settings in a profile file.

    Straight through the configuration parser, so an unknown option is
    refused here rather than at the next service start, and one we withdrew
    is skipped with a note the same way.
    """
    return config_module.parse_file(path)


# What Steam's Game Mode session looks like from inside a program it started.
GAME_MODE_MARKERS = ("GAMESCOPE_WAYLAND_DISPLAY", "STEAM_GAMESCOPE_VRR_ENABLED")

# Phrases only pkexec's own complaint uses. "/dev/tty" was in here once and
# matched the serial port a firmware flash prints - /dev/ttyUSB0 - so a
# perfectly good flash came with advice about Game Mode under it.
NO_AGENT_SIGNS = ("authentication agent", "polkit-agent-helper",
                  "controlling terminal")


def in_game_mode(environ=None):
    """Whether this is running inside Steam's Game Mode session.

    Told apart by gamescope's own variables: it is the compositor Game Mode
    runs under, and what makes the difference that matters here.
    """
    environ = os.environ if environ is None else environ
    if any(marker in environ for marker in GAME_MODE_MARKERS):
        return True
    return "gamescope" in environ.get("XDG_CURRENT_DESKTOP", "").lower()


def looks_like_no_auth_agent(output, exit_code):
    """Whether a privileged command failed for want of a password prompt.

    pkexec needs a polkit agent to ask with. Game Mode runs none, and pkexec's
    fallback wants a controlling terminal that a Steam-launched program has
    not got - so it exits 127 complaining about /dev/tty.
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

    The same installer as a first install, which keeps an existing config.
    --flash 0 is explicit: repairing must never touch the board.
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


# -- updating the clone ----------------------------------------------------
#
# Unprivileged: the clone belongs to whoever made it. Only installing what an
# update brings needs rights, and that is the separate step afterwards.


def update_command(source_dir, branch=None, check=False):
    """Bring the clone up to date, or with check=True only report what would."""
    command = [os.path.join(source_dir, "scripts", "update.sh")]
    if check:
        command.append("--check")
    if branch:
        command.append(branch)
    return command


# What a --check run found. The window needs it to say so somewhere the log
# does not have to be unfolded for, and to know whether installing would do
# anything at all.
UPDATE_CURRENT = "current"
UPDATE_AVAILABLE = "available"
UPDATE_UNKNOWN = "unknown"

_ALREADY = re.compile(r"^Already up to date with ", re.MULTILINE)
_WAITING = re.compile(r"^(\d+) commit\(s\) waiting on ", re.MULTILINE)
_UPDATED = re.compile(r"^Updated \S+ to ", re.MULTILINE)
_STOPPER = re.compile(r"^Note: .*would stop", re.MULTILINE)


def update_verdict(text, code=0):
    """What an update run said, as (state, sentence) the window can act on.

    Read back out of the output rather than asked for a second time: the
    script is the thing that knows, and running it twice to be told the same
    thing is how the two answers start to disagree.

    A run that failed says nothing either way - the log has the reason, and
    guessing over it would be worse than admitting the answer is not known.
    """
    if code != 0:
        return UPDATE_UNKNOWN, ""
    if _ALREADY.search(text) or _UPDATED.search(text):
        return UPDATE_CURRENT, "Up to date."
    waiting = _WAITING.search(text)
    if not waiting:
        return UPDATE_UNKNOWN, ""
    count = int(waiting.group(1))
    sentence = ("1 update waiting." if count == 1
                else "%d updates waiting." % count)
    if _STOPPER.search(text):
        # The script says what it would refuse over; better here than after
        # pressing the button and having it stop.
        sentence += " Installing would stop - see the log."
    return UPDATE_AVAILABLE, sentence


def _git(source_dir, *args):
    """git's output, or "" if it fails - including "this is not a clone"."""
    try:
        result = subprocess.run(("git", "-C", source_dir) + args,
                                capture_output=True, text=True)
    except OSError:
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def is_git_clone(source_dir):
    return bool(_git(source_dir, "rev-parse", "--is-inside-work-tree"))


def current_branch(source_dir):
    """The branch the clone is on, or "" when it is on none."""
    return _git(source_dir, "symbolic-ref", "--quiet", "--short", "HEAD")


def known_branches(source_dir, remote="origin"):
    """Branches this clone has heard of, without asking the network.

    Enough to fill a menu at startup; a branch made since the last fetch turns
    up once the update itself has fetched.
    """
    return parse_branches(_git(source_dir, "for-each-ref",
                               "--format=%(refname:strip=3)",
                               "refs/remotes/%s" % remote))


def parse_branches(text):
    """Branch names out of for-each-ref, minus the origin/HEAD pointer."""
    names = {line.strip() for line in text.splitlines() if line.strip()}
    return sorted(names - {"HEAD"})


def head_commit(source_dir):
    return _git(source_dir, "rev-parse", "HEAD")


def module_changed(source_dir, since):
    """Whether the kernel module's source moved since that commit.

    The installer leaves a loaded module alone unless told otherwise, which is
    right for a repair and wrong after an update that changed it. Not knowing
    counts as changed: rebuilding costs half a minute, a stale module costs
    the bar.
    """
    if not since:
        return True
    return bool(_git(source_dir, "diff", "--name-only",
                     "%s..HEAD" % since, "--", MODULE_SOURCE_DIR))


MODULE_SOURCE_DIR = "leds-valve-shim"


# -- the ESP firmware ------------------------------------------------------
#
# (label, environment) pairs, in the order install.sh's menu lists them. The
# environments themselves live in firmware/led-client/platformio.ini; a test
# keeps all three lists naming the same set.

# Short enough to read at a glance, because the pin is the whole decision -
# the installer's prompt has room for the longer wording, a drop-down has not.
# Nothing is marked as recommended: which one is right is decided by where the
# strip's data line is soldered, so a hint here reads as a judgement on the
# board and sends people looking for a difference that is not there.
FIRMWARE_ENVS = (
    ("ESP8266 - GPIO2", "nodemcuv2"),
    ("ESP8266 - GPIO14 / D5", "esp8266_gpio14"),
    ("ESP32 - GPIO16", "esp32dev"),
    ("ESP32-S3 - GPIO16", "esp32s3"),
    ("ESP8266 D1 mini - GPIO2", "d1_mini"),
)


def flash_firmware_command(source_dir, environment):
    """Flash the ESP with one of the shipped firmware builds.

    Privileged for one reason only: the service holds the serial port and has
    to let go of it. PlatformIO itself runs back as the caller, since the
    toolchains live in their home - see the script.
    """
    return ["pkexec", os.path.join(source_dir, "scripts", "flash-firmware.sh"),
            environment]


def restart_watchers_command():
    """Restart both user units so they re-read the configuration.

    Unprivileged and separate on purpose: they are *user* units, out of the
    privileged helper's reach - but the panel already runs as that user.

    Both of them, not just the achievement watcher. The phone bridge reads
    the same file and was never restarted here, so the panel's own switch did
    not take: turning the phone flashes off left the bridge running until the
    next reboot, and turning them back on left it stopped where it had exited
    on the old setting - which is exactly what the new status check then
    reported, correctly, as a problem the panel had caused.
    """
    return ["systemctl", "--user", "restart", WATCHER, PHONE_BRIDGE]


def notify_command(kind):
    """Flash the bar. Deliberately unprivileged: the FIFO is world-writable."""
    return [BINARY, "--notify", kind]


# What the shape buttons flash. Steam's own blue: the point of that row is the
# shape, so the colour should be the same for all of them and belong to none
# of the notifications - otherwise you are comparing two things at once.
SHAPE_TEST_COLOUR = "#1a9fff"


def shape_test_command(shape, colour=SHAPE_TEST_COLOUR):
    """Flash one shape without configuring it first.

    The service takes "shape:colour" for exactly this: choosing between five
    shapes by editing the config and restarting for each one is the wrong way
    round.
    """
    return notify_command("%s:%s" % (shape, colour))


def self_test_command(source_dir, seconds=12):
    """Drive test patterns on the strip.

    Privileged not only for the serial port: the service holds it exclusively,
    so the helper has to stop it, run the test and start it again.
    """
    return ["pkexec", os.path.join(source_dir, "scripts", "self-test.sh"),
            str(seconds)]


def steam_check_command():
    """Must run as the user: Steamworks talks to their Steam client."""
    return [BINARY, "--steam-check"]


def probe_messages_command():
    return [BINARY, "--probe-messages"]


def temperature_command():
    """List the machine's sensors and what the gauge makes of them."""
    return [BINARY, "--temperature"]


def load_command():
    """Show which CPU and GPU load counters this machine has."""
    return [BINARY, "--load"]


# -- notification colours --------------------------------------------------
#
# (label, value) pairs; the value goes in the config file, which takes any
# colour. These are the offered ones, not the possible ones - first is the
# default the service ships with.

# One list for every notification, and a short one: eight hues round the wheel
# and white. It used to be a handful per kind - gold and bronze for an
# achievement, a teal for a friend - which read well in the source and came out
# of the menu as eighteen near-neighbours you had to compare swatch by swatch.
#
# These are the offered colours, not the possible ones. The config file takes
# any colour at all, and so does the trigger; a strip is also not a screen, and
# the difference between a bronze and a gold on one is not the difference it is
# in a paint chart.
NOTIFICATION_COLOURS = (
    ("Red", "#ff0000"),
    ("Orange", "#ff8000"),
    ("Yellow", "#ffff00"),
    ("Green", "#00ff00"),
    ("Cyan", "#00ffff"),
    ("Blue", "#0000ff"),
    ("Purple", "#8000ff"),
    ("Magenta", "#ff00ff"),
    ("White", "#ffffff"),
)

# Offered when a colour is picked outright rather than set - the Test page's
# "flash a colour", where trying an odd one is the whole point. The wheel
# first, then the ones worth having a name for.
EXTRA_COLOURS = (
    ("Steam blue", SHAPE_TEST_COLOUR),
    ("WhatsApp green", "#25d366"),
    ("Signal blue", "#3a76f0"),
    ("Gold", "#ffd700"),
    ("Amber", "#ffa000"),
)


def palette():
    """Colours to offer when one is being picked outright, in a sensible order.

    The ones a notification can be set to come first - they are the wheel, and
    what somebody reaching for "make it go red" wants - then the few worth
    having a name for. Anything else is typed in: the config file takes any
    colour and so does the trigger.
    """
    offered = []
    for label, value in NOTIFICATION_COLOURS + EXTRA_COLOURS:
        if value.lower() not in {had.lower() for _name, had in offered}:
            offered.append((label, value))
    return tuple(offered)



# -- the flash shapes ------------------------------------------------------
#
# Not a list of its own: the service registers them, and a shape the panel
# does not offer would be one nobody ever finds.

def rainbow_choices(names):
    """Menu entries for what the rainbow slot shows, in the service's order.

    Same arrangement as the shapes: the service owns the list, and anything
    it grows shows up here rather than needing to be named twice. Only the
    wording lives here, because "load" on its own does not say load of what.
    """
    labels = {
        "rainbow": "Steam's rainbow",
        "temperature": "Temperature",
        "load": "CPU and GPU load",
        "fire": "Fire",
        "aurora": "Aurora",
    }
    return tuple((labels.get(name, name.capitalize()), name) for name in names)


def desktop_choices(names):
    """Menu entries for what the bar shows in Desktop Mode.

    Same arrangement as the two above: the service owns the list and only the
    wording is here. "steam" needs the most of it - what it stands for is the
    bar carrying on with whatever the last Game Mode session left, which is
    what it did before this page existed.
    """
    labels = {
        "steam": "Leave it to Steam",
        "off": "Off",
        "color": "One colour",
    }
    return tuple((labels.get(name, name.capitalize()), name) for name in names)


def style_choices(styles, inherit=None):
    """Menu entries for the flash shapes, in the order the service has them.

    `inherit` is the value that means "whatever the general setting says";
    passing it adds that entry at the top, which is what the per-notification
    menus need and the general one must not have.
    """
    entries = [(name.replace("_", " ").capitalize(), name) for name in styles]
    if inherit is None:
        return tuple(entries)
    return tuple([("Same as default", inherit)] + entries)


# -- menus whose entries are not what gets written -------------------------
#
# A sensor is a path into /sys and a colour is six hex digits - neither is
# something to show someone. These two translate between label and value.


def menu_label(choices, value):
    """The entry for a configured value, or None if the menu lacks it."""
    for label, known in choices:
        if known == value:
            return label
    for label, known in choices:
        if known.lower() == value.lower():      # #CD7F32 is #cd7f32
            return label
    return None


def menu_value(choices, label):
    """Back from an entry to what the config file wants.

    An entry nobody put there is its own value - that is how a hand-written
    setting the menu does not offer stays what it was.
    """
    for known, value in choices:
        if known == label:
            return value
    return label


# -- the temperature sensor menu ------------------------------------------
#
# The setting is a path into /sys, so the machine is asked what it has and
# the answer becomes the menu.


def read_sensors():
    """Every temperature sensor on this machine, and the best of them."""
    sensors = temperature.find_sensors()
    return sensors, temperature.pick_sensor(sensors)


def sensor_label(sensor):
    """One sensor, as a menu line: the chip and what it measures.

    Deliberately not its reading, which would be stale the moment the menu
    opened. `--temperature` is where to look at what they say.
    """
    name = sensor.get("label") or os.path.basename(
        sensor["path"]).replace("_input", "")
    return "%s %s" % (sensor.get("chip") or "?", name)


def sensor_choices(sensors, chosen=None, current="auto"):
    """(label, value) pairs for the menu, best answer first.

    Automatic leads, and names what it landed on so the choice is not a
    mystery. The rest follow in the order it ranked them.
    """
    automatic = "Automatic"
    if chosen is not None:
        automatic += " (%s)" % sensor_label(chosen)
    choices = [(automatic, "auto")]

    for sensor in sorted(sensors, key=lambda entry: entry["rank"]):
        choices.append((sensor_label(sensor), sensor["path"]))

    if current and current not in [value for _label, value in choices]:
        # Configured by hand and not there any more - an eGPU unplugged, a
        # driver unloaded, a typo. Showing it says what the service is set to;
        # dropping it would look like the setting had changed by itself.
        choices.append(("%s (not found)" % current, current))
    return _uniquify(choices)


def _uniquify(choices):
    """Pull apart labels that would otherwise be the same line twice.

    Two inputs on one chip can describe themselves identically, and the menu
    is keyed on what it shows - so a repeated line makes one unreachable.
    """
    counts = {}
    for label, _value in choices:
        counts[label] = counts.get(label, 0) + 1
    return [(label if counts[label] == 1 else "%s [%s]" % (label, _where(value)),
             value) for label, value in choices]


def _where(path):
    """A sensor's place in /sys, short: "hwmon1/temp2"."""
    directory, name = os.path.split(path)
    return "%s/%s" % (os.path.basename(directory), name.replace("_input", ""))
