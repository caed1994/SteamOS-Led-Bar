# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""One control surface for this project, which speaks JSON.

The panel imports these modules and calls them. A plugin in Game Mode cannot:
it is a process of another program, with a web page inside Steam for a front
end. It needs a command that prints an answer.

    steamos-utility-centerctl status
    steamos-utility-centerctl get strip
    steamos-utility-centerctl set strip '{"NOTIFY": false}'
    steamos-utility-centerctl action cec-wake

`set` takes a JSON object rather than one command for each setting, so a new
setting is no work here at all. See AREA.

Three rules give this file its shape.

**One object on stdout, and nothing else.** A message for a person goes to
stderr. Every command prints an object with `ok` in it, and the exit status
agrees with that field.

**`status` starts no process.** A front end asks for it again and again while
a person watches a page, so it reads files and nothing more. `status --full`
is the half that needs `systemctl`, the toolkit and lsblk, asked one time when
a page opens.

**Nothing asks for a password unless the caller permits it.** Game Mode runs
no polkit agent and gives no terminal, so `pkexec` there fails after a delay
and says nothing. This uses `sudo -n`, which works or refuses at once, and the
refusal names the file that would make it work. `--may-prompt` is for a caller
with a desktop around it.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

from . import cec
from . import config as config_module
from . import lact
from . import modules
from . import mounts
from . import power
from . import shim
from . import syssettings

# Where an installation puts the programs that need root. A stable path, and
# not a path inside somebody's clone: a sudoers rule names the program it
# permits, and a rule for a path that a person can move permits whatever that
# person moves there.
INSTALL_DIR = "/var/lib/steamos-utility-center"
APPLY_CONFIG = os.path.join(INSTALL_DIR, "steamos-utility-center-config-apply")
APPLY_POWER = os.path.join(INSTALL_DIR, "steamos-utility-center-power-apply")
APPLY_MOUNTS = os.path.join(INSTALL_DIR, "steamos-utility-center-mounts-apply")
# The switch that wakes the television after a resume. It is a unit of root,
# so it needs a program of its own with a rule of its own. See
# scripts/resume-wake.sh, which says why it is not scripts/install-cec.sh.
RESUME_WAKE = os.path.join(INSTALL_DIR, "steamos-utility-center-resume-wake")

# Which applier belongs to which area. The panel reads this to build the same
# command that this file runs, so the two never name different programs.
APPLIER = {"strip": APPLY_CONFIG, "power": APPLY_POWER,
           "drives": APPLY_MOUNTS}

CONFIG_PATH = "/etc/steamos-utility-center.conf"

# Where a change waits while the applier reads it, and one fixed name for each.
#
# A temporary file with a name that nobody knows in advance needs a `*` in the
# sudoers rule, and a rule with a `*` in it permits every argument. These
# names are fixed, so each rule names exactly one file and permits nothing
# else. The installer makes this directory and gives it to the desktop user.
# Its parent belongs to root, so nobody can put a symlink in the place of it.
STAGED_DIR = os.path.join(INSTALL_DIR, "staged")
STAGED = {"strip": os.path.join(STAGED_DIR, "strip.conf"),
          "power": os.path.join(STAGED_DIR, "power.conf"),
          "drives": os.path.join(STAGED_DIR, "mounts.conf")}

# Cooling Boost holds the fan of the card at the full speed it has. 1.0 is
# how LACT counts a fan that runs as fast as it can.
BOOST_SPEED = 1.0

# Where the fan settings wait while Cooling Boost has the fan.
#
# In the state directory of the person who pressed the switch, and not in
# /var/lib. It needs no root, and it answers "what was set before", which is
# that person's own answer. A machine with no such file gives the fan back to
# the card, and that is the state of a card that nobody changed.
BOOST_MEMORY = os.path.join(".local", "state", "steamos-utility-center",
                            "gpu-fan.json")

# The rule that lets the appliers above run with no password. The installer
# writes it, with one line for each module that is installed. Its absence is
# the usual reason a `set` fails, so the answer names it.
SUDO_RULE = "/etc/sudoers.d/zz-steamos-utility-center"

# Our own units, for the full status.
UNITS = ("steamos-utility-center.service",
         "steamos-utility-center-power.service",
         "steamos-utility-center-mounts.service")

