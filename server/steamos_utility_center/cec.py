# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""Talking to the television over HDMI, through the CEC toolkit.

Almost none of the CEC work is ours. It is the SteamOS CEC Toolkit, a fork of
another person's project. It is a module of its own under cec-toolkit/.
cec-toolkit/ORIGIN records where it came from. cec-toolkit/README.md gives the
changes and their reasons.

This module is the connection between that toolkit and this panel, and it is
deliberately thin. The toolkit has `steamos-cec-toolkitctl`, which reports each
of its own values as one JSON document and switches one feature at a time.

Nothing in this module reads a device, runs systemctl or edits a configuration
file. It builds the commands and reads the answers. The toolkit stays the one
program that knows how CEC operates.

Two results are important.

**To install it needs root. To use it does not.** The toolkit's installer
writes a sudoers file. That file gives the desktop user NOPASSWD on the helpers
that its own switches need.

After that one install with root, each switch on the page is an ordinary
command that runs as the user. There is no password, no polkit and no Apply
button. The CEC page thus saves at each click, where the LED pages wait for
Apply. The machine does not ask, so a request from this panel would be a
ceremony that the system does not need.

**Each answer comes from the machine.** The feature list here is a list of
names and words. Whether a feature is on, installable, or possible at all comes
from `toolkitctl status` at the moment of the question. A page from this module
thus cannot report a state that the toolkit does not report.

Nothing in this module runs a command. It builds the commands and returns them
to the panel's Runner. This module is thus testable on a machine with no
television, no CEC adapter and no toolkit, which is each machine that it was
written on.
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
# The order is the order in which a person sets them up. It is not the order of
# the toolkit's own names.
#
# First are the two that make the television follow the machine. Then the two
# that make the machine follow the television. Then the volume. Then the three
# that are repairs for particular hardware.
#
# The last three are last because a person must switch them on only after a
# fault.
#
# The name is the toolkit's own name. It goes directly to set-service, so this
# project cannot rename it.
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
# The file has approximately forty values. These are the values whose wrong
# content stops CEC completely. The other values tune particular televisions
# and controllers, and they belong in the file with their comments.
#
# `choices` is a list of (label, value) pairs for a setting with a fixed set of
# answers. It is empty for a setting that a person types.
#
# The first pair is what the toolkit does when the setting is not in the file.
# A drop-down list thus opens on the behaviour that a person gets anyway, and
# not on an empty entry.
#
# The last two values belong to a switch on the same page and not to the
# adapter, and their text says so. A feature whose behaviour depends on a value
# in a file is a feature with half of itself off the page. That is what sent a
# person to the configuration file with a text editor.
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


def status_command(home=None):
    return [command_path(home), "status"]


def read_status(text):
    """Returns what `toolkitctl status` printed, as a dictionary.

    This function is separate from the run of the command, so that each
    caller can be tested against a recorded answer.

    The toolkit prints one JSON document and nothing else. Text that this
    cannot parse thus means that the program did not run: python is missing,
    or the installation is incomplete. That is worth the exception. An empty
    status reads as "nothing is on", which is a different answer.
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


def toggle_command(name, on, home=None, source_dir=None):
    """Returns the command that switches one feature on or off.

    `source_dir` is the clone. The resume-wake switch alone needs it. That
    switch controls a root unit that the toolkit's control program does not
    know, so it goes through the same privileged helper of ours that installs
    the toolkit and not through toolkitctl.
    """
    kind = BY_NAME[name][0]
    state = "on" if on else "off"
    if kind == RESUME_WAKE:
        return ["pkexec",
                os.path.join(source_dir or "", "scripts", "install-cec.sh"),
                "resume-wake", state]
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
# Volume over CEC is the System Audio Control feature, and it is written for
# an amplifier.
#
# A television that does not implement it does not say so. It accepts the
# volume command, does nothing, and answers nothing. The switch thus looks
# defective while each line in the log says that the message went out. To
# establish this took one evening.
#
# Asked directly, the same television answers immediately. On the set this was
# found on:
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
# `toolkitctl status`. The status does not carry it. The status says whether
# the usb-wake service is enabled, and not which radios it found.
#
# "The switch is on and nothing wakes the machine" is the state of a person who
# asks this question. The helper's own `status` lists what it matched and
# whether each radio can wake the machine. That is the answer.
#
# No password is necessary. The toolkit's installer writes a sudoers rule for
# this program, as it does for each switch on the page.
USB_WAKE_HELPER = "/var/lib/steamos-cec-toolkit/steamos-cec-usb-wake-control"


def wake_radios_command():
    """Returns the command that asks the toolkit which radios it found."""
    return ["sudo", "-n", USB_WAKE_HELPER, "status"]


def wake_radios_said(text):
    """Returns the meaning of that answer, in one sentence.

    There are three answers, and this function separates them deliberately.
    It did not do so before. To find nothing and to find a radio that is
    already allowed printed the same line. A machine where this does not
    operate thus read as a machine with nothing left to do. That is not an
    answer to the question "did it find my radio?".
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

    It must be readable *and* writable, because CEC is a conversation. An
    adapter that this user can read and cannot write is the form that a
    permissions fault takes after a suspend or a SteamOS update.

    The toolkit installs a helper and a udev rule to repair that fault. A
    False answer here is thus a repairable state, and it is worth a
    difference from a machine with no adapter.
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
