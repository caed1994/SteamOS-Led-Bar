# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""What the control panel knows, minus the widgets.

Kept free of tkinter on purpose: the interesting parts are "what is broken"
and "what fixes it", and those are worth testing without a display.
"""

from __future__ import annotations

import getpass
import os
import platform
import re
import subprocess

from steamos_utility_center import cec as cec_module
from steamos_utility_center import ctl as ctl_module
from steamos_utility_center import config as config_module
from steamos_utility_center import lact as lact_module
from steamos_utility_center import phone
from steamos_utility_center import power as power_module
from steamos_utility_center import temperature

INSTALL_DIR = "/var/lib/steamos-utility-center"
BINARY = os.path.join(INSTALL_DIR, "steamos-utility-center")
CONFIG_PATH = "/etc/steamos-utility-center.conf"
UNIT_PATH = "/etc/systemd/system/steamos-utility-center.service"
UDEV_PATH = "/etc/udev/rules.d/99-steamos-utility-center.rules"

# This file must not stay on disk. The checklist does not report it as an
# installed item. scripts/install-cec.sh gives the desktop user a sudo rule
# for the time of a CEC install, and a trap removes the rule. A signal that
# the trap cannot see is the one exit that the trap does not cover. No other
# part of the window reports this file, so the checklist reports it.
CEC_INSTALL_RULE = "/etc/sudoers.d/zz-steamos-utility-center-cec-install"
SHIM_DEVICE = "/dev/valve-leds-shim"
MODULE_NAME = "leds-valve-shim"
SERVICE = "steamos-utility-center.service"
WATCHER = "steamos-utility-center-achievements.service"
PHONE_BRIDGE = "steamos-utility-center-phone.service"

# The time to wait for an answer from KDE Connect. After this time, the panel
# reports no answer. This time is shorter than the wait of the service,
# because the panel asks on the thread that draws the window. The two normal
# answers come back in some milliseconds: the name is on the bus, or the bus
# has no such name. The wait is only for a machine where the call stops.
PHONE_ASK_SECONDS = 2.0


# The panel's own icon, next to the panel. A theme icon name is the fallback
# because a missing icon file leaves a menu entry with no picture at all.
ICON_NAME = "steamos-utility-center-panel.png"
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
        """Reports whether the systemd of this user runs with no open session.

        Without this setting, a change to Game Mode ends the session and stops
        each user service. The two services of this project stop also, and
        both must stay alive across that change.

        This function asks about one *given* user, by number. Without a user,
        loginctl answers about a different subject and does not give Linger.
        Then this function reported "no" on a machine that answered "yes" to
        the same question with the user in it.
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
        """Returns the phones that KDE Connect gives, or None for no answer.

        The two conditions give the same symptom: the bar never flashes. But
        they are two different faults, so this function separates them. In the
        first fault no program forwards the notifications. In the second fault
        a program runs, but no phone is connected to it.

        This function does not start KDE Connect. A status check reports the
        condition and starts no program.
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

    fifo = (config or {}).get("NOTIFY_FIFO") or "/run/steamos-utility-center/notify"
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

    # Report this only when the file is on disk, and it must never be on
    # disk. A line that is always in the list gives a green line about HDMI
    # CEC on each machine without HDMI CEC. The checklist then reports on a
    # part that is not in the installation that it examines.
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
# This toolbox now installs four parts: the LED service, a CPU power unit, a
# complete HDMI CEC toolkit, and a line in the environment.d of the user.
# Before this layer, the page reported on one part only, and it still had the
# name "this installation". The other parts reported on their own settings
# pages, in their own forms, and never together.
#
# So this layer describes each part in the same form, and one place holds
# them. The status page draws them, and the headline counts them. Neither of
# the two knows the method that examines one part.


class Part:
    """Holds one part that a user can install, and its condition.

    `ok` has three values on purpose. True and False have the normal meaning.
    None means "not installed", and that is not a fault. A machine without
    HDMI CEC has no problem. A count of it as a problem puts a permanent red
    number on each page.
    """

    def __init__(self, key, name, ok, verdict, detail=(), repair=""):
        self.key = key
        self.name = name
        self.ok = ok
        # One line, for the heading of the block. Give the fault and not the
        # fact of a fault: "no adapter" is better than "not working".
        self.verdict = verdict
        # The Checks or lines behind it, shown when the block is unfolded.
        self.detail = list(detail)
        # The repair for this part, if there is one. This is a name and not a
        # function, so that this module needs no part of the window. See
        # PART_REPAIRS in the panel for the action of each name.
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
    """Returns the CPU governor, if this project controls this machine.

    With no setting, the result is "not installed" and not "broken". This
    project keeps the CPU as SteamOS set it, and that is the default and the
    normal condition. A machine with no change to the CPU has nothing to
    report.
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


