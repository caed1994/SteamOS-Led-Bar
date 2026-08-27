# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""How the CPU trades speed against power, and how to change it.

Two knobs, and they are not independent.

The *governor* decides the clock. The *EPP* - energy performance preference -
is a hint to the firmware about where in its range to sit. Which of them exist
depends on the cpufreq driver, and the two families behave the same way:

    amd-pstate / intel_pstate, active   performance and powersave, plus an EPP
    amd-pstate / intel_cpufreq, passive the classic governors, usually no EPP
    acpi-cpufreq and older              the classic governors, no EPP at all

Nothing here is written for one vendor. Everything but one reporting line goes
through the generic cpufreq files, which is why this works on Intel as it does
on the AMD part a Steam Machine has - the pinning rule below included, since
intel_pstate pins the preference under `performance` exactly as amd-pstate
does. The exception is driver_mode, which reads a file only AMD publishes and
is reported beside driver() rather than instead of it.

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

DRIVER = "scaling_driver"
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

# What the drivers with an EPP offer, for the one moment the machine will not
# say. While the pinning governor is set the kernel collapses
# energy_performance_available_preferences to that single value - so a list
# read then reports what is choosable *now* rather than what the hardware can
# do, and switching away from that governor in the same sitting would be
# choosing from a list of one.
#
# These five are amd-pstate's and intel_pstate's alike - between them every
# machine that has this file at all, which is why one list serves both. Used
# only while the reported list is untrustworthy; anywhere else the machine is
# asked.
PINNED_FALLBACK = ("default", "performance", "balance_performance",
                   "balance_power", "power")

DEFAULTS = {
    "CPU_GOVERNOR": UNSET,
    # Not UNSET. The preference is only ever written alongside a governor -
    # see epp_in_play - so there is no case where "leave the file alone" is a
    # thing this can mean: either the CPU is being managed here, in which case
    # both are set, or it is not, in which case neither is written.
    "CPU_EPP": "default",
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


def driver(root=""):
    """Which cpufreq driver is running: intel_pstate, amd-pstate-epp, ...

    The generic answer, and the one worth leading a report with. Every machine
    with cpufreq has this file, where amd_pstate/status exists on exactly one
    family - so a report built on that one alone told an Intel machine only
    what it was not.
    """
    found = policies(root)
    if not found:
        return ""
    return _read(os.path.join(found[0], DRIVER))


def driver_mode(root=""):
    """What amd_pstate says it is in, or "" when it is not amd_pstate.

    Only AMD publishes its mode under a name like this; on Intel the same
    distinction is in the driver's own name - intel_pstate is the active one
    and intel_cpufreq is that driver in passive mode. So this is reported
    beside driver() rather than instead of it.

    Worth reporting rather than only acting on: active and passive offer
    different governors and only one of them reliably has an EPP, so a page
    that showed the wrong set would look broken to somebody who had read the
    wiki.
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
    """What the preference will take once a governor that allows it is set.

    Not simply what the file says right now. While the pinning governor is
    running the kernel collapses this list to that one value, so reading it
    then answers "what may I choose at this instant" - and the question being
    asked is always "what may I choose once I have applied the governor I am
    about to apply", which is a different one.

    This is the bug that reached a screenshot: with the performance governor
    already applied, picking powersave and a preference in the same sitting
    was refused, because the check read a list of one.
    """
    reported = _offered(EPP_AVAILABLE, root)
    if not reported:
        return ()                       # no EPP on this machine at all
    if epp_applies(current(root).get("CPU_GOVERNOR", "")):
        return reported
    return PINNED_FALLBACK


def epp_in_play(values, root=""):
    """Whether the preference is a setting at all, for these values.

    Three ways it is not, and all three mean the same thing to everything
    that asks: do not offer it, do not validate it, do not write it.

    Without a governor of ours, because then this project is not managing how
    the CPU runs and should not be asserting half of it - a preference set
    behind a governor nobody chose is a setting whose effect depends on what
    SteamOS happens to default to. Under the pinning governor, because the
    kernel refuses the file. And on a machine whose driver has no such file.
    """
    if values.get("CPU_GOVERNOR", UNSET) == UNSET:
        return False
    if not epp_applies(values.get("CPU_GOVERNOR", UNSET)):
        return False
    return bool(epp_values(root))


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
        "driver": driver(root),
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
    governor = values.get("CPU_GOVERNOR", UNSET)
    if governor != UNSET and governor not in governors(root):
        offered = governors(root)
        raise ValueError(
            "CPU_GOVERNOR=%s is not one this machine offers%s"
            % (governor, (": " + ", ".join(offered)) if offered
               else " - it has no cpufreq at all"))

    # Only when it is a setting at all. Checked against what will be on offer
    # once the governor above has been applied, which is not what the file
    # reports while a pinning governor is still running - see epp_values.
    if not epp_in_play(values, root):
        return
    epp = values.get("CPU_EPP", UNSET)
    if epp != UNSET and epp not in epp_values(root):
        raise ValueError(
            "CPU_EPP=%s is not one this machine offers: %s"
            % (epp, ", ".join(epp_values(root))))
