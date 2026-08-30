# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""Talking to the television over HDMI, through the CEC toolkit.

None of the CEC work is ours. It is the SteamOS CEC Toolkit, vendored under
vendor/steamos-cec-toolkit - see vendor/README.md for where it came from and
why it lives in the repository rather than being fetched.

This module is the seam between that toolkit and this panel, and it is thin on
purpose. The toolkit already ships `steamos-cec-toolkitctl`, which reports
everything about itself as one JSON document and turns each feature on and off
one at a time. So there is nothing here that reads a device, runs systemctl or
edits a config file: this builds the commands and reads the answers, and the
toolkit stays the only thing that knows how CEC works.

Two consequences worth knowing before reading further.

**Installing needs root; using it does not.** The toolkit's installer writes a
sudoers file granting the desktop user NOPASSWD on exactly the helpers its own
toggles need. After that one elevated install, every switch on the page is an
ordinary command run as the user - no password, no polkit, no Apply button.
That is why the CEC page saves as you click where the LED pages queue up
behind Apply: the machine does not ask, so making the user ask would be this
panel inventing a ceremony the system does not need.

**Every answer comes from the machine.** The feature list here is a list of
names and wording; whether a feature is on, installable, or possible at all is
read from `toolkitctl status` at the moment it is asked. A page built from
this cannot claim a state the toolkit does not report.