def features_on(status):
    """The CEC features currently switched on, by name."""
    return [name for name, _kind, _label, _said in cec_module.FEATURES
            if cec_module.feature_on(status, name)]


def adapter_gone_cost(status):
    """Returns the cost of the features that stay on with no adapter.

    A user removed the adapter and connected a normal monitor. The machine
    then needed one and a half minutes more to start, and no message gave the
    reason. Each feature was still on, and the wake service of the toolkit
    did not know about the removed adapter. That service waits eight seconds
    for the device and then twelve seconds for a logical address. It repeats
    this four times before it stops and lets the session start.

    The result is "" for each condition but one: the adapter does not answer
    *and* a feature is on. An adapter that is out, with each feature off,
    costs nothing.
    """
    if status is None or cec_module.usable(status):
        return ""
    on = features_on(status)
    if not on:
        return ""
    return ("%d HDMI CEC feature%s still switched on, and the adapter cannot "
            "be reached. Every start then spends over a minute trying to "
            "reach a television that is not there before giving up. Turn "
            "them off while the adapter is out; switching them back on when "
            "it returns costs nothing."
            % (len(on), " is" if len(on) == 1 else "s are"))


def cec_part(status, installed, source_dir=None):
    """HDMI CEC: is the toolkit there, can it reach the television, and is it
    the one that this clone carries."""
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
    if cec_module.running_version(status):
        detail.append("Version: %s" % cec_module.running_version(status))
    if not cec_module.usable(status):
        # The cost of this condition goes into the detail and not into the
        # verdict. The verdict is one line in a headline. This text is the
        # paragraph to read after the user opens the block.
        cost = adapter_gone_cost(status)
        return Part("cec", "HDMI CEC", False,
                    "%s cannot be reached, so nothing can be sent."
                    % (device.get("device") or "The adapter"),
                    detail + ([cost] if cost else []), repair="install-cec")

    # An old toolkit is not a fault, and it is not "ready" either.
    #
    # Nothing compared the two before. update.sh brought a newer cec-toolkit/
    # into the clone, the installer did not touch the toolkit, and the copy on
    # the machine stayed as old as it was. It answered every question, so the
    # page reported it as ready, and the five fixes of this fork were not on
    # the machine.
    if source_dir and cec_module.out_of_date(status, source_dir):
        return Part("cec", "HDMI CEC", False,
                    "Ready on %s, and older than this clone: %s against %s."
                    % (device.get("device"),
                       cec_module.running_version(status),
                       cec_module.clone_version(source_dir)),
                    detail + ["Install it again on the HDMI CEC page, or "
                              "press Rebuild and reinstall."],
                    repair="install-cec")
    return Part("cec", "HDMI CEC", True,
                "Ready on %s, %d feature(s) on."
                % (device.get("device"), len(on)), detail)