# What sudo says when the rule above is missing or does not cover the program.
# sudo -n never asks; it prints one of these and exits.
REFUSAL_SIGNS = ("a password is required", "no tty present",
                 "a terminal is required", "not allowed to execute",
                 "may not run", "sudo: a password")


class CtlError(ValueError):
    """A refusal with a sentence for the caller. It is not a stack trace.

    A ValueError, as mounts.MountError and syssettings.SettingError are. Every
    refusal in this project thus has one base, and a caller that catches that
    one catches all of them.
    """


def _run(command, timeout=120):
    """Runs a command and returns (exit status, what it printed)."""
    try:
        done = subprocess.run(command, capture_output=True, text=True,
                              timeout=timeout, check=False)
    except FileNotFoundError:
        return 127, "no such program: %s" % command[0]
    except subprocess.TimeoutExpired:
        return 124, "%s did not answer in %d seconds" % (command[0], timeout)
    return done.returncode, (done.stdout or "") + (done.stderr or "")


def escalate(command, may_prompt=False):
    """Returns how to run a privileged program from here.

    `sudo -n` is the default because it never asks. In Game Mode a question
    has nobody to answer it: there is no polkit agent and no terminal.
    """
    if may_prompt:
        return ["pkexec"] + list(command)
    return ["sudo", "-n"] + list(command)


def refused_for_rights(said):
    """Reports whether sudo refused because nothing permits this program."""
    lowered = (said or "").lower()
    return any(sign in lowered for sign in REFUSAL_SIGNS)


def module_of(program):
    """Which module installs one privileged program, or "" for none.

    modules.MARK is the one place that pairs a module with its applier. This
    adds the switch for the wake after a resume, which is the power module's
    program and is not the file that marks it.
    """
    for name, where in modules.MARK.items():
        if where == program:
            return name
    return modules.POWER if program == RESUME_WAKE else ""


def privileged(command, may_prompt=False, run=None):
    """Runs one of the appliers, and turns a refusal into a sentence."""
    run = _run if run is None else run
    code, said = run(escalate(command, may_prompt))
    if code == 0:
        return said.strip()
    # A module that is not installed has no applier, and sudo reports that as
    # a refusal about rights. The message below would then tell a person to
    # reinstall for a rule that is already correct.
    #
    # After the call and not before it: a machine that answers has nothing to
    # explain.
    owner = module_of(command[0])
    if owner and not os.path.exists(command[0]):
        raise CtlError("the %s module is not installed on this machine. "
                       "Install it from its page in the panel, or with "
                       "install.sh --with %s." % (modules.title(owner), owner))
    if refused_for_rights(said):
        raise CtlError(
            "%s may not run this without a password. The installer writes %s "
            "to permit it. Reinstall, or use --may-prompt where a person can "
            "answer." % (os.path.basename(command[0]), SUDO_RULE))
    raise CtlError(said.strip() or "%s failed" % os.path.basename(command[0]))


def stage(text, path):
    """Writes text where the applier reads it, and returns the path it used.

    `path` is the fixed name a sudoers rule permits. With no such directory it
    writes a temporary file instead, and `sudo -n` then refuses the argument
    and names the rule that is missing. That is a better answer than a failure
    to write.

    Mode 0644, because the applier runs as root and reads it.
    """
    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(path, 0o644)
        return path
    except OSError:
        pass
    staged = tempfile.NamedTemporaryFile(
        "w", suffix=".conf", prefix="steamos-utility-center-ctl-", delete=False)
    with staged:
        staged.write(text)
    os.chmod(staged.name, 0o644)
    return staged.name


# -- the areas ---------------------------------------------------------------
#
# Each area answers three questions: what is set, what the machine offers, and
# how to write a change. Everything else in this file is the same for all of
# them, which is why a new setting inside an area costs nothing here.


def strip_read(home=None):
    """The settings of the LED service, defaults for what the file omits."""
    values = dict(config_module.DEFAULTS)
    if os.path.exists(CONFIG_PATH):
        values.update(config_module.parse_file(CONFIG_PATH))
    return values


def strip_offers():
    """The menus of the strip, so a front end does not carry its own copy."""
    return {"MAPPING": list(config_module.MAPPINGS),
            "NOTIFY_STYLE": list(config_module.NOTIFY_STYLES),
            "RAINBOW_SHOWS": list(config_module.RAINBOW_CHOICES),
            "DESKTOP_SCENE": list(config_module.DESKTOP_SCENES)}


