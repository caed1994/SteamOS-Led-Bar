# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""Talking to the television over HDMI, through the CEC toolkit.

Almost none of the CEC work is ours. It is the SteamOS CEC Toolkit, a fork of
somebody else's project kept as a module of its own under cec-toolkit/ - see
cec-toolkit/ORIGIN for where it came from and cec-toolkit/README.md for what
was changed in it and why.

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

# The toolkit's tree, relative to the repository root. Its installer is run
# from inside it and reads its own siblings, so it cannot be copied out on its
# own - see scripts/install-cec.sh.
SOURCE = "cec-toolkit"

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
# What actually speaks CEC, for the one action below that does not go through
# the toolkit at all.
CEC_CTL = "cec-ctl"

# Which adapter to speak to. Read from the toolkit's own status rather than
# guessed - see config() - and falling back to what the toolkit itself falls
# back to, so a machine with the setting missing is asked the same question
# the toolkit would ask.
DEVICE_SETTING = "CEC_DEVICE"
DEFAULT_DEVICE = "/dev/cec0"

# The setting that says which device is supposed to render the sound.
AUDIO_ADDRESS = "CEC_AUDIO_LOGICAL_ADDRESS"


def configured_device(settings):
    """Which adapter the toolkit is set to talk to."""
    return str(settings.get(DEVICE_SETTING, "")).strip() or DEFAULT_DEVICE

# The one action here that is not a toolkit subcommand, because the question
# is not about the toolkit.
#
# Volume over CEC is the System Audio Control feature, and it is written for
# an amplifier. A television that does not implement it does not say so: it
# accepts the volume key, acts on nothing, and answers nothing - so the switch
# looks broken and everything in the log says the message was sent. That was
# an evening's work to establish once.
#
# Asked directly, the same television answers immediately. On the set this was
# found on:
#
#   GIVE_SYSTEM_AUDIO_MODE_STATUS (0x7d)
#       Received from TV (0): FEATURE_ABORT reason: refused (0x04)
#       Approximate response time: 26 ms
#
# Which is the difference between "will not" and "did not hear", and it is
# worth a button.
AUDIO_PROBE = "audio-probe"

ACTIONS = (
    ("wake", "Wake the television", ["wake"]),
    ("standby", "Send standby", ["standby"]),
    ("volume-up", "Volume up", ["volume", "up"]),
    ("volume-down", "Volume down", ["volume", "down"]),
    (AUDIO_PROBE, "Ask about volume", None),
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


def audio_probe_command(settings=None):
    """Ask whichever device is meant to have the volume whether it does.

    A refusal comes back as FEATURE_ABORT in a few milliseconds; a device
    that does implement it reports its mode. Either is an answer, which is
    more than sending a volume key gets you - see AUDIO_PROBE.
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
# The one thing on the CEC page read straight from a helper rather than out of
# `toolkitctl status`, because the status does not carry it: it says whether
# the usb-wake service is enabled, not which radios it found - and "enabled,
# and still nothing wakes it" is exactly the state somebody is in when they
# ask. The helper's own `status` lists what it matched and whether each is
# allowed to wake the machine, which is the answer.
#
# No password. The toolkit's installer writes a sudoers rule for exactly this
# program, so this asks for nothing, the way every switch on the page does not.
USB_WAKE_HELPER = "/var/lib/steamos-cec-toolkit/steamos-cec-usb-wake-control"


def wake_radios_command():
    """Ask the toolkit which radios it found and whether they may wake."""
    return ["sudo", "-n", USB_WAKE_HELPER, "status"]


def wake_radios_said(text):
    """What that answer means, in a sentence.

    Three answers, told apart on purpose. They were not, once: finding nothing
    and finding one that was already allowed printed the same line, so a
    machine where this does not work at all read exactly like one where there
    was nothing left to do - which is no way to answer "did it find mine?".
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
                "\u201cLet a controller wake the machine\u201d above." % named)
    return ("Found %s, of which %d of %d may wake this machine."
            % (named, len(waking), len(radios)))


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