def gpu_part(state, error="", available=None, asked=True):
    """Returns the graphics card, when a daemon controls it.

    The result is "not installed" when LACT does not run, and that is the
    condition of most machines. LACT is the tool of another project, and
    nothing here installs it. Its absence is not a fault, and this function
    does not count it as one.

    `available` is the socket of the daemon, which is a file test. `asked`
    tells whether the window spoke to the daemon already.

    Both are here because `state is None` had three meanings and this function
    read it as one. No daemon, a daemon that gives no answer, and a window
    that did not ask are three different things. The page reported all three
    as "LACT is not running." A person whose card was under LACT's control
    read that on the status page, while the page beside it set the card.
    """
    if error:
        return Part("gpu", "Graphics card", False, error)
    if available is False:
        return Part("gpu", "Graphics card", None, "LACT is not running.")
    if state is None and available and not asked:
        return Part("gpu", "Graphics card", None,
                    "Looking for the graphics card...")
    if state is None and available:
        return Part("gpu", "Graphics card", False,
                    "LACT is running and did not answer.")
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
    """Returns the keyboard layout, which is a setting and not an install.

    The result is never False. Nothing here can be broken, because a line is
    in a file or it is not in a file. So this function reports set or unset,
    and it never counts against the summary of the window.
    """
    if not layout:
        return Part("keyboard", "Keyboard layout", None,
                    "Left to the system.")
    named = (labels or {}).get(layout, layout)
    # This text is in the line and not behind a fold. There is one sentence
    # to say about a keyboard layout. To put one sentence behind a Details
    # button, which the user must find and click, is worse than a line that
    # is a little longer.
    return Part("keyboard", "Keyboard layout", True,
                "%s. Applies to Game Mode at the next login." % named)


def panel_part(version, update_state=None, update_said="", behind="",
               installed=None, head=""):
    """Returns the program itself. It is always installed and on the screen.

    `behind` is the sentence of install_is_behind(), and it is the one item
    here that counts as a fault. A pull and an install are two steps, and the
    window showed the first step only. A clone some commits in front of the
    running copy then looked the same as a machine at the current version. So
    a correction in the clone did not run, and each user read the log of the
    old copy.
    """
    # Show both commits. The question for this block is which code runs now.
    # A version number does not answer that question. It changes when a
    # person changes it, and it never changes between two commits on the
    # same day.
    installed = installed_commit() if installed is None else installed
    detail = []
    if installed:
        detail.append("Installed from: %s" % installed[:12])
    if head:
        detail.append("This clone: %s%s"
                      % (head[:12],
                         "" if head == installed else "  (not installed yet)"))

    # Named at call time rather than imported above it: this block sits
    # before the update constants in the file, and moving it below them would
    # put the parts a long way from the checks they are built out of.
    if behind:
        return Part("panel", "This panel", False, behind, detail)
    if update_state == UPDATE_AVAILABLE:
        return Part("panel", "This panel", True,
                    "Version %s. %s" % (version, update_said or
                                        "An update is available."), detail)
    if installed:
        return Part("panel", "This panel", True,
                    "Version %s, installed from %s."
                    % (version, installed[:7]), detail)
    return Part("panel", "This panel", True, "Version %s." % version, detail)


# Which part answers for which section, where the two are not the same word.
# Only the pages that have one: Status shows every block already, and App
# Settings answers for the panel through its own block.
SECTION_PARTS = {"strip": "led", "cec": "cec", "keyboard": "layout"}


def summary_for(parts, section=""):
    """Returns the sentence for one page, and not always the global one.

    A user reported this: the HDMI CEC page said "Everything is in order."
    below its own title, and above a card with the text "Not installed yet".
    Both texts were correct. The sentence counts each part, and a part that is
    not installed is not a problem. A machine without CEC is not a broken
    machine. But a global answer below the heading of a section reads as an
    answer about that section.

    So the part of the page gives the sentence when its condition is not good.
    A page with no part of its own gets the count across all parts.
    """
    key = SECTION_PARTS.get(section, section)
    mine = next((part for part in parts if part.key == key), None)
    if mine is not None and mine.ok is not True:
        return "%s: %s" % (mine.name, mine.verdict)
    return parts_summary(parts)


def parts_summary(parts):
    """Returns one sentence for the top of the window, over each part.

    This counts each part. Before, it counted the LED checklist only. A
    machine with a removed CEC adapter then showed "Everything is in order",
    and the CEC page showed the opposite. The count of the LED bar also stood
    over pages with no connection to the LED bar.

    This function does not count a part that is not installed. A machine
    without HDMI CEC is not a machine with a problem.
    """
    problems = [part for part in parts if part.ok is False]
    if not problems:
        return "Everything is in order."
    if len(problems) == 1:
        # Give the name of one fault. The sentence is then the same sentence
        # that the block of the part shows, and the user opens that block
        # next.
        return "%s: %s" % (problems[0].name, problems[0].verdict)
    return "%d problems: %s." % (len(problems),
                                 ", ".join(part.name for part in problems))