def strip_write(updates, may_prompt=False, run=None, home=None):
    """Merges the updates into the file, and restarts the service.

    The whole file goes to the applier, and the applier asks the service
    whether it accepts it. A file that the service refuses would otherwise
    replace one that operates, and the service would not start again.
    """
    values = strip_read()
    values.update(updates)
    config_module.validate(values)      # raises ConfigError
    try:
        with open(CONFIG_PATH, encoding="utf-8") as handle:
            was = handle.read()
    except OSError:
        was = ""
    staged = stage(config_module.update_text(was, updates), STAGED["strip"])
    try:
        return privileged([APPLY_CONFIG, staged], may_prompt, run)
    finally:
        os.unlink(staged)


def power_read(home=None):
    """The CPU settings that the file holds, which is what a reboot restores."""
    return power.read()


def power_offers():
    """What this machine's cpufreq accepts, read at the moment of the ask."""
    offered = power.available()
    offered["labels"] = dict(power.LABELS)
    return offered


def power_write(updates, may_prompt=False, run=None, home=None):
    """Writes the CPU settings and puts them into effect."""
    values = power_read()
    values.update(updates)
    power.validate(values)              # raises ValueError
    staged = stage(power.text(values), STAGED["power"])
    try:
        return privileged([APPLY_POWER, staged], may_prompt, run)
    finally:
        os.unlink(staged)


def keyboard_read(home=None):
    """The keyboard layout, which is in the home directory of the user."""
    return syssettings.read(home)


def keyboard_offers():
    """Each layout that this machine has, with a name for each."""
    return {"XKB_DEFAULT_LAYOUT": [{"value": value, "label": label}
                                   for label, value in syssettings.layouts()]}


def keyboard_write(updates, may_prompt=False, run=None, home=None):
    """Writes the layout. This is the one area that needs no rights at all."""
    values = keyboard_read(home)
    values.update(updates)
    syssettings.validate(values)        # raises SettingError
    syssettings.write(values, home)
    return "keyboard layout written to %s" % syssettings.directory(home)


def drives_read(home=None):
    """The drives of the record, and whether each one is mounted now."""
    entries = mounts.read()
    found = [dict(entry, mounted=os.path.ismount(entry["where"]))
             for entry in entries]
    return {"drives": found,
            "missing_units": mounts.missing_units(entries)}


def drives_offers():
    """Nothing to offer without lsblk, which is a process. See status."""
    return {"partitions": "ask for the full status"}


def drives_write(updates, may_prompt=False, run=None, home=None):
    """Writes the whole list of drives, and mounts what it names.

    A drive is a record and not a setting, so this takes the list and does not
    merge into one. `{"drives": [...]}` replaces every drive with those.
    """
    if "drives" not in updates:
        raise CtlError('drives takes {"drives": [...]}, the whole list')
    entries = updates["drives"]
    if not isinstance(entries, list):
        raise CtlError("drives must be a list of drives")
    staged = stage(mounts.text(entries), STAGED["drives"])  # raises MountError
    try:
        return privileged([APPLY_MOUNTS, staged], may_prompt, run)
    finally:
        os.unlink(staged)


def cec_read(home=None):
    """Whether the toolkit is there. It is not the state of its switches.

    Each switch is a systemd unit, and only systemd knows whether it is
    enabled. That answer needs a process, so it is in status(full=True), which
    asks the toolkit. A front end reads the switches from there and polls
    this.
    """
    return {"installed": cec.installed(home)}


def cec_offers():
    """The switches of the toolkit and the actions it accepts."""
    return {"features": [{"name": name, "label": label, "explains": said}
                         for name, _kind, label, said in cec.FEATURES],
            "actions": [name for name, _label, _tail in cec.ACTIONS]}


def cec_write(updates, may_prompt=False, run=None, home=None):
    """Switches a feature of the toolkit, or writes a setting of it.

    A key that names a feature is a switch, and every other key is a setting.
    Neither needs root: a switch is a unit of the user, and a setting goes in
    a file of the user that has priority over the one in /etc.
    """
    run = _run if run is None else run
    said = []
    settings = {}
    for key, value in updates.items():
        if key not in cec.BY_NAME:
            settings[key] = value
            continue
        if cec.BY_NAME[key][0] == cec.RESUME_WAKE:
            # A unit of root, so this one goes through a program of ours that
            # the sudoers rule permits. Every other switch is a unit of the
            # user, or a helper that the toolkit's own rule permits.
            said.append(privileged([RESUME_WAKE, "on" if value else "off"],
                                    may_prompt, run))
            continue
        code, answer = run(cec.toggle_command(key, bool(value), home))
        if code != 0:
            raise CtlError(answer.strip() or "the toolkit refused %s" % key)
        said.append(answer.strip())
    if settings:
        code, answer = run(cec.set_config_command(settings, home))
        if code != 0:
            raise CtlError(answer.strip() or "the toolkit refused the settings")
        said.append(answer.strip())
    return "\n".join(one for one in said if one)


