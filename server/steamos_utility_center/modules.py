# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""Which parts of this project a machine has, and what each part does.

install.sh installs the panel, the control command and the shared code. That
is the whole of the core. The LED bar, the CPU and GPU power, HDMI CEC and the
drives are modules. A person takes each module from its own page in the panel,
and gives it back on the same page.

The reason is the cost of the parts. The LED module builds a kernel module and
downloads PlatformIO, and a person with no ESP board on the machine paid that
cost for nothing. The other three are smaller, and the same argument holds:
a unit that nobody asked for is a unit that a person must examine.

-- how this file answers "is it installed" ---------------------------------

Each module is present when one file is present, and that file is the applier
of the module. There is no record of "what the person asked for" beside it.

A record and a machine can disagree, and then one question has two answers.
This project met that shape twice already, in the LACT status and in the
handover out of Game Mode, and both times the cost was an evening.

The applier is also the file that the sudoers rule names. So the rule is
exactly the list of installed modules, and neither of the two can move away
from the other. See ctl.sudoers_text.

HDMI CEC has no applier. It is somebody else's toolkit, it installs into the
home directory of the user, and it answers this question itself. This file
asks it rather than keeping a second answer. See cec.installed.
"""

from __future__ import annotations

import os

from . import cec

# Where an installation puts the programs that need root. The same directory
# as ctl.INSTALL_DIR. It is spelled here and not imported, because ctl imports
# this file and a circle between the two is a circle for one string.
INSTALL_DIR = "/var/lib/steamos-utility-center"

LED = "led"
POWER = "power"
CEC = "cec"
SYSTEM = "system"

# The order of the pages of the panel, so a list of modules reads in the order
# a person meets them.
ORDER = (LED, POWER, CEC, SYSTEM)


# What each module is, in the words the panel puts on the page.
#
# `title` names it. `does` says what a person gets. `brings` says what lands on
# the machine, because a module is an install and a person must know what an
# install writes before they press the button.
#
# The text is here and not in the panel. The installer prints the same list
# for --modules, and two copies of one sentence become two different
# sentences.
SAYS = {
    LED: {
        "title": "LED bar",
        "does": "Lights the strip on the case from Steam. It shows the "
                "game you play, achievements, messages, the CPU and GPU "
                "load, and a light while the machine sleeps.",
        "brings": "the LED service, the serial settings, the "
                  "leds-valve-shim kernel module, PlatformIO for the ESP "
                  "firmware, and the two watchers that run in your session.",
        "needs": "an ESP board on USB, with a WS2812 strip on it.",
    },
    POWER: {
        "title": "CPU and GPU power",
        "does": "Sets the governor and the energy preference of the CPU. It "
                "also sets the power limits, the clocks and the fan of the "
                "graphics card through LACT.",
        "brings": "a program that applies the settings, a unit that applies "
                  "them again at each boot, and the switch that wakes the "
                  "television after a resume.",
        "needs": "LACT for the graphics card. The CPU part needs nothing.",
    },
    CEC: {
        "title": "HDMI CEC",
        "does": "Talks to the television over the HDMI cable. It wakes the "
                "screen, switches the input, turns the television off with "
                "the machine, and puts its volume on the volume buttons.",
        "brings": "the SteamOS CEC Toolkit, almost all of it somebody "
                  "else's work, kept in this repository under cec-toolkit/.",
        "needs": "a CEC adapter, and a television that answers on it.",
    },
    SYSTEM: {
        "title": "Drives and Game Mode",
        "does": "Mounts the drives you choose at each boot, and puts the "
                "plugin for Game Mode into Decky Loader. The keyboard "
                "layout above works without this module.",
        "brings": "a program that writes the mount units, a unit that writes "
                  "them again at each boot, and the Decky plugin.",
        "needs": "Decky Loader, for the Game Mode plugin only.",
    },
}

# The file that says a module is on this machine. See the note at the top.
#
# HDMI CEC is not here. It is in the home directory of the user, so its answer
# needs that home and a constant cannot hold it. See installed().
MARK = {
    LED: os.path.join(INSTALL_DIR, "steamos-utility-center-config-apply"),
    POWER: os.path.join(INSTALL_DIR, "steamos-utility-center-power-apply"),
    SYSTEM: os.path.join(INSTALL_DIR, "steamos-utility-center-mounts-apply"),
}


def title(name):
    """The name of one module, for a sentence."""
    return SAYS[name]["title"] if name in SAYS else name


def installed(name, home=None, present=None):
    """Whether one module is on this machine.

    `present` is a parameter so that a test can answer for a machine it did
    not build. `home` reaches HDMI CEC, whose files are in a home directory.
    """
    if name == CEC:
        return cec.installed(home)
    where = MARK.get(name)
    if not where:
        return False
    present = os.path.exists if present is None else present
    return bool(present(where))


def here(home=None, present=None):
    """The modules on this machine, in the order of the pages."""
    return tuple(name for name in ORDER
                 if installed(name, home=home, present=present))


def known(names):
    """Splits a list of module names into the known ones and the rest.

    The installer takes names from a person, so a name that is not a module
    must give a message and not a silent skip.
    """
    good, bad = [], []
    for name in names:
        if not name:
            continue
        (good if name in ORDER else bad).append(name)
    # In the order of the pages, and each name one time. A person can type
    # "led,led" and the installer must install it one time.
    return tuple(one for one in ORDER if one in good), tuple(bad)
