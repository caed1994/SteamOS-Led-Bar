# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""How the CPU trades speed against power, and how to change it.

Two knobs, and on this hardware they are not independent.

The *governor* decides the clock. The *EPP* - energy performance preference -
is a hint to the firmware about where in its range to sit. Which of them exist
depends on the driver: with amd-pstate in `active` mode, which is what a Steam
Machine ships with, the kernel offers only `performance` and `powersave` as
governors and adds an EPP file; in `passive` or `guided` mode the classic
governors are back and there is no EPP at all.

So nothing here has a list of its own. Everything offered is read out of sysfs
at the moment it is asked for, and a machine that offers neither gets a page
that says so rather than a menu that writes to files it does not have.

The one interaction worth knowing: under the `performance` governor the
firmware is pinned to its top preference and the EPP file stops accepting
anything else. That is the kernel's rule, not ours - see epp_applies.

Nothing in here writes anything. Applying needs root and lives in
steamos-led-power, which this module is the reading half of.
"""

from __future__ import annotations

import glob
import os

CPUFREQ = "/sys/devices/system/cpu/cpu*/cpufreq"
PSTATE_STATUS = "/sys/devices/system/cpu/amd_pstate/status"

GOVERNOR = "scaling_governor"
GOVERNORS_AVAILABLE = "scaling_available_governors"
EPP = "energy_performance_preference"
EPP_AVAILABLE = "energy_performance_available_preferences"

# What "leave it alone" is spelled as, the same way the keyboard layout does
# it: the absence of a setting rather than a value it could take. A machine
# with nothing set here is the ordinary one, and this project should not be
# holding an opinion about the CPU until somebody asks it to.
UNSET = ""

# The governor that pins the firmware to its top preference, so the EPP file
# is refused while it is set. Named because two places have to agree about it:
# the panel, which greys the EPP menu, and the applier, which does not treat
# that refusal as a failure.
PINNED_GOVERNOR = "performance"

DEFAULTS = {
    "CPU_GOVERNOR": UNSET,
    "CPU_EPP": UNSET,
}

# Wording for the values these files use, for anything showing them to a
# person. Only where the kernel's own word is not one: "powersave" reads like
# a mode you would only pick on battery, and on amd-pstate active it is the
# ordinary one - it is the setting that lets the firmware range at all, and
# `performance` is the one that pins it.
LABELS = {
    "powersave": "Powersave - the firmware picks the clock",
    "performance": "Performance - hold the top of the range",
    "default": "Driver's default",
    "balance_performance": "Lean towards performance",
    "balance_power": "Lean towards power saving",
    "power": "Power saving",
}


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def policies(root=""):
    """Every cpufreq policy directory, sorted. Empty when there is no cpufreq.

    Every CPU rather than the first: the files are per policy, and a machine
    that only had cpu0 written to would be running its cores under different
    governors with nothing on screen to say so.
    """
    return sorted(glob.glob(root + CPUFREQ))


def driver_mode(root=""):
    """What amd_pstate says it is in, or "" when it is not amd_pstate.

    Worth reporting rather than only acting on: `active` and `passive` offer
    different governors and only one of them has an EPP, so a page that showed
    the wrong set would look broken to somebody who had read the wiki.
    """
    return _read(root + PSTATE_STATUS)


def _offered(name, root=""):
    """The values one cpufreq file accepts, as reported by the first policy."""
    found = policies(root)
    if not found:
        return ()
    return tuple(_read(os.path.join(found[0], name)).split())


def governors(root=""):
    return _offered(GOVERNORS_AVAILABLE, root)


def epp_values(root=""):
    return _offered(EPP_AVAILABLE, root)


def current(root=""):
    """What is set right now, as {key: value}. Empty strings when unreadable."""
    found = policies(root)
    if not found:
        return {"CPU_GOVERNOR": UNSET, "CPU_EPP": UNSET}
    return {"CPU_GOVERNOR": _read(os.path.join(found[0], GOVERNOR)),
            "CPU_EPP": _read(os.path.join(found[0], EPP))}


def available(root=""):
    """Everything a page needs to build itself, in one read of the machine."""
    return {
        "mode": driver_mode(root),
        "governors": governors(root),
        "epp": epp_values(root),
        "policies": len(policies(root)),
        "current": current(root),
    }


def epp_applies(governor):
    """Whether the EPP setting reaches the firmware under this governor.

    False under `performance`, where the kernel pins the preference and
    refuses writes to the file. A page that offered the menu anyway would be
    offering a setting the machine will not take.
    """
    return governor != PINNED_GOVERNOR


def validate(values, root=""):
    """Refuse a value this machine does not offer.

    Checked against the machine rather than against a list here, because the
    list depends on the driver's mode - and a config file carried over from a
    machine in a different mode is exactly how you get a governor that does
    not exist written into sysfs.

    A machine with no cpufreq at all accepts only "leave it alone", which is
    what an unset setting already is.
    """
    for key, offered in (("CPU_GOVERNOR", governors(root)),
                         ("CPU_EPP", epp_values(root))):
        value = values.get(key, UNSET)
        if value == UNSET:
            continue
        if value not in offered:
            raise ValueError(
                "%s=%s is not one this machine offers%s"
                % (key, value,
                   (": " + ", ".join(offered)) if offered
                   else " - it has no cpufreq at all"))