def gpu_read(home=None):
    """Whether there is a daemon to speak to. It is not the card itself.

    A file test, so this stays in the half that costs nothing. Every value of
    the card comes over a socket, and those are in gpu_offers, which a caller
    asks for when a page opens.
    """
    return {"available": lact.available()}


def gpu_offers():
    """Each control that this card publishes, with its range and its value.

    From the card and not from a table here. A control with no range writes
    nowhere. See lact.offered.

    The fan curve and the firmware settings belong to the window of LACT.
    `boost` is the one fan answer here, because a page has a switch for it. It
    reads the card and not the file that _gpu_boost writes: somebody can set
    the same speed in LACT, and a switch that reported a file would disagree
    with the machine.

    Here and not in gpu_read, which opens files only because `status` calls it
    while somebody watches a page.
    """
    found = lact.state()
    if not found or not found.get("gpu"):
        return {"knobs": [], "gpu": "", "name": "", "boost": False}
    return {"knobs": lact.offered(found.get("config") or {},
                                  found.get("clocks") or {},
                                  found.get("stats") or {}),
            "gpu": found["gpu"], "name": found.get("name", ""),
            "boost": boosting(lact.fan(found.get("config") or {}))}


def gpu_write(updates, may_prompt=False, run=None, home=None):
    """Writes one or more controls of the card, and waits to be told to keep it.

    Two rules of LACT to follow. set_gpu_config replaces the whole document,
    so this starts from the document the daemon holds and changes the named
    keys only.

    The daemon takes the change back after some seconds unless it is told to
    keep it. A voltage offset that is too low hangs the card, and a hang that
    was kept comes back at every boot. This returns the seconds, and the
    caller says `gpu-keep` while the machine is still there to say it.
    """
    found = lact.state()
    if not found or not found.get("gpu"):
        raise CtlError("no graphics card that LACT reports")
    known = {knob["key"]: knob for knob in lact.offered(
        found.get("config") or {}, found.get("clocks") or {},
        found.get("stats") or {})}
    config = found.get("config") or {}
    for key, value in updates.items():
        if key not in known:
            raise CtlError("this card has no %s. It has: %s"
                           % (key, ", ".join(sorted(known)) or "nothing"))
        config = lact.with_knob(config, key, round(float(value)),
                                known[key].get("scale", 1))
    seconds = lact.set_gpu_config(found["gpu"], config)
    return ("the card took it. Say gpu-keep in %s seconds, or the daemon "
            "puts it back." % seconds)


# The table that makes a new setting free. An area is four functions and one
# line here. A setting inside an area is nothing.
#
# "keys" is the names that area accepts. Without it, LED_COUTN=60 writes and
# the machine comes back from a reboot with no strip. `None` means the program
# behind the area decides, which is the CEC toolkit's own configuration.
AREA = {
    "strip": {"read": strip_read, "offers": strip_offers,
              "write": strip_write, "keys": tuple(config_module.DEFAULTS)},
    "power": {"read": power_read, "offers": power_offers,
              "write": power_write, "keys": tuple(power.DEFAULTS)},
    "keyboard": {"read": keyboard_read, "offers": keyboard_offers,
                 "write": keyboard_write, "keys": tuple(syssettings.DEFAULTS)},
    "drives": {"read": drives_read, "offers": drives_offers,
               "write": drives_write, "keys": ("drives",)},
    "cec": {"read": cec_read, "offers": cec_offers, "write": cec_write,
            "keys": None},
    # The card decides which controls it has, so there is no list here. A key
    # that this card does not publish is refused by gpu_write itself.
    "gpu": {"read": gpu_read, "offers": gpu_offers, "write": gpu_write,
            "keys": None},
}

AREAS = tuple(sorted(AREA))