Nothing in here runs anything. The commands are built and handed back for the
panel's Runner to run, which is what keeps this testable on a machine with no
television, no CEC adapter and no toolkit installed - which is every machine
this was written on.
"""

from __future__ import annotations

import json
import os
import shutil

# Where the toolkit's installer puts its control program. A user path, not a
# system one: the toolkit installs per user, because half of what it manages
# are user systemd units and a WirePlumber config in somebody's home.
COMMAND = ".local/bin/steamos-cec-toolkitctl"

# The vendored tree, relative to the repository root. The installer is run
# from inside it and reads its own siblings, so it cannot be copied out on its
# own - see scripts/install-cec.sh.
SOURCE = os.path.join("vendor", "steamos-cec-toolkit")

# How a feature is turned on, which is not the same for all of them. The three
# kinds differ in what they touch, and the toolkit gives each its own
# subcommand rather than one that guesses.
USER_SERVICE = "user"           # a systemd user unit, enabled as the user
SYSTEM_SERVICE = "system"       # a root unit, through a NOPASSWD helper
EXTERNAL_VOLUME = "volume"      # WirePlumber config plus a service override
RESUME_WAKE = "resume"          # a root unit toolkitctl cannot switch

# The unit behind that last one. The toolkit installs it and enables it only
# alongside the Steam button - see its install.sh - and its control program
# has it in neither service table, so nothing it offers can switch it and
# nothing it reports says whether it is on. Both are done here instead.
RESUME_WAKE_UNIT = "steamos-cec-resume-wake.service"
RESUME_WAKE_REPORT = "resume_wake_enabled"

# The eight things this page can switch, in the order they are shown.
#
# Ordered by what somebody sets up first rather than by what the toolkit calls
# them: the two that make the television follow the machine, then the two that
# make the machine follow the television, then volume, then the three that are
# repairs for particular hardware. The last three are last because they are
# the ones nobody should turn on until something is wrong.
#
# The name is the toolkit's own - it is passed straight to set-service - so
# these are not ours to rename.
FEATURES = (
    ("steam-button", USER_SERVICE,
     "Steam button wakes the television",
     "A press of Home or Guide on the controller powers the TV and receiver "
     "on and switches the input back to this machine."),
    ("boot-wake", USER_SERVICE,
     "Wake the television at start",
     "The same wake and input switch when Game Mode starts after a cold "
     "boot, instead of waiting for a button."),
    ("resume-wake", RESUME_WAKE,
     "Wake the television on resume",
     "The same wake and input switch when this machine comes back from "
     "suspend. The toolkit enables this only alongside the Steam button and "
     "cannot switch it afterwards, so this one goes through our own helper."),
    ("power-standby", SYSTEM_SERVICE,
     "Turn the television off with the machine",
     "Sends HDMI-CEC standby before this machine sleeps or shuts down. Some "
     "televisions cold-boot from that - CEC_SLEEP_TV_ACTION in the config "
     "file sends Inactive Source instead for those."),
    ("tv-standby", USER_SERVICE,
     "Sleep when the television does",
     "Suspends this machine when the television broadcasts standby, so "
     "turning off the TV turns off the machine."),
    ("input-away-suspend", USER_SERVICE,
     "Sleep when the television switches away",
     "Suspends this machine once the TV has been on another input for a "
     "while. The delay is INPUT_INACTIVE_SUSPEND_DELAY_SECONDS."),
    ("external-volume", EXTERNAL_VOLUME,
     "Volume buttons control the television",
     "Game Mode shows + and - instead of a slider, and they change the "
     "volume on the receiver or soundbar over CEC. Needs a reboot before "
     "Game Mode picks up the new controls."),
    ("usb-wake", SYSTEM_SERVICE,
     "Let a controller wake the machine",
     "Allows matching Bluetooth radios and controller receivers to wake this "
     "machine from suspend, so the Steam button can reach it at all. Whether "
     "it works depends on the hardware, not on this switch."),
    ("gamescope-recovery", USER_SERVICE,
     "Recover Gamescope after a wake",
     "Restarts Gamescope when the display comes back in a bad state after "
     "CEC switched the input. A repair for a specific fault - leave it off "
     "unless you have that fault."),
)

# The same, keyed, for everything that has a name and wants the rest.
BY_NAME = {name: (kind, label, said) for name, kind, label, said in FEATURES}

# What has to be on the machine before any of this can work, and what to say
# when it is not. The toolkit checks these too and fails clearly; they are
# here so the panel can say so *before* somebody installs and wonders why
# nothing happens.
#
# dbus_next is not in this list even though three services import it: it is a
# python module rather than a program, so it is looked for differently - see
# missing().
NEEDS = (
    ("cec-ctl", "v4l-utils, which is what actually speaks CEC"),
    ("varlinkctl", "part of systemd, used to reach PipeWire's volume control"),
    ("systemctl", "systemd itself"),
)

# The config values worth putting on a page. The file has about forty; these
# are the ones whose wrong value stops CEC working at all, and the rest are
# tuning for particular televisions and controllers that belongs in the file
# with its comments around it.
# (key, label, explanation, choices)
#
# `choices` is (label, value) pairs for a setting that has a fixed set of
# answers, and empty for one that is typed. The first pair is what the
# toolkit does when the setting is not in the file at all, so a drop-down
# opens on the behaviour somebody would get anyway rather than on a blank.
#
# The last two belong to a switch on the same page rather than to the adapter,
# and say so: a feature whose behaviour is decided by a value in a file is a
# feature half of which is not on the page, which is what sent somebody to the
# config file with a text editor.
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
    """The toolkit could not be asked, or did not answer in JSON."""


def command_path(home=None):
    """Where the toolkit's control program is, installed or not."""
    return os.path.join(home or os.path.expanduser("~"), COMMAND)


def installed(home=None):
    """Whether the toolkit has been installed for this user.

    The control program, not the config file: /etc/steamos-cec-toolkit.conf
    survives an uninstall on an atomic-update system, so a page that keyed off
    the config would offer to configure a toolkit that is no longer there.
    """
    return os.access(command_path(home), os.X_OK)


def source_dir(repo):
    return os.path.join(repo, SOURCE)


def status_command(home=None):
    return [command_path(home), "status"]


