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

from steamos_led import cec as cec_module
from steamos_led import config as config_module
from steamos_led import lact as lact_module
from steamos_led import phone
from steamos_led import power as power_module
from steamos_led import temperature

INSTALL_DIR = "/var/lib/steamos-led-serial"
BINARY = os.path.join(INSTALL_DIR, "steamos-led-serial")
CONFIG_PATH = "/etc/steamos-led-serial.conf"
UNIT_PATH = "/etc/systemd/system/steamos-led-serial.service"
UDEV_PATH = "/etc/udev/rules.d/99-steamos-led-serial.rules"

# Not something that should be installed - something that should not be left
# behind. scripts/install-cec.sh lends the desktop user a sudo rule for the
# length of a CEC install and removes it in a trap; the one way out that trap
# cannot cover is the signal it cannot see. Nothing else in the window would
# ever mention it, so the checklist is where it belongs.
CEC_INSTALL_RULE = "/etc/sudoers.d/zz-steamos-led-cec-install"
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

    def __init__(self, name, ok, detail="", repairable=False, live=False):
        self.name = name
        self.ok = ok
        self.detail = detail
        # Whether re-running the installer would put this right. A missing
        # kernel module would; an unplugged ESP would not.
        self.repairable = repairable
        # Whether this one is about the bar being driven *now* rather than
        # about the installation being complete. Marked here rather than
        # matched by name somewhere else: the foot of the window asks a
        # narrower question than the repair summary does, and a check's label
        # is a sentence for people to read, not a key to look it up by.
        self.live = live

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
        repairable=True, live=True))

    checks.append(Check(
        "Service running", probe.unit_active(SERVICE),
        "systemctl status %s says why" % SERVICE, repairable=True,
        live=True))

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

    # Only when it is there, and it never should be. Listed unconditionally it
    # would be a green line about HDMI CEC on every machine that has never
    # installed it, which is a checklist reporting on something that is not
    # part of the installation it is checking.
    if probe.exists(CEC_INSTALL_RULE):
        checks.append(Check(
            "No sudo rule left over from a CEC install", False,
            "%s is still there from an HDMI CEC install that was killed part "
            "way through. It grants this user five programs as root and "
            "nothing needs it now - installing or removing HDMI CEC again "
            "clears it, or delete the file." % CEC_INSTALL_RULE,
            # Rebuild and reinstall is about the LED service and would not
            # touch it, so offering that button here would be a promise the
            # button does not keep.
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


def bar_is_live(checks):
    """Whether the bar is actually being driven, as far as the checks see.

    A narrower question than repair_summary answers, and deliberately so. The
    summary is about the installation being whole - a missing menu entry
    counts against it - and the foot of the window is about whether anything
    is driving the strip at all. The two disagree often and both are right:
    an installation with a stale menu entry is imperfect and lit.

    None when nothing in the list can answer, which is not the same as a fault:
    a caller can then say it is still looking rather than report a failure it
    has not established.
    """
    found = [check.ok for check in checks if check.live]
    return all(found) if found else None


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


# -- the state of every part -----------------------------------------------
#
# This toolbox installs four things now - the LED service, a CPU power unit, a
# whole HDMI CEC toolkit and a line in somebody's environment.d - and until
# this layer existed it reported on exactly one of them while calling that
# page "this installation". The others said their piece on their own settings
# pages, in their own shapes, and nowhere together.
#
# So every part is described the same way here and read from one place: the
# status page draws these, the headline counts them, and neither knows how any
# particular part is checked.


class Part:
    """One installable thing, and how it is doing.

    `ok` has three values on purpose. True and False are what you expect;
    None is "not installed", which is not a fault - a machine that never
    wanted HDMI CEC is not a machine with a problem, and counting it as one
    would put a permanent red number over every page.
    """

    def __init__(self, key, name, ok, verdict, detail=(), repair=""):
        self.key = key
        self.name = name
        self.ok = ok
        # One line, for the block's own heading. What is wrong rather than
        # that something is: "no adapter" beats "not working".
        self.verdict = verdict
        # The Checks or lines behind it, shown when the block is unfolded.
        self.detail = list(detail)
        # Which repair belongs to this part, if any. Named rather than a
        # callable so this module stays free of the window - see the panel's
        # PART_REPAIRS for what each name does.
        self.repair = repair

    @property
    def installed(self):
        return self.ok is not None

    def __repr__(self):                                     # pragma: no cover
        return "<Part %s %s>" % (self.key, self.ok)


# What each part's repair button says, keyed by Part.repair. Here rather than
# in the window so a part that gains a repair cannot gain a button with no
# label, and so the tests can check every name resolves.
REPAIR_LABELS = {
    "reinstall": "Rebuild and reinstall",
    "install-cec": "Reinstall HDMI CEC",
}


def led_part(checks):
    """The LED service, from the checklist that was this page's only content."""
    problems = broken(checks)
    if not problems:
        verdict = "Installed and running."
    elif any(check.name.startswith("Kernel module") for check in problems):
        # The one a SteamOS update reliably causes, and the one with an
        # answer, so it is worth saying instead of the count.
        verdict = ("The kernel module is missing for the running kernel - a "
                   "SteamOS update brings a new one, and reinstalling builds "
                   "it again.")
    else:
        verdict = "%d of %d checks failed." % (len(problems), len(checks))
    return Part("led", "LED bar", not problems, verdict, checks,
                repair="reinstall")


def power_part(current, available):
    """The CPU governor, if this machine is being told what to do at all.

    Not installed rather than broken when nothing is set: leaving the CPU as
    SteamOS had it is this project's default and the ordinary state, so a
    machine that never touched it has nothing to report.
    """
    governor = (current or {}).get("CPU_GOVERNOR", "")
    if not governor:
        return Part("power", "CPU power", None, "Left as SteamOS set it.")
    running = (available or {}).get("current", {}).get("CPU_GOVERNOR", "")
    detail = ["Wanted: %s" % governor, "Running: %s" % (running or "unknown")]
    epp = (current or {}).get("CPU_EPP", "")
    if epp:
        detail.append("Energy preference: %s" % epp)
    if running and running != governor:
        return Part("power", "CPU power", False,
                    "Set to %s, but the CPU is running %s."
                    % (governor, running), detail)
    return Part("power", "CPU power", True, "Running %s." % governor, detail)


def cec_part(status, installed):
    """HDMI CEC: is the toolkit there, and can it reach the television."""
    if not installed:
        return Part("cec", "HDMI CEC", None, "Not installed.")
    if status is None:
        return Part("cec", "HDMI CEC", False,
                    "Installed, but it will not say how it is.",
                    repair="install-cec")
    device = cec_module.device(status)
    on = [name for name, _k, _l, _s in cec_module.FEATURES
          if cec_module.feature_on(status, name)]
    detail = ["Adapter: %s" % (device.get("device") or "none configured"),
              "Features on: %s" % (", ".join(on) if on else "none")]
    if not cec_module.usable(status):
        return Part("cec", "HDMI CEC", False,
                    "%s cannot be reached, so nothing can be sent."
                    % (device.get("device") or "The adapter"),
                    detail, repair="install-cec")
    return Part("cec", "HDMI CEC", True,
                "Ready on %s, %d feature(s) on."
                % (device.get("device"), len(on)), detail)


def gpu_part(state, error=""):
    """The graphics card, when there is a daemon that owns it.

    Not installed when LACT is not running, which is most machines - it is
    somebody else's tool and nothing here installs it, so its absence is not a
    fault and is not counted as one.
    """
    if error:
        return Part("gpu", "Graphics card", False, error)
    if state is None:
        return Part("gpu", "Graphics card", None, "LACT is not running.")
    if not state.get("gpu"):
        return Part("gpu", "Graphics card", False,
                    "LACT is running, but reports no graphics card.")
    fan = lact_module.fan(state.get("config") or {})
    knobs = gpu_knobs(state)
    detail = ["Card: %s" % (state.get("name") or state["gpu"]),
              "Settable here: %s" % (", ".join(knob["label"]
                                               for knob in knobs) or "nothing")]
    if state.get("profile"):
        detail.append("Profile: %s" % state["profile"])
    detail.append("Fan: %s" % ("under LACT's control" if fan["enabled"]
                               else "left to the card's firmware"))
    return Part("gpu", "Graphics card", True,
                gpu_summary(state), detail)


def layout_part(layout, labels=None):
    """The keyboard layout, which is a setting rather than an installation.

    Never False. There is nothing here that can be broken - a line is in a
    file or it is not - so it reports set or unset and never counts against
    the window's summary.
    """
    if not layout:
        return Part("keyboard", "Keyboard layout", None,
                    "Left to the system.")
    named = (labels or {}).get(layout, layout)
    return Part("keyboard", "Keyboard layout", True, named,
                ["Applies to Game Mode at the next login."])


def panel_part(version, update_state=None, update_said=""):
    """The program itself. Always installed - you are looking at it."""
    # Named at call time rather than imported above it: this block sits
    # before the update constants in the file, and moving it below them would
    # put the parts a long way from the checks they are built out of.
    if update_state == UPDATE_AVAILABLE:
        return Part("panel", "This panel", True,
                    "Version %s. %s" % (version, update_said or
                                        "An update is available."))
    return Part("panel", "This panel", True, "Version %s." % version)


def parts_summary(parts):
    """One sentence for the top of the window, over every part.

    Counted across all of them rather than over the LED checklist alone,
    which is what it used to say - so a machine whose CEC adapter had gone
    read "Everything is in order" while the CEC page said otherwise, and the
    LED bar's own count was printed over pages that have nothing to do with
    it.

    Parts that are not installed are not counted. A machine that never wanted
    HDMI CEC is not a machine with a problem.
    """
    problems = [part for part in parts if part.ok is False]
    if not problems:
        return "Everything is in order."
    if len(problems) == 1:
        # One thing wrong is worth naming; the sentence is then the same one
        # its own block shows, which is where you are about to look.
        return "%s: %s" % (problems[0].name, problems[0].verdict)
    return "%d problems: %s." % (len(problems),
                                 ", ".join(part.name for part in problems))


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


# The two the load gauge ships with. Offered beside the wheel because they are
# not on it: they were picked to sit about as far apart as two colours on a
# strip can, which is what makes the two halves read as two - so without them
# here, putting back what you started with would mean typing six hex digits.
# Named apart from the amber in EXTRA_COLOURS, which is a different shade for a
# different picker.
LOAD_DEFAULT_COLOURS = (
    ("Deep amber", "#ff6e00"),
    ("Steam blue", SHAPE_TEST_COLOUR),
)


def load_colours():
    """Colours to offer for the load gauge's two halves, best answers first.

    The shipped pair leads - one of them is what each row is already set to,
    and a menu opening on six hex digits reads as a setting nobody chose -
    then the wheel the notifications use.
    """
    offered = []
    for label, value in LOAD_DEFAULT_COLOURS + NOTIFICATION_COLOURS:
        if value.lower() not in {had.lower() for _name, had in offered}:
            offered.append((label, value))
    return tuple(offered)


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

    The four this project added are worded as the rainbow slot words them, and
    "rainbow" is Steam's own for the same reason it is up there: here they are
    five separate scenes rather than one slot with a tenant, and a menu
    offering "Rainbow" next to "Fire" should not leave anybody wondering
    whether the first of them is the second in disguise.
    """
    labels = {
        "steam": "Leave it to Steam",
        "off": "Off",
        "color": "One colour",
        "rainbow": "Steam's rainbow",
        "load": "CPU and GPU load",
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


POWER_CONFIG_PATH = "/etc/steamos-led-power.conf"


def read_power_config(path=None):
    """The CPU settings file as {key: value}, defaults for what is missing.

    Its own small reader rather than config_module.load: that one is the LED
    service's, it validates against the service's own options, and it would
    refuse a file made only of settings it has never heard of.
    """
    values = dict(power_module.DEFAULTS)
    try:
        with open(path or POWER_CONFIG_PATH,
                  encoding="utf-8", errors="replace") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return values
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, sign, value = line.partition("=")
        if sign and name.strip() in values:
            values[name.strip()] = value.strip().strip("\"'")
    return values


def power_config_text(values):
    """Those settings as a file, keeping the shipped one's comments.

    The same shape as the LED config's update_text: what is written back is
    the file that was there with its values replaced, so the explanation of
    what a governor is survives every Apply.
    """
    lines = []
    for key in sorted(power_module.DEFAULTS):
        lines.append("%s=%s" % (key, values.get(key,
                                                power_module.DEFAULTS[key])))
    return "\n".join(lines) + "\n"


def apply_power_command(source_dir, staged_path):
    """Install the CPU settings and put them into effect, in one prompt."""
    return ["pkexec", os.path.join(source_dir, "scripts", "apply-power.sh"),
            staged_path]


def power_choices(offered, current="", labels=None, unset=True):
    """(label, value) pairs for a CPU setting, from what the machine offers.

    Never from a list here. Which governors and which preferences exist
    depends on the cpufreq driver and the mode it is in - see power.py - so a
    menu written down would be a menu that is wrong on somebody's machine.

    "Leave it alone" leads, because it is the default and because it is the
    entry that undoes the others. A value set in the config file that this
    machine does not offer is kept and marked, the same way a missing sensor
    is: dropping it would look like the setting had changed by itself.
    """
    labels = labels or {}
    # `unset` is False for the preference, which has no such entry: it is only
    # ever written alongside a governor, so "leave the file alone" is not
    # something it can mean - either the CPU is being managed here or it is
    # not, and the governor is where that is said.
    choices = [("Leave it to SteamOS", "")] if unset else []
    for value in offered:
        choices.append((labels.get(value, value), value))
    if current and current not in [value for _label, value in choices]:
        choices.append(("%s (not offered here)" % current, current))
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


# -- HDMI CEC ----------------------------------------------------------------
#
# The CEC work itself is the vendored SteamOS CEC Toolkit and the reading of
# it is server/steamos_led/cec.py. What belongs here is the same thing the
# rest of this file holds: the commands the window runs, in one place, so a
# window that is hard to drive under a test is not also the only record of
# what it would have executed.


def install_cec_command(source_dir, remove=False):
    """Install or remove the CEC toolkit, in one prompt.

    Through a script of ours rather than straight at the toolkit's installer,
    because that installer refuses to run as root and wants sudo about forty
    times - see scripts/install-cec.sh for what stands between the two.
    """
    return ["pkexec", os.path.join(source_dir, "scripts", "install-cec.sh"),
            "remove" if remove else "install",
            cec_module.source_dir(source_dir)]


def cec_status(home=None, run=None):
    """Ask the installed toolkit about itself. None when it is not installed.

    None rather than an empty status, and the difference is the whole of what
    the page does next: not installed is a page offering to install, and
    installed-but-broken is a page saying what is wrong. An empty dictionary
    would look like the second while meaning the first.
    """
    if not cec_module.installed(home):
        return None
    runner = run or _run_quietly
    done = runner(cec_module.status_command(home))
    if done is None:
        return None
    return cec_module.read_status(done)


def _run_quietly(command):
    """Run something short and hand back its output, or None if it would not.

    Used for the status read, which happens on every visit to the page and on
    a timer - so it is the one command here that must not put a line in the
    log or a warning in the status bar each time the toolkit is mid-restart.
    """
    try:
        done = subprocess.run(command, capture_output=True, text=True,
                              timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


# -- the graphics card, through LACT -----------------------------------------
#
# Read in one go rather than a call at a time: five short round trips to a
# local socket, and a page built from four of them separately would draw
# itself from four moments. What comes back is one document, the same shape
# the CEC page gets, so everything downstream is a pure function of it.


def gpu_state(path=None, ask=None):
    """Everything the GPU block needs, or None when there is no daemon.

    None rather than an empty document, and the difference is the whole of
    what the page does with it: no daemon means the block is not drawn at all,
    where an empty document would mean a card that reports nothing - which is
    a page of empty sliders.
    """
    where = path or lact_module.SOCKET_PATH
    if not lact_module.available(where):
        return None
    talk = ask or (lambda name, args=None: lact_module.talk(name, where, args))
    # A LactError from any of these reaches the caller. The page turns it into
    # a block that says the daemon would not answer, which is a different
    # thing from there being no daemon and has to stay different.
    found = talk("list_devices")
    devices = list(found) if isinstance(found, list) else []
    if not devices:
        # A daemon with no card is a daemon with nothing to configure -
        # reported as a state of its own so the page can say so rather than
        # showing an empty card picker.
        return {"gpu": "", "name": "", "devices": []}
    gpu = devices[0].get("id", "")
    state = {
        "gpu": gpu,
        "name": devices[0].get("name", ""),
        "devices": devices,
        "config": talk("get_gpu_config", {"id": gpu}) or {},
        "stats": talk("device_stats", {"id": gpu}) or {},
        "clocks": talk("device_clocks_info", {"id": gpu}) or {},
    }
    # Profiles on their own: an older daemon that does not have them should
    # cost the page its profile picker, not the whole block.
    try:
        state["profiles"], state["profile"] = lact_module.profiles(where)
    except lact_module.LactError:
        state["profiles"], state["profile"] = [], ""
    return state


def gpu_knobs(state):
    """The sliders to draw, from what the card reported."""
    if not state or not state.get("gpu"):
        return []
    return lact_module.offered(state.get("config") or {},
                               state.get("clocks") or {},
                               state.get("stats") or {})


def gpu_summary(state):
    """One line about the card, for the status page and the block's heading."""
    if state is None:
        return "LACT is not running."
    if not state.get("gpu"):
        return "LACT is running, but reports no graphics card."
    knobs = gpu_knobs(state)
    fan = lact_module.fan(state.get("config") or {})
    said = state.get("name") or state["gpu"]
    if not knobs:
        return "%s - nothing on it can be set from here." % said
    return "%s, %d setting(s)%s." % (said, len(knobs),
                                     ", fan under control" if fan["enabled"]
                                     else "")