def get(area, home=None):
    """One area: what is set, and what this machine offers for it."""
    if area not in AREA:
        raise CtlError("no such area: %s. There are: %s"
                       % (area, ", ".join(AREAS)))
    return {"area": area,
            "settings": AREA[area]["read"](home=home),
            "offers": AREA[area]["offers"]()}


def set_values(area, updates, may_prompt=False, run=None, home=None):
    """Writes a change into one area, and puts it into effect."""
    if area not in AREA:
        raise CtlError("no such area: %s. There are: %s"
                       % (area, ", ".join(AREAS)))
    if not isinstance(updates, dict):
        raise CtlError("the updates must be a JSON object")
    known = AREA[area]["keys"]
    if known is not None:
        unknown = sorted(key for key in updates if key not in known)
        if unknown:
            raise CtlError("%s does not have %s. It has: %s"
                           % (area, ", ".join(unknown), ", ".join(sorted(known))))
    said = AREA[area]["write"](updates, may_prompt=may_prompt, run=run,
                               home=home)
    return {"area": area, "written": sorted(updates), "said": said,
            "settings": AREA[area]["read"](home=home)}


# -- what is not a setting ---------------------------------------------------


def _cec_action(name):
    def action(may_prompt=False, run=None, home=None):
        run = _run if run is None else run
        code, said = run(cec.action_command(name, home))
        if code != 0:
            raise CtlError(said.strip() or "%s failed" % name)
        return said.strip()
    return action


def repair_drives(may_prompt=False, run=None, home=None):
    """Writes the mount units again and mounts what the record names.

    The same step the boot-time unit does, for a person who does not want to
    restart. It stages a copy of the record rather than name the record, so
    the repair needs no sudoers rule of its own.
    """
    staged = stage(mounts.text(mounts.read()), STAGED["drives"])
    try:
        return privileged([APPLY_MOUNTS, staged], may_prompt, run)
    finally:
        os.unlink(staged)


def restart_service(may_prompt=False, run=None, home=None):
    """Starts the LED service again, without a change to its settings."""
    return privileged(["systemctl", "restart", UNITS[0]], may_prompt, run)


# -- Cooling Boost -----------------------------------------------------------
#
# One switch that puts the fan of the card at its full speed, and gives the
# fan back when it goes off.
#
# It does not go through the sliders of a page. It reads the document the
# daemon holds, changes the fan in it, and writes it back, so a person who
# moved a slider and did not send it keeps what that slider holds.


def boosting(fan):
    """Whether these fan settings are the ones Cooling Boost writes.

    A comparison of the speed, and not a flag. The card is the answer: a
    person can set the same speed in the window of LACT, and a switch that
    read a flag of ours is a switch that disagrees with the machine.
    """
    return (bool(fan.get("enabled"))
            and fan.get("mode") == lact.FAN_STATIC
            and float(fan.get("static_speed") or 0) >= BOOST_SPEED - 0.005)


def _boost_memory(home=None):
    """The file that holds the fan settings from before the boost."""
    where = os.path.expanduser("~") if home is None else home
    return os.path.join(where, BOOST_MEMORY)


def _remember_fan(fan, home=None):
    """Writes down the fan settings that the boost is about to replace.

    A failure here is not a reason to refuse the boost. The switch then gives
    the fan back to the card when it goes off, and that is where the fan of
    most cards is.
    """
    path = _boost_memory(home)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"enabled": bool(fan.get("enabled")),
                       "mode": fan.get("mode"),
                       "static_speed": fan.get("static_speed"),
                       "curve": {str(at): speed for at, speed
                                 in (fan.get("curve") or {}).items()}},
                      handle)
    except (OSError, TypeError, ValueError):
        pass


def _remembered_fan(home=None):
    """The settings from before the boost, as arguments for lact.with_fan.

    With no file, or with a file that says the card had its own fan, this
    gives the fan back to the card. That is the safe answer: the firmware of
    the card always drives the fan, and a curve out of a stale file does not.
    """
    try:
        with open(_boost_memory(home), "r", encoding="utf-8") as handle:
            found = json.load(handle)
    except (OSError, ValueError):
        return {"enabled": False}
    if not isinstance(found, dict) or not found.get("enabled"):
        return {"enabled": False}
    return {"enabled": True,
            "mode": found.get("mode") or lact.FAN_CURVE,
            "static_speed": found.get("static_speed"),
            "curve": found.get("curve") or None}