def read_status(text):
    """Turn what `toolkitctl status` printed into a dictionary.

    Kept apart from running it so that everything downstream can be tested
    against a recorded answer. The toolkit prints one JSON document and
    nothing else, so anything unparseable means it did not get to run - a
    missing python, a half-finished install - and that is worth the exception
    rather than an empty status that reads as "nothing is on".
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
    """Whether one feature is switched on, according to a status document.

    Enabled rather than active, and the difference matters. boot-wake is a
    oneshot that runs when Game Mode starts and then exits, so it is almost
    never active and asking that would show it as off while it is working
    perfectly. Enabled is the question the switch is asking.
    """
    kind = BY_NAME[name][0]
    if kind == EXTERNAL_VOLUME:
        return bool(status.get("external_volume", {}).get("enabled"))
    if kind == RESUME_WAKE:
        # Not in the toolkit's status at all - put there by whoever read it,
        # because systemd is the only one that knows. See resume_wake_command.
        return bool(status.get(RESUME_WAKE_REPORT))
    where = "services" if kind == USER_SERVICE else "system_services"
    return bool(status.get(where, {}).get(name, {}).get("is_enabled"))


def resume_wake_command():
    """Ask systemd whether the resume-wake unit is enabled.

    The toolkit's own status reports only that the unit file is on disk,
    which it is from the moment the toolkit is installed - so a switch drawn
    from that would be on from the start and would never be off.
    """
    return ["systemctl", "is-enabled", RESUME_WAKE_UNIT]


def resume_wake_enabled(answer):
    """What that command said. Anything but "enabled" is off.

    `systemctl is-enabled` exits non-zero for a disabled unit, so a runner
    that hands back nothing on a bad exit is answering "off" - which is the
    same answer as a unit that is not there, and on this page those are the
    same thing.
    """
    return bool(answer) and answer.strip().startswith("enabled")


def toggle_command(name, on, home=None, source_dir=None):
    """The command that turns one feature on or off.

    `source_dir` is the clone, and only the resume-wake switch needs it: that
    one is a root unit the toolkit's control program does not know, so it
    goes through the same privileged helper of ours that installs the toolkit
    rather than through toolkitctl.
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


# What the page's buttons do, as (key, label, argv tail). Actions rather than
# settings: each one happens once, now, and changes nothing that outlives it -
# which is what makes them the way to find out whether any of this works
# before turning a feature on and rebooting into it.
ACTIONS = (
    ("wake", "Wake the television", ["wake"]),
    ("standby", "Send standby", ["standby"]),
    ("volume-up", "Volume up", ["volume", "up"]),
    ("volume-down", "Volume down", ["volume", "down"]),
)

# The three discoveries, which write what they find into the config. Separate
# from ACTIONS because these do leave something behind.
DISCOVERIES = (
    ("discover-cec", "Discover CEC devices",
     "Asks the bus what is on it and fills in the adapter and the addresses."),
    ("discover-audio", "Discover audio output",
     "Finds the HDMI sound card PipeWire is using for the television."),
    ("discover-input", "Discover controllers",
     "Lists the gamepads whose Home button can be watched for."),
)


def action_command(key, home=None):
    for name, _label, tail in ACTIONS:
        if name == key:
            return [command_path(home)] + tail
    for name, _label, _said in DISCOVERIES:
        if name == key:
            return [command_path(home), name]
    raise KeyError(key)


def config(status):
    """The toolkit's configuration, as it reported it. Empty when it did not."""
    found = status.get("config")
    return dict(found) if isinstance(found, dict) else {}


def set_config_command(values, home=None):
    """The command that writes settings into the toolkit's own config file.

    The toolkit takes them as one JSON argument and writes a *user* config
    that shadows /etc - which is why this needs no root either, and why a
    setting made here survives the toolkit being reinstalled over the top.
    """
    return [command_path(home), "set-config", json.dumps(values, sort_keys=True)]


# -- the adapter's own place on the bus --------------------------------------
#
# Before a CEC adapter can say anything as itself it has to claim a logical
# address. Nothing in the toolkit ever does: every wake path asks the adapter
# which address it holds, and when the answer is none - "CEC logical address
# is not allocated yet" in its log - falls back to "pretend to be 4" and sends
# anyway. Those messages carry an initiator the adapter does not own, and the
# bus has no reason to act on them.
#
# Standby is the exception, which is what hid this for so long. Its first two
# sends carry no initiator at all, so they go out from the unregistered
# address as a broadcast - which an adapter that has claimed nothing is still
# allowed to do, and which a television accepts. So the television turned off
# on request and would not turn back on, and every line in the log said the
# wake had been sent.
#
# Found on a machine whose adapter reported a good physical address (3.0.0.0,
# so HDMI 3) and "Logical Address Mask: 0x0000" - a working cable, a listening
# television, and an adapter that was not on the bus.
#
# What the panel adds is that missing step, and only when it is missing: if
# anything at all is registered - Steam's own cecd, an earlier run of this -
# the adapter is left exactly as it is. It is never taken off whoever has it.