# -- settings profiles -----------------------------------------------------
#
# A profile is the same KEY=value file as the configuration. That makes this
# function small, because the parser, the validator and the formatter are
# already available. A profile holds only the settings that the panel can set.
# The lines for one machine are not in it: the serial port, the baud rate and
# the device. So those lines cannot come from the machine of another user.
#
# A profile is in the clone and not under ~/.config. The user goes to the
# clone for this project. The clone also needs no rights: the panel writes it
# as the user, and the configuration in /etc needs root.
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
    """Returns the path of a profile with that name, for each input.

    The suffix is not a subject for the user. The user gives the name of a
    profile and not the name of a file. So this function adds the suffix when
    the name has none, and it never adds the suffix twice.
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
        "# A settings profile for the SteamOS Utility Center.",
        "# Load it from the panel with \"Load profile\", or copy the lines",
        "# you want into /etc/steamos-utility-center.conf by hand.",
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

# Text that only the error message of pkexec holds. "/dev/tty" was in this
# list before, and it matched the serial port of a firmware flash, which is
# /dev/ttyUSB0. A correct flash then showed advice about Game Mode below it.
NO_AGENT_SIGNS = ("authentication agent", "polkit-agent-helper",
                  "controlling terminal")


def in_game_mode(environ=None):
    """Reports whether this program runs in the Game Mode session of Steam.

    The variables of gamescope give the answer. gamescope is the compositor of
    Game Mode, and it makes the difference that is important here.
    """
    environ = os.environ if environ is None else environ
    if any(marker in environ for marker in GAME_MODE_MARKERS):
        return True
    return "gamescope" in environ.get("XDG_CURRENT_DESKTOP", "").lower()


def looks_like_no_auth_agent(output, exit_code):
    """Reports whether a command failed because no program asked for a password.

    pkexec needs a polkit agent for the question. Game Mode runs no such
    agent. The fallback of pkexec needs a controlling terminal, and a program
    that Steam starts has no terminal. pkexec then exits with 127 and an
    error about /dev/tty.
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


# -- running one of the appliers -------------------------------------------
#
# Two ways in, and the difference is a password.
#
# The installer writes a sudoers rule for the three appliers. Each line of it
# names one program and the one file that program reads, and there is no
# wildcard in it. A run that matches a line needs no password at all.
#
# pkexec is the other way, and it asks. It is what runs when the rule is not
# there: an installation with --no-sudoers, one from before the rule existed,
# or an update that took the rule away in spite of the keep-list. It is also
# what runs for the work that the rule deliberately leaves out, which today is
# the chown of Take ownership. That one walks a whole drive as root, and a
# person answers for it.
#
# The password thus means something again. It was twenty presses a day of
# noise before, on settings that the same rule now permits anyway: the rule is
# on the machine from the moment the installer runs, so a panel that keeps
# asking makes nothing safer. See ctl.sudoers_text.

# The reason a run failed, when the reason is rights. ctl knows the words that
# sudo uses; this is the same question asked about a transcript.
refused_for_rights = ctl_module.refused_for_rights


def applier_command(area, source_dir, script, staged_path, extra=(),
                    ask=False):
    """Returns how to run one applier, with a password or without one.

    Without one needs three things: an installation, the staged file at the
    name the rule permits, and no extra argument. A call with an extra
    argument is a call that no line of the rule matches, so it goes the other
    way from the start rather than being refused first.
    """
    permitted = (not ask and not extra
                 and staged_path == ctl_module.STAGED.get(area)
                 and os.path.exists(ctl_module.APPLIER[area])
                 and os.path.exists(ctl_module.SUDO_RULE))
    if permitted:
        return ["sudo", "-n", ctl_module.APPLIER[area], staged_path]
    return ["pkexec", os.path.join(source_dir, "scripts", script),
            staged_path] + list(extra)


def apply_config_command(source_dir, staged_path, ask=False):
    """Install a prepared config file and restart the service."""
    return applier_command("strip", source_dir, "apply-config.sh",
                           staged_path, ask=ask)