def _forget_fan(home=None):
    """Drops the memory, so a later boost writes down what is set then."""
    try:
        os.unlink(_boost_memory(home))
    except OSError:
        pass


def _gpu_boost(on):
    """Builds the action behind the switch. See the block above it.

    It confirms the change itself, and gpu_write does not. The countdown of
    LACT is for a voltage that hangs the card. A fan at full speed hangs
    nothing, and a switch that needs a second press to stay on is a switch
    nobody trusts.
    """
    def action(may_prompt=False, run=None, home=None):
        found = lact.state()
        if not found or not found.get("gpu"):
            raise CtlError("no graphics card that LACT reports")
        config = found.get("config") or {}
        fan = lact.fan(config)
        if on:
            # Not while the boost already has the fan. A second press would
            # write down the boost itself as the state to come back to.
            if not boosting(fan):
                _remember_fan(fan, home)
            config = lact.with_fan(config, enabled=True,
                                   mode=lact.FAN_STATIC,
                                   static_speed=BOOST_SPEED)
        else:
            config = lact.with_fan(config, **_remembered_fan(home))
        lact.set_gpu_config(found["gpu"], config)
        lact.confirm(keep=True)
        if not on:
            _forget_fan(home)
        return ("the fan is at full speed" if on
                else "the card has its fan back")
    return action


def gpu_keep(may_prompt=False, run=None, home=None):
    """Tells the daemon to keep the last change to the card."""
    lact.confirm(keep=True)
    return "kept"


def gpu_revert(may_prompt=False, run=None, home=None):
    """Tells the daemon to put the card back, without waiting for the clock."""
    lact.confirm(keep=False)
    return "put back"


ACTION = {
    "gpu-boost-on": _gpu_boost(True),
    "gpu-boost-off": _gpu_boost(False),
    "gpu-keep": gpu_keep,
    "gpu-revert": gpu_revert,
    "cec-wake": _cec_action("wake"),
    "cec-standby": _cec_action("standby"),
    "cec-volume-up": _cec_action("volume-up"),
    "cec-volume-down": _cec_action("volume-down"),
    "repair-drives": repair_drives,
    "restart-service": restart_service,
}

ACTIONS = tuple(sorted(ACTION))


def action(name, may_prompt=False, run=None, home=None):
    """Does one thing that is not a setting."""
    if name not in ACTION:
        raise CtlError("no such action: %s. There are: %s"
                       % (name, ", ".join(ACTIONS)))
    said = ACTION[name](may_prompt=may_prompt, run=run, home=home)
    return {"action": name, "said": said}


# -- the rule that makes a password unnecessary ------------------------------
#
# The text of the rule is here and not in install.sh. The paths in it must be
# the paths this file runs, and two copies of a path is two answers to one
# question. The installer asks this program to write the file.

# A user name as the passwd file spells one. It goes into a sudoers file, so a
# name with a space or a new line in it is a way to add a rule that nobody
# asked for.
NAME = re.compile(r"^[a-z_][a-z0-9_.-]*\$?$")


def sudoers_text(user, present=None):
    """The rule that lets one user apply a change with no password.

    One line for each applier, and each line names the one file that applier
    is permitted to read. There is no `*` in it: the argument of these
    programs is a file that they read as root.

    An applier that is not on the machine gets no line. The appliers are the
    modules, so this rule is the list of installed modules. See modules.py.

    Returns "" for a machine with no module. permit() then removes the rule.

    `present` is a parameter so a test can answer for a machine it did not
    build.
    """
    if not NAME.match(user or ""):
        raise CtlError("%r is not a user name" % user)
    present = os.path.exists if present is None else present
    permitted = [(applier, STAGED[area])
                 for applier, area in ((APPLY_CONFIG, "strip"),
                                       (APPLY_POWER, "power"),
                                       (APPLY_MOUNTS, "drives"))
                 if present(applier)]
    # The switch for the wake after a resume. Two lines rather than one with a
    # `*`: the argument is one of two words, so both words fit in the rule and
    # nothing else does.
    #
    # It arrives with the power module, so it is asked for on its own.
    if present(RESUME_WAKE):
        permitted.extend((RESUME_WAKE, state) for state in ("on", "off"))
    if not permitted:
        return ""
    lines = [
        "# Written by the SteamOS Utility Center. See",
        "# server/steamos_utility_center/ctl.py.",
        "#",
        "# Game Mode runs no polkit agent and gives no terminal, so pkexec",
        "# there has nobody to ask for a password. Without these lines every",
        "# setting of this project is a setting for the desktop only.",
        "#",
        "# Each line names one program and the one file it is permitted to",
        "# read. There is no wildcard: a rule with a `*` in it permits every",
        "# argument, and the argument of these programs is a file that they",
        "# read as root. The file is in a directory that belongs to %s, and"
        % user,
        "# the parent of that directory belongs to root, so nobody can put a",
        "# symlink in the place of it. The programs refuse a symlink also.",
        "#",
        "# The chown of Take ownership is deliberately not here. It walks a",
        "# whole drive as root, and it is a rare and deliberate act. It stays",
        "# in the panel, where a person answers for it.",
        "",
    ]
    for program, argument in permitted:
        lines.append("%s ALL=(root) NOPASSWD: %s %s"
                     % (user, program, argument))
    return "\n".join(lines) + "\n"