CEC_CTL = "cec-ctl"

# Every adapter on the machine rather than the one the toolkit is configured
# for. Registering an adapter nobody has claimed cannot disturb anything, and
# a machine with two of them is a machine where the configured one is the more
# likely to be wrong.
ADAPTERS = "/dev/cec*"

# What the television will show as the source's name. Registering as a
# playback device is what claims logical address 4, which is what every one of
# the toolkit's wake paths already assumes it will find.
REGISTERED_NAME = "SteamOS"

# f.f.f.f is what an adapter reports when it has no picture to belong to - no
# link, or one whose EDID has not been read yet. Claiming an address then
# would put us on the bus at an address that means nothing.
NO_PHYSICAL_ADDRESS = "f.f.f.f"


def adapter_state_command(device):
    """How to ask one adapter what it is. Reports; changes nothing."""
    return [CEC_CTL, "-d", device]


def register_command(device):
    """How to claim logical address 4 for an adapter that has none."""
    return [CEC_CTL, "-d", device, "--playback", "--osd-name", REGISTERED_NAME]


def _field(text, name):
    """One "Name : value" line of cec-ctl's report, or "" if it said none."""
    for line in (text or "").splitlines():
        left, sep, right = line.partition(":")
        if sep and left.strip().lower() == name.lower():
            return right.strip()
    return ""


def adapter_registered(text):
    """Whether this adapter holds a logical address. None when it did not say.

    Two fields answer it and either will do - the mask is the one to trust,
    the count is there for a cec-ctl that words it differently. None rather
    than False when neither is present, because "the answer was not in a shape
    this understands" and "the adapter is not on the bus" want opposite
    responses: the first is a reason to leave well alone.
    """
    mask = _field(text, "Logical Address Mask")
    if mask:
        try:
            return int(mask, 0) != 0
        except ValueError:
            pass
    count = _field(text, "Logical Addresses")
    if count:
        try:
            return int(count, 0) != 0
        except ValueError:
            pass
    return None


def adapter_physical_address(text):
    """Where this adapter sits on the television, or "" with no picture."""
    found = _field(text, "Physical Address")
    if not found or found.lower() == NO_PHYSICAL_ADDRESS:
        return ""
    return found


def wants_registering(text):
    """Whether this adapter is one we should claim an address for.

    Both halves have to be true, and the physical address is the half that is
    easy to forget: an adapter with no link reports one that means nothing, so
    registering it would claim a place on a bus it cannot see.
    """
    return adapter_registered(text) is False and bool(
        adapter_physical_address(text))


def device(status):
    """What the status says about the adapter itself.

    Its own answer rather than one derived from the feature states: every
    feature can be enabled and none of them can work if the adapter is not
    there or is not writable, and that is the first thing to say on a page
    where nothing is happening.
    """
    found = status.get("cec_device")
    if not isinstance(found, dict):
        return {"device": "", "exists": False, "readable": False,
                "writable": False}
    return found


def usable(status):
    """Whether CEC could work right now: an adapter that is there and writable.

    Readable *and* writable, because CEC is a conversation. An adapter that
    can be read but not written is the shape a permissions problem takes after
    a suspend or a SteamOS update, and the toolkit installs a helper and a
    udev rule to repair exactly that - so this being false is a repairable
    state and worth telling apart from having no adapter at all.
    """
    found = device(status)
    return bool(found.get("exists") and found.get("readable")
                and found.get("writable"))


def missing(module_check=None, which=None):
    """The programs and modules CEC needs that this machine does not have.

    Returned as (name, why) pairs, in the order of NEEDS with the python
    module last. Both lookups are injectable so the answer can be tested on a
    machine that has none of them, which is the machine this runs on.
    """
    which = which or shutil.which
    absent = [(name, why) for name, why in NEEDS if not which(name)]
    # Three of the services import it, and its absence is the one requirement
    # the toolkit's installer warns about rather than refuses over - so it is
    # a thing you can install into and only find out about from a log.
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