# -- the Game Mode plugin --------------------------------------------------
#
# Decky Loader keeps its plugins under the home directory of the user, so
# nothing here needs root to *look*. To install one does: Decky keeps that
# directory as root and its loader is a system service.

DECKY_HOME = "homebrew"
DECKY_PLUGIN = os.path.join(DECKY_HOME, "plugins", "SteamOS Utility Center")

# The files that Decky reads, and the ones this compares. dist/index.js is
# built and in the repository, because nobody must run npm on a Steam Machine.
DECKY_FILES = ("plugin.json", "main.py", "package.json",
               os.path.join("dist", "index.js"))

# What the machine is, as one word. The page draws a different sentence and a
# different button for each.
DECKY_NONE = "no-decky"          # Decky Loader is not installed
DECKY_ABSENT = "absent"          # Decky is there and the plugin is not
DECKY_OLD = "old"                # installed, and not the files of this clone
DECKY_CURRENT = "current"        # installed, and the same files


def decky_where(home=None):
    """Where Decky keeps this plugin, under the home directory of the user."""
    return os.path.join(home or os.path.expanduser("~"), DECKY_PLUGIN)


def decky_state(source_dir, home=None):
    """Returns which of the four cases this machine is in.

    It reads files and starts nothing. The page asks it whenever it is drawn,
    and an answer that cost a process would be a process for each visit.
    """
    home = home or os.path.expanduser("~")
    if not os.path.isdir(os.path.join(home, DECKY_HOME)):
        return DECKY_NONE
    where = decky_where(home)
    for name in DECKY_FILES:
        there = os.path.join(where, name)
        if not os.path.exists(there):
            return DECKY_ABSENT
        # The bytes, and not the time of the file. A clone that is updated
        # writes a new time on a file whose content did not change, and a
        # button that offered an update for that would offer it for ever.
        try:
            with open(there, "rb") as installed, \
                    open(os.path.join(source_dir, "decky", name), "rb") as mine:
                if installed.read() != mine.read():
                    return DECKY_OLD
        except OSError:
            return DECKY_ABSENT
    return DECKY_CURRENT


# What the page says and what the button is called, for each of the four.
DECKY_WORDS = {
    DECKY_NONE: (
        "Decky Loader is not installed, so there is nowhere to put the "
        "plugin. Install Decky from https://decky.xyz and open this page "
        "again.",
        "Install it anyway"),
    DECKY_ABSENT: (
        "The Game Mode plugin is not installed. It puts the strip, the CPU, "
        "the graphics card and the television into the Quick Access menu.",
        "Add the Game Mode plugin"),
    DECKY_OLD: (
        "The Game Mode plugin is installed, and this clone has a newer one.",
        "Update the Game Mode plugin"),
    DECKY_CURRENT: (
        "The Game Mode plugin is installed and current. Open the Quick "
        "Access menu in Game Mode.",
        "Install it again"),
}


def install_decky_command(source_dir, user=None):
    """Installs the plugin and restarts the loader, in one prompt.

    Root for both halves: Decky keeps its plugin directory as root, and the
    loader is a system service. See scripts/install-decky.sh, which the
    installer runs as well.
    """
    return ["pkexec", os.path.join(source_dir, "scripts", "install-decky.sh"),
            source_dir, user or getpass.getuser()]


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