def permit(user, run=None, present=None):
    """Writes that rule, and makes the directory the rule names.

    visudo reads the file before it is installed. A sudoers file that does not
    parse takes sudo away from the machine.

    A machine with no module has nothing to permit, so this removes the rule.
    The installer runs it after every module change, in both directions.

    This needs root, and the installer is what runs it.
    """
    run = _run if run is None else run
    text = sudoers_text(user, present=present)
    if not text:
        code, said = run(["rm", "-f", SUDO_RULE])
        if code != 0:
            raise CtlError(said.strip() or "could not remove %s" % SUDO_RULE)
        return {"rule": "", "staged": STAGED_DIR, "user": user}
    staged = tempfile.NamedTemporaryFile("w", suffix=".sudoers", delete=False)
    with staged:
        staged.write(text)
    try:
        code, said = run(["visudo", "-c", "-f", staged.name])
        if code != 0:
            raise CtlError("the rule does not parse, so it is not installed: "
                           "%s" % said.strip())
        code, said = run(["install", "-m", "0440", staged.name, SUDO_RULE])
        if code != 0:
            raise CtlError(said.strip() or "could not write %s" % SUDO_RULE)
    finally:
        os.unlink(staged.name)

    # The directory the rule names, and the user it belongs to. Its parent
    # belongs to root, so this is the whole of what that user can write here.
    code, said = run(["install", "-d", "-m", "0755", "-o", user,
                      STAGED_DIR])
    if code != 0:
        raise CtlError(said.strip() or "could not make %s" % STAGED_DIR)
    return {"rule": SUDO_RULE, "staged": STAGED_DIR, "user": user}


# -- the status --------------------------------------------------------------


def _unit_state(name, run):
    """Whether one unit runs and whether it starts at boot."""
    code, said = run(["systemctl", "show", name, "--no-pager",
                      "--property=ActiveState", "--property=UnitFileState"])
    state = {"active": "", "enabled": ""}
    if code != 0:
        return state
    for line in said.splitlines():
        key, _, value = line.partition("=")
        if key == "ActiveState":
            state["active"] = value.strip()
        elif key == "UnitFileState":
            state["enabled"] = value.strip()
    return state


def _tried(question):
    """Returns what the question answered, or why it could not be answered."""
    try:
        return question()
    except Exception as exc:            # a probe, and every failure is data
        return {"error": "%s: %s" % (type(exc).__name__, exc)}


def _cec_status(run, home):
    """What the toolkit says about itself, or why it said nothing."""
    code, said = run(cec.status_command(home))
    if code != 0:
        return {"ok": False, "error": said.strip()}
    return cec.read_status(said)


def _cec_features(said, run):
    """Returns {name: whether it is on} for each switch of the toolkit."""
    if not isinstance(said, dict) or said.get("ok") is False:
        return {}
    # resume-wake is not in that document. It is a unit of root, and only
    # systemd knows it. See cec.resume_wake_command.
    code, answer = run(cec.resume_wake_command())
    carried = dict(said)
    carried[cec.RESUME_WAKE_REPORT] = (code == 0
                                       and cec.resume_wake_enabled(answer))
    return {name: cec.feature_on(carried, name) for name in cec.BY_NAME}


