# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""Talking to the television over HDMI, through the CEC toolkit.

Almost none of the CEC work is ours. It is the SteamOS CEC Toolkit, under
cec-toolkit/. Its ORIGIN records where it came from, and its README.md gives
the changes and their reasons.

This module is the thin connection between that toolkit and this panel. It
builds the commands and reads the answers of `steamos-cec-toolkitctl`. It
reads no device, runs no systemctl and edits no configuration file, so the
toolkit stays the one program that knows how CEC operates. It is also testable
on a machine with no television, no adapter and no toolkit.

Two results are important.

**To install it needs root. To use it does not.** The toolkit's installer
writes a sudoers file for the helpers its own switches need. Each switch on
the page is thus an ordinary command that runs as the user, with no password
and no Apply button. The LED pages wait for Apply because they write /etc.

**Each answer comes from the machine.** The list here is names and words.
Whether a feature is on, installable or possible comes from `toolkitctl
status` at the moment of the question.
"""

from __future__ import annotations

import json
import os
import shutil

# Where the toolkit's installer puts its control program. This is a user path
# and not a system path. The toolkit installs for one user, because half of
# what it manages is user systemd units and a WirePlumber configuration in a
# home directory.
COMMAND = ".local/bin/steamos-cec-toolkitctl"

# The toolkit's directory, relative to the repository root. Its installer runs
# from inside the directory and reads the files beside it. The installer thus
# cannot be copied out alone. See scripts/install-cec.sh.
SOURCE = "cec-toolkit"

# How to switch a feature on. The method is not the same for each feature. The
# three kinds change different things, and the toolkit gives each kind its own
# subcommand.
USER_SERVICE = "user"           # a systemd user unit, enabled as the user
SYSTEM_SERVICE = "system"       # a root unit, through a NOPASSWD helper
EXTERNAL_VOLUME = "volume"      # WirePlumber config plus a service override
RESUME_WAKE = "resume"          # a root unit toolkitctl cannot switch

# The unit for that last kind. The toolkit installs it, but enables it only
# with the Steam button. See its install.sh.
#
# Its control program has the unit in neither service table. Nothing that the
# program offers can switch the unit, and nothing that it reports gives the
# state of the unit. This module does both instead.
RESUME_WAKE_UNIT = "steamos-cec-resume-wake.service"
RESUME_WAKE_REPORT = "resume_wake_enabled"

# The eight features that this page can switch, in the order of the display.
#
# The order is the order a person sets them up in: the television follows the
# machine, then the machine follows the television, then the volume, then the
# three that repair particular hardware and are switched on after a fault.
#
# The name is the toolkit's own. It goes to set-service, so this project
# cannot rename it.
FEATURES = (
    ("steam-button", USER_SERVICE,
     "Steam button wakes the television",
     "Home or Guide on the controller turns the television on and switches "
     "it to this machine."),
    ("boot-wake", USER_SERVICE,
     "Wake the television at start",
     "The same, when Game Mode starts after a cold boot."),
    ("resume-wake", RESUME_WAKE,
     "Wake the television on resume",
     "The same, when this machine wakes from sleep."),
    ("power-standby", SYSTEM_SERVICE,
     "Turn the television off with the machine",
     "Turns the television off when this machine sleeps or shuts down."),
    ("tv-standby", USER_SERVICE,
     "Sleep when the television does",
     "Turning the television off puts this machine to sleep."),
    ("input-away-suspend", USER_SERVICE,
     "Sleep when the television switches away",
     "Sleeps once the television has been on another input for a while."),
    ("external-volume", EXTERNAL_VOLUME,
     "Volume buttons control the television",
     "Game Mode gets + and - instead of a slider. Needs an AV receiver or "
     "a soundbar - most televisions refuse. \u201cAsk about volume\u201d "
     "below says whether yours does."),
    ("usb-wake", SYSTEM_SERVICE,
     "Let a controller wake the machine",
     "Lets a controller wake this machine from sleep, so the Steam button "
     "can reach it."),
    ("gamescope-recovery", USER_SERVICE,
     "Recover Gamescope after a wake",
     "Restarts Gamescope if the picture comes back wrong. Leave it off "
     "unless you have that fault."),
)

# The same table, keyed by name, for a caller that has a name only.
BY_NAME = {name: (kind, label, said) for name, kind, label, said in FEATURES}

# What the machine must have before any of this can operate, and what to say
# when it does not. The toolkit also checks these and reports a clear error.
# They are here so that the panel can say so *before* the installation.
#
# dbus_next is not in this list, although three services import it. It is a
# python module and not a program, so this module looks for it in a different
# way. See missing().
NEEDS = (
    ("cec-ctl", "v4l-utils, which is what actually speaks CEC"),
    ("varlinkctl", "part of systemd, used to reach PipeWire's volume control"),
    ("systemctl", "systemd itself"),
)

# The configuration values that belong on a page, as
# (key, label, explanation, choices).
#
# The file has approximately forty values. These are the ones whose wrong
# content stops CEC completely. The others tune particular televisions and
# controllers and belong in the file with their comments.
#
# `choices` is (label, value) pairs, and empty for a setting a person types.
# Its first pair is what the toolkit does with the setting absent, so a
# drop-down opens on the behaviour a person gets anyway.
#
# The last two belong to a switch on the same page and say so. A feature whose
# behaviour depends on a value in a file has half of itself off the page.
SHOWN = (
    ("CEC_DEVICE", "CEC adapter",
     "The adapter's device node, usually /dev/cec0.", ()),
    ("CEC_AUDIO_LOGICAL_ADDRESS", "Which device has the volume",
     "5 is an amplifier or soundbar. Use 0 when the television itself makes "
     "the sound.", ()),
    ("HDMI_ALSA_CARD_NAME", "HDMI sound card",
     "The PipeWire card the television is on. Discover Audio fills this in.",
     ()),
    ("CEC_SLEEP_TV_ACTION", "What sleep sends the television",
     "For \u201cTurn the television off with the machine\u201d. Standby turns "
     "most televisions and receivers off; Inactive source is for the sets "
     "that cold-boot from a standby instead, some Philips ones among them.",
     (("Standby", "standby"), ("Inactive source", "inactive-source"))),
    ("INPUT_INACTIVE_SUSPEND_DELAY_SECONDS", "Wait before sleeping (seconds)",
     "For \u201cSleep when the television switches away\u201d: how long the "
     "television has to have been on another input first. 60 when it is not "
     "set.", ()),
)


class CecError(Exception):
    """The toolkit did not answer, or did not answer in JSON."""


def command_path(home=None):
    """Returns the path of the toolkit's control program, installed or not."""
    return os.path.join(home or os.path.expanduser("~"), COMMAND)