A run that failed gives no answer. The log holds the reason. A guess is
    worse than a clear report that the answer is not known.
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
    """Returns the output of git, or "" for each failure.

    A directory that is not a clone is one such failure.
    """
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
    """Returns the branches that this clone knows, with no network access.

    This is sufficient to fill a menu at start. A branch from after the last
    fetch comes into the menu after the next update fetches it.
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


# The file where the installer records the commit of the install. This is the
# same path that scripts/user-unit.sh gives. It is written again here, because
# this side cannot source a shell file.
STAMP_PATH = os.path.join(INSTALL_DIR, "installed-from")


def installed_commit():
    """Returns the commit of the running files, or "" when there is no record.

    "" does not mean "old". Two installs leave no record: an install from
    before this function, and an install from a directory that git does not
    read. An answer of "out of date" to a question that nobody asked is worse
    than an answer of "not recorded".
    """
    try:
        with open(STAMP_PATH, encoding="utf-8", errors="replace") as handle:
            return handle.read().strip().split()[0]
    except (OSError, IndexError):
        return ""


def install_is_behind(source_dir, installed=None, head=None):
    """Returns the sentence for a clone that is in front of the install.

        The result is "" for each condition but one: both commits are known and
        they are different. An unknown commit is not proof. A status line reads
        this result, and a status line must not guess.

    Written because "pulled" and "installed" are two steps and the window
    showed only the first. A clone three commits ahead of the running copy
    looked exactly like an up-to-date machine, and two evenings went into
    working out from timestamps which of the two somebody had done.
    """
    installed = installed_commit() if installed is None else installed
    head = head_commit(source_dir) if head is None else head
    if not installed or not head or installed == head:
        return ""
    return ("Installed from %s, but this clone is at %s. Run the installer "
            "to put the new files in place - pulling on its own only "
            "changes the clone." % (installed[:7], head[:7]))


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
    """Returns the command that flashes the ESP with a firmware build.

    The command needs root for one reason: the service holds the serial port
    and must release it. PlatformIO runs again as the caller, because the
    toolchains are in the home directory of the caller. See the script.
    """
    return ["pkexec", os.path.join(source_dir, "scripts", "flash-firmware.sh"),
            environment]


def restart_watchers_command():
    """Returns the command that restarts both user units.

    The two units then read the configuration again. The command needs no
    root, and it is separate on purpose. The two are *user* units, and the
    privileged helper cannot reach them. But the panel runs as that user.

    The command restarts both units, and not the achievement watcher alone.
    The phone bridge reads the same file, and this command did not restart it.
    The switch in the panel therefore had no result. With the phone flashes
    off, the bridge continued until the next boot. With the phone flashes on
    again, the bridge stayed stopped, because it exited on the old setting.
    The new status check then reported that condition, correctly, as a
    problem from the panel.
    """
    return ["systemctl", "--user", "restart", WATCHER, PHONE_BRIDGE]


def notify_command(kind):
    """Flash the bar. Deliberately unprivileged: the FIFO is world-writable."""
    return [BINARY, "--notify", kind]


# The colour that the shape buttons flash. It is the blue of Steam. That row
# shows the shape, so each button must use the same colour, and that colour
# must belong to no notification. A different colour for each button makes
# the user compare two properties at the same time.
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
# (label, value) pairs. The value goes into the configuration file, and that
# file accepts each colour. This list holds the colours that the panel offers,
# and not each colour that is possible. The first colour is the default of
# the service.

# One short list for each notification: eight hues from the colour wheel and
# white. Before, each type of notification had some colours of its own. An
# achievement had gold and bronze, and a friend had a teal. That list was
# clear in the source. In the menu it became eighteen colours that the user
# had to compare one against the other.
#
# This list holds the colours that the panel offers, and not each colour that
# is possible. The configuration file accepts each colour, and the trigger
# does the same. A strip is also not a screen. On a strip, a bronze and a gold
# are almost the same colour.
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

# The panel offers these when the user selects a colour directly and does not
# set one. The "flash a colour" control on the Test page does this, and there
# an unusual colour is the purpose. The colour wheel comes first. Then come
# the colours that are worth a name.
EXTRA_COLOURS = (
    ("Steam blue", SHAPE_TEST_COLOUR),
    ("WhatsApp green", "#25d366"),
    ("Signal blue", "#3a76f0"),
    ("Gold", "#ffd700"),
    ("Amber", "#ffa000"),
)


# The two default colours of the load gauge. The panel offers them beside the
# wheel, because they are not on the wheel. They have almost the maximum
# distance that two colours on a strip can have, and that distance makes the
# two halves separate. Without them here, a user who wants the first setting
# again must type six hex digits. This list is separate from the amber in
# EXTRA_COLOURS, which is a different shade for a different control.
LOAD_DEFAULT_COLOURS = (
    ("Deep amber", "#ff6e00"),
    ("Steam blue", SHAPE_TEST_COLOUR),
)


def load_colours():
    """Returns the colours for the two halves of the load gauge.

    The best answers come first. The default pair leads, because each row
    already holds one of the two. A menu that opens on six hex digits looks
    like a setting that no person selected. Then comes the colour wheel of
    the notifications.
    """
    offered = []
    for label, value in LOAD_DEFAULT_COLOURS + NOTIFICATION_COLOURS:
        if value.lower() not in {had.lower() for _name, had in offered}:
            offered.append((label, value))
    return tuple(offered)


def palette():
    """Returns the colours for a direct selection, in a useful order.

    The colours of a notification come first. They are the colour wheel, and
    a user who wants a red bar finds red there. Then come the few colours
    that are worth a name. The user types each other colour. The
    configuration file accepts each colour, and the trigger does the same.
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
    """Returns the menu entries for the bar in Desktop Mode.

    This has the same arrangement as the two functions above. The service
    holds the list, and only the text is here. "steam" needs the most text.
    It means that the bar keeps the effect of the last Game Mode session.
    The bar did that before this page existed.

    The four effects of this project use the words of the rainbow slot.
    "rainbow" is the name of Steam, for the same reason as in the slot. Here
    the five are separate scenes and not one slot with one effect in it. A
    menu with "Rainbow" beside "Fire" must not make the user ask whether the
    first entry is the second entry with another name.
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
# A sensor is a path into /sys, and a colour is six hex digits. The panel must
# show neither of the two to a user. These two functions convert between the
# label and the value.


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
    """Converts an entry back to the value for the configuration file.

    An entry that no person added is its own value. That is the method that
    keeps a manual setting which the menu does not offer.
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
        # This value came from a manual edit and the machine does not have it
        # now. A removed eGPU, an unloaded driver or a spelling error causes
        # this. The entry shows the setting of the service. Without the
        # entry, the setting looks different for no reason.
        choices.append(("%s (not found)" % current, current))
    return _uniquify(choices)