def status(full=False, run=None, home=None):
    """Every area at once.

    Without `full` this opens files and starts no process. That is the half a
    front end can ask for again and again while a person watches a page.
    """
    drives = AREA["drives"]["read"](home=home)["drives"]
    answer = {"areas": {name: AREA[name]["read"](home=home)
                        for name in AREAS},
              # The three answers that a front end puts at the top of a page.
              # Each one is a file that is there or is not, so this stays the
              # half that costs no process.
              "ready": {
                  "module": os.path.exists(shim.DEFAULT_DEVICE),
                  "cec": cec.installed(home),
                  "mounted": sum(1 for one in drives if one["mounted"]),
                  "drives": len(drives),
              },
              "sudo_rule": os.path.exists(SUDO_RULE),
              # Which parts this machine has. A front end that offers a
              # setting of a module that is not installed offers a setting
              # that cannot be applied, and the Game Mode plugin is a screen
              # where nobody can look in /var/lib to find out why.
              #
              # Each answer is one file that is there or is not, so this stays
              # the half that costs no process. See modules.py.
              "modules": list(modules.here(home=home)),
              "full": bool(full)}
    if not full:
        return answer

    # Each of these is its own question, and each is answered on its own. A
    # machine with no CEC adapter must still get its units and its drives: an
    # answer that is lost because one part of it failed is a page that shows
    # nothing and says why about the wrong thing.
    run = _run if run is None else run
    answer["units"] = _tried(lambda: {name: _unit_state(name, run)
                                      for name in UNITS})
    answer["partitions"] = _tried(mounts.partitions)
    answer["cec"] = _tried(lambda: _cec_status(run, home))
    # Whether each switch is on, worked out here and not by a front end. The
    # answer is in three different places of the toolkit's status document,
    # and cec.feature_on is the one function that knows which. A second copy
    # in TypeScript is a second answer.
    answer["cec_features"] = _tried(
        lambda: _cec_features(answer["cec"], run))
    answer["gpu"] = _tried(lambda: {"available": lact.available()})
    return answer


# -- the command line --------------------------------------------------------


def _parser():
    parser = argparse.ArgumentParser(
        prog="steamos-utility-centerctl",
        description="Read and write the settings of this project, in JSON.")
    parser.add_argument("--may-prompt", action="store_true",
                        help="permit a password question. A desktop only: "
                             "Game Mode has nothing that can ask.")
    where = parser.add_subparsers(dest="command", required=True)

    said = where.add_parser("status", help="every area at once")
    said.add_argument("--full", action="store_true",
                      help="add the answers that need a process")

    said = where.add_parser("get", help="one area")
    said.add_argument("area", choices=AREAS)

    said = where.add_parser("set", help="write into one area")
    said.add_argument("area", choices=AREAS)
    said.add_argument("updates", help="a JSON object of settings")

    said = where.add_parser("action", help="what is not a setting")
    said.add_argument("name", choices=ACTIONS)

    where.add_parser("areas", help="the areas and the actions of this build")

    said = where.add_parser(
        "permit", help="write the sudoers rule. The installer runs this.")
    said.add_argument("user", help="the desktop user the rule is for")
    return parser


def run_command(argv=None):
    """Returns the answer of one command, as the object that is printed."""
    parsed = _parser().parse_args(argv)
    if parsed.command == "status":
        return status(full=parsed.full)
    if parsed.command == "get":
        return get(parsed.area)
    if parsed.command == "set":
        try:
            updates = json.loads(parsed.updates)
        except ValueError as exc:
            raise CtlError("the updates are not JSON: %s" % exc)
        return set_values(parsed.area, updates, may_prompt=parsed.may_prompt)
    if parsed.command == "action":
        return action(parsed.name, may_prompt=parsed.may_prompt)
    if parsed.command == "permit":
        return permit(parsed.user)
    return {"areas": list(AREAS), "actions": list(ACTIONS)}


def main(argv=None):
    """Prints one JSON object, and exits with a status that agrees with it."""
    argv = sys.argv[1:] if argv is None else argv
    try:
        answer = dict(run_command(argv))
        answer["ok"] = True
    # ValueError covers the refusals of every module: ConfigError, MountError
    # and SettingError are all ValueError, and power.validate raises the plain
    # one. A refusal is an answer here and not a fault, so it is printed as
    # one rather than as a stack trace that a front end cannot read.
    except (CtlError, ValueError, OSError, lact.LactError,
            cec.CecError) as exc:
        answer = {"ok": False, "error": str(exc)}
    print(json.dumps(answer, sort_keys=True, default=str))
    return 0 if answer["ok"] else 1