def installed(home=None):
    """Returns whether the toolkit is installed for this user.

    It looks for the control program and not for the configuration file.
    /etc/steamos-cec-toolkit.conf stays after an uninstall on a system with
    atomic updates. A page that used the configuration file would thus offer
    to configure a toolkit that is not there.
    """
    return os.access(command_path(home), os.X_OK)


def source_dir(repo):
    return os.path.join(repo, SOURCE)


# The file that says which toolkit a clone carries. The installed copy reports
# its own, and `toolkitctl status` carries it as "version".
VERSION_FILE = "VERSION"


def clone_version(repo):
    """Returns the version of the toolkit in a clone, or "" if unreadable."""
    try:
        with open(os.path.join(source_dir(repo), VERSION_FILE),
                  encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def running_version(status):
    """Returns the version that the installed toolkit reports."""
    return str((status or {}).get("version") or "").strip()


def out_of_date(status, repo):
    """Reports whether the installed toolkit is older than this clone.

    False where either version is unreadable. A question that cannot be
    answered is not an answer of "yes".
    """
    running = running_version(status)
    theirs = clone_version(repo)
    return bool(running and theirs and running != theirs)


def status_command(home=None):
    return [command_path(home), "status"]


def read_status(text):
    """Returns what `toolkitctl status` printed, as a dictionary.

    Separate from the run of the command, so a caller can be tested against a
    recorded answer.

    The toolkit prints one JSON document and nothing else, so text this cannot
    parse means the program did not run. That is worth the exception: an empty
    status would read as "nothing is on".
    """
    try:
        found = json.loads(text)
    except ValueError as exc:
        raise CecError("the CEC toolkit did not answer in JSON: %s" % exc)
    if not isinstance(found, dict):
        raise CecError("the CEC toolkit answered with %s, not a status"
                       % type(found).__name__)
    return found


def feature_on(status, name):
    """Returns whether one feature is on, from a status document.

    It reads "enabled" and not "active", and the difference is important.
    boot-wake is a oneshot. It runs at the start of Game Mode and then exits,
    so it is almost never active. A question about "active" thus shows it as
    off while it operates correctly. "Enabled" is the question of the switch.
    """
    kind = BY_NAME[name][0]
    if kind == EXTERNAL_VOLUME:
        return bool(status.get("external_volume", {}).get("enabled"))
    if kind == RESUME_WAKE:
        # This is not in the toolkit's status. The caller puts it there,
        # because systemd alone knows it. See resume_wake_command.
        return bool(status.get(RESUME_WAKE_REPORT))
    where = "services" if kind == USER_SERVICE else "system_services"
    return bool(status.get(where, {}).get(name, {}).get("is_enabled"))


def resume_wake_command():
    """Asks systemd whether the resume-wake unit is enabled.

    The toolkit's own status reports only that the unit file is on the disk.
    The file is there from the moment of the installation. A switch from that
    report would thus be on from the start and would never be off.
    """
    return ["systemctl", "is-enabled", RESUME_WAKE_UNIT]


def resume_wake_enabled(answer):
    """Returns what that command said. Each answer except "enabled" is off.

    `systemctl is-enabled` exits non-zero for a disabled unit. A runner that
    returns nothing after a non-zero exit thus answers "off". That is also
    the answer for a unit that is not there, and on this page the two states
    are the same.
    """
    return bool(answer) and answer.strip().startswith("enabled")


# The program that switches the wake after a resume, and where it is.
#
# That switch is a unit of root, so it needs a program with root behind it and
# a sudoers line that permits it. Every switch of this kind in the toolkit has
# both. This one is not upstream's, so it went through pkexec and worked on a
# desktop and nowhere else.
#
# See scripts/resume-wake.sh and ctl.sudoers_text.
RESUME_WAKE_HELPER = ("/var/lib/steamos-utility-center/"
                      "steamos-utility-center-resume-wake")


def toggle_command(name, on, home=None, source_dir=None, ask=False):
    """Returns the command that switches one feature on or off.

    `source_dir` is the clone, and only the fallback of the resume-wake switch
    uses it. `ask` is that fallback: it gives the command that asks for a
    password, for an installation that has no sudoers rule.
    """
    kind = BY_NAME[name][0]
    state = "on" if on else "off"
    if kind == RESUME_WAKE:
        if ask or not os.path.exists(RESUME_WAKE_HELPER):
            return ["pkexec",
                    os.path.join(source_dir or "", "scripts",
                                 "install-cec.sh"),
                    "resume-wake", state]
        return ["sudo", "-n", RESUME_WAKE_HELPER, state]
    if kind == EXTERNAL_VOLUME:
        return [command_path(home), "set-external-volume", state]
    verb = "set-service" if kind == USER_SERVICE else "set-system-service"
    return [command_path(home), verb, name, state]


# What the buttons of the page do, as (key, label, argv tail).
#
# These are actions and not settings. Each one occurs one time, now, and
# changes nothing that stays. They are thus the way to find out whether any of
# this operates, before a person switches a feature on and reboots.
# What actually speaks CEC, for the one action below that does not go through
# the toolkit at all.
CEC_CTL = "cec-ctl"

# The adapter to speak to. It comes from the toolkit's own status and is not a
# guess. See config(). The default is the toolkit's own default, so a machine
# with no setting gets the same question that the toolkit would ask.
DEVICE_SETTING = "CEC_DEVICE"
DEFAULT_DEVICE = "/dev/cec0"

# The setting that says which device is supposed to render the sound.
AUDIO_ADDRESS = "CEC_AUDIO_LOGICAL_ADDRESS"


def configured_device(settings):
    """Returns the adapter that the toolkit is configured to use."""
    return str(settings.get(DEVICE_SETTING, "")).strip() or DEFAULT_DEVICE

# The one action here that is not a toolkit subcommand, because the question
# is not about the toolkit.
#
# Volume over CEC is the System Audio Control feature, written for an
# amplifier. A television that does not implement it accepts the volume
# command, does nothing, and answers nothing, so the switch looks defective
# while the log says the message went out.
#
# Asked directly, the same television answers at once:
#
#   GIVE_SYSTEM_AUDIO_MODE_STATUS (0x7d)
#       Received from TV (0): FEATURE_ABORT reason: refused (0x04)
#       Approximate response time: 26 ms
#
# That is the difference between "will not" and "did not receive". The
# difference is worth a button.
AUDIO_PROBE = "audio-probe"

ACTIONS = (
    ("wake", "Wake the television", ["wake"]),
    ("standby", "Send standby", ["standby"]),
    ("volume-up", "Volume up", ["volume", "up"]),
    ("volume-down", "Volume down", ["volume", "down"]),
    (AUDIO_PROBE, "Ask about volume", None),
)

# The three discovery commands. Each one writes what it finds into the
# configuration. They are separate from ACTIONS because they leave a change.
DISCOVERIES = (
    ("discover-cec", "Discover CEC devices",
     "Asks the bus what is on it and fills in the adapter and the addresses."),
    ("discover-audio", "Discover audio output",
     "Finds the HDMI sound card PipeWire is using for the television."),
    ("discover-input", "Discover controllers",
     "Lists the gamepads whose Home button can be watched for."),
)


def audio_probe_command(settings=None):
    """Asks the device that must have the volume whether it does.

    A refusal returns FEATURE_ABORT in some milliseconds. A device that
    implements the feature reports its mode. Both are answers, and a volume
    command gives neither. See AUDIO_PROBE.
    """
    settings = settings or {}
    target = str(settings.get(AUDIO_ADDRESS, "")).strip() or "0"
    return [CEC_CTL, "-d", configured_device(settings), "--to", target,
            "--give-system-audio-mode-status"]


def action_command(key, home=None, settings=None):
    if key == AUDIO_PROBE:
        return audio_probe_command(settings)
    for name, _label, tail in ACTIONS:
        if name == key and tail is not None:
            return [command_path(home)] + tail
    for name, _label, _said in DISCOVERIES:
        if name == key:
            return [command_path(home), name]
    raise KeyError(key)


# -- which radios can wake this machine --------------------------------------
#
# The one value on the CEC page that comes from a helper and not from
# `toolkitctl status`, which says whether the usb-wake service is enabled and
# not which radios it found.
#
# The question comes from "the switch is on and nothing wakes the machine".
# The helper's own `status` lists what it matched. The toolkit's installer
# permits it, as it does each switch on the page.
USB_WAKE_HELPER = "/var/lib/steamos-cec-toolkit/steamos-cec-usb-wake-control"


def wake_radios_command():
    """Returns the command that asks the toolkit which radios it found."""
    return ["sudo", "-n", USB_WAKE_HELPER, "status"]


def wake_radios_said(text):
    """Returns the meaning of that answer, in one sentence.

    Three answers, separated. "Found nothing" and "found a radio that is
    already allowed" are not the same, and a person is asking "did it find my
    radio?".
    """
    try:
        found = json.loads(text)
        radios = found["helper"]["devices"]
        if not isinstance(radios, list):
            raise TypeError
    except (ValueError, TypeError, KeyError, IndexError):
        return ("The toolkit's USB wake helper did not answer. Install HDMI "
                "CEC first, or look at what the command printed above.")
    if not radios:
        return ("No radio on the USB bus matched. One built into the board "
                "and not wired through USB cannot be switched on from here.")
    named = ", ".join(str(radio.get("label", "")).strip() or "an unnamed radio"
                      for radio in radios)
    waking = [radio for radio in radios if radio.get("after") == "enabled"]
    if len(waking) == len(radios):
        return "Found %s, allowed to wake this machine." % named
    if not waking:
        return ("Found %s. Nothing there may wake this machine yet - turn on "
                "\u201cLet a controller wake the machine\u201d below." % named)
    return ("Found %s, of which %d of %d may wake this machine."
            % (named, len(waking), len(radios)))


def config(status):
    """Returns the toolkit's configuration, as it reported it. {} if none."""
    found = status.get("config")
    return dict(found) if isinstance(found, dict) else {}


def set_config_command(values, home=None):
    """Returns the command that writes settings into the toolkit's file.

    The toolkit takes the settings as one JSON argument. It writes a *user*
    configuration that has priority over /etc. This command thus needs no
    root, and a setting from here stays after an installation over the top.
    """
    return [command_path(home), "set-config", json.dumps(values, sort_keys=True)]


def device(status):
    """Returns what the status says about the adapter itself.

    This is the status's own answer and not a value from the feature states.
    Each feature can be enabled and none of them can operate when the adapter
    is absent or is not writable. That is the first thing to report on a page
    where nothing occurs.
    """
    found = status.get("cec_device")
    if not isinstance(found, dict):
        return {"device": "", "exists": False, "readable": False,
                "writable": False}
    return found


def usable(status):
    """Returns whether CEC can operate now: the adapter is there and writable.

    Readable *and* writable, because CEC is a conversation. Read-only is the
    form a permissions fault takes after a suspend or a SteamOS update, and
    the toolkit has a helper and a udev rule that repair it.
    """
    found = device(status)
    return bool(found.get("exists") and found.get("readable")
                and found.get("writable"))


def missing(module_check=None, which=None):
    """Returns the programs and modules that CEC needs and this machine lacks.

    The result is a list of (name, why) pairs, in the order of NEEDS with the
    python module last. Both lookups are parameters, so a test can give an
    answer on a machine that has none of them. That is the machine that this
    runs on.
    """
    which = which or shutil.which
    absent = [(name, why) for name, why in NEEDS if not which(name)]
    # Three of the services import it. It is the one requirement that the
    # toolkit's installer reports as a warning and does not refuse. A person
    # can thus complete an installation and find the fault only in a log.
    if not (module_check or _has_dbus_next)():
        absent.append(("python dbus_next",
                       "needed by the services that watch the CEC bus"))
    return tuple(absent)


def _has_dbus_next():
    try:
        import dbus_next                                     # noqa: F401
    except ImportError:
        return False
    return True