POWER_CONFIG_PATH = "/etc/steamos-utility-center-power.conf"


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


# The text of the CPU settings file. It is in the power module, because the
# program that applies the file and steamos-utility-centerctl write it also.
power_config_text = power_module.text


def apply_power_command(source_dir, staged_path, ask=False):
    """Install the CPU settings and put them into effect."""
    return applier_command("power", source_dir, "apply-power.sh",
                           staged_path, ask=ask)


# What a line of an applier says while everything works, and the rows of a
# `systemctl status` block that repeat what the unit file holds. A page that
# showed these would show a wall of text, and the one line that answers the
# question would be in the middle of it.
#
# A phrase anywhere in the line, and not at the start of it: the summary at
# the end begins with a number, so "1 drive(s) mounted" and "0 drive(s)
# mounted" are the same line with a different count in front.
DRIVE_PROGRESS = ("wrote /", "removed /", "unmounting ", "giving ",
                  "configuration applied", "cpu settings applied",
                  "drive(s) mounted", "loaded:", "active:", "invocation:",
                  "where:", "what:", "process:", "tasks:", "memory:",
                  "cgroup:")

# The prefix that journalctl puts in front of a line: the date, the host and
# the program. The answer is after it, and the width of a card is not enough
# for both.
JOURNAL = re.compile(r"^[A-Z][a-z]{2} +\d+ [\d:]+ \S+ [^:]+: ")


def drive_trouble(transcript, most=2):
    """Returns what an applier said about a failure, short enough for a page.

    A person had to start this panel from a terminal to read the reason that a
    drive did not mount. The reason was in the output all the time.

    The last lines, because a program that fails says why at the end. The
    lines that report progress are dropped, and the prefix of journalctl with
    them: the answer is a mount point and a reason, not a date and a host.
    """
    lines = []
    for line in "".join(transcript).splitlines():
        line = JOURNAL.sub("", line.strip())
        low = line.lower()
        if not line or any(word in low for word in DRIVE_PROGRESS):
            continue
        lines.append(line)
    return "\n".join(lines[-most:])


def apply_mounts_command(source_dir, staged_path, owner_dir="", ask=False):
    """Returns the command that writes the drives and mounts them.

    `owner_dir` is a mount point to give to the desktop user, and it is
    optional. A drive that a person adds is a drive that root owns, so Steam
    cannot write a library to it until somebody says otherwise. The page has
    that as a button, and the button is this argument.

    A call that carries it always asks for a password, because no line of the
    sudoers rule matches a command of two arguments. That is the design and
    not a limit to work around: the chown walks a whole drive as root.
    """
    return applier_command("drives", source_dir, "apply-mounts.sh",
                           staged_path,
                           extra=(owner_dir,) if owner_dir else (), ask=ask)


def mount_point_for(found):
    """Returns the mount point to offer for a partition, from its own label.

    A person who adds a drive has a name for it, and the filesystem carries
    one. A partition with the label games gives /mnt/games, which is the
    answer that the person wants. A partition with no label uses its device
    name. A partition with neither gives nothing, and the page then asks.
    """
    name = str(found.get("label") or "").strip()
    if not name:
        name = os.path.basename(str(found.get("device") or ""))
    name = re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-").lower()
    return "/mnt/%s" % name if name else ""


def power_choices(offered, current="", labels=None, unset=True):
    """Returns (label, value) pairs for a CPU setting that the machine offers.

    This function never uses a list in this file. The available governors and
    preferences depend on the cpufreq driver and on its mode. See power.py. A
    menu in this file is therefore incorrect on some machines.

    "Leave it alone" comes first, because it is the default and because it
    reverses the other entries. This function keeps and marks a value from the
    configuration file that this machine does not offer. It does the same for
    an absent sensor. Without the entry, the setting looks different for no
    reason.
    """
    labels = labels or {}
    # `unset` is False for the preference, which has no such entry. The
    # panel writes the preference only with a governor. So "leave the file
    # alone" has no meaning for it. This project controls the CPU or it does
    # not control it, and the governor gives that answer.
    choices = [("Leave it to SteamOS", "")] if unset else []
    for value in offered:
        choices.append((labels.get(value, value), value))
    if current and current not in [value for _label, value in choices]:
        choices.append(("%s (not offered here)" % current, current))
    return _uniquify(choices)


def _uniquify(choices):
    """Makes each label different, where two labels are the same text.

    Two inputs on one chip can give the same description. The key of the menu
    is the text that it shows. A text that occurs twice therefore makes one
    of the two entries unreachable.
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
# The SteamOS CEC Toolkit under cec-toolkit/ does the CEC work, and
# server/steamos_utility_center/cec.py reads it. This file holds the same
# items as the rest of this module: the commands that the window runs, in one
# place. A window is difficult to drive under a test, and it must not also be
# the one record of the commands that it runs.


def install_cec_command(source_dir, remove=False):
    """Returns the command that installs or removes the CEC toolkit.

    The command asks for a password one time. It calls a script of this
    project and not the installer of the toolkit. That installer refuses to
    run as root, and it calls sudo approximately forty times. See
    scripts/install-cec.sh for the steps between the two.
    """
    return ["pkexec", os.path.join(source_dir, "scripts", "install-cec.sh"),
            "remove" if remove else "install",
            cec_module.source_dir(source_dir)]


def wake_radios_command():
    """Returns the command that lists the radios that the toolkit found.

    The command also reports whether each radio can wake the machine. It is a
    question and not a repair. The toolkit turns wakeup on for each radio that
    it matches, and this command is the one method to read that list. See
    cec.wake_radios_command.
    """
    return cec_module.wake_radios_command()


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
    status = cec_module.read_status(done)
    # The one switch the toolkit does not report on: its status says the
    # resume-wake unit file is there, which it is from the moment the toolkit
    # is installed, and never whether it is enabled. systemd is asked instead.
    status[cec_module.RESUME_WAKE_REPORT] = cec_module.resume_wake_enabled(
        runner(cec_module.resume_wake_command()))
    return status


def _run_quietly(command):
    """Runs a short command and returns its output, or None after a failure.

    The status read uses this function. That read occurs at each visit to the
    page and also on a timer. So this is the one command here that must write
    no line in the log and no warning in the status bar during a restart of
    the toolkit.
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


# The card, read from the daemon. It is in the lact module, because
# steamos-utility-centerctl reads the same card for the Game Mode plugin.
gpu_state = lact_module.state


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
