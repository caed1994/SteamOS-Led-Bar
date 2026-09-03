# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""Holds the preferences of the panel. They apply to the window only.

This is the third type of setting, and the smallest one. The settings of the
LED service are in /etc, and the service validates them. The settings of the
machine are in the home directory of the user, and they change how the
computer runs. These settings change the look of this window only, and no
program outside the window reads them.

So they get a file of their own, and not a section in the file of another
program. The file belongs to the user and needs no rights. A lost file costs
a preference and not a setting. For that reason, this module always starts,
also when it cannot read the file.

This module does not use tkinter. The other gui modules that are not the
window do the same.
"""

from __future__ import annotations

import os
import tempfile

# How the window is coloured, independent of the desktop.
#
# Three values, and not two. Before the new dark look, this panel always
# followed the desktop. That is still a good answer, but it is not the only
# answer now. The third value keeps that answer available. The setting then
# adds a choice and does not remove one.
THEME = "THEME"
THEME_DARK = "dark"
THEME_LIGHT = "light"
THEME_SYSTEM = "system"

# Dark leads because it is what the window is built for: this is a panel for a
# strip of light, and its preview page is judged against the window round it.
THEMES = (THEME_DARK, THEME_LIGHT, THEME_SYSTEM)

THEME_LABELS = {
    THEME_DARK: "Dark",
    THEME_LIGHT: "Light",
    THEME_SYSTEM: "Follow the desktop",
}

DEFAULTS = {
    THEME: THEME_DARK,
}

CONFIG_DIR = ".config"
CONFIG_FILE = "steamos-utility-center-panel.conf"

# The name of this file when the project was the SteamOS LED bar. Read it
# when the current name is not on disk. The theme of the user then survives
# the change of name, also when the user does not run the installer again.
# The installer moves the file too. But the user opens the panel, and the
# panel can do this without root rights.
#
# Read, not written: the next write lands under the current name, and the old
# file is left alone. Deleting somebody's file to tidy up is not this window's
# business, and it costs one stat on a start where the new one is missing.
OLD_CONFIG_FILE = "steamos-led-panel.conf"

HEADER = ("# Settings for the SteamOS Utility Center itself.\n"
          "# Only this window reads them; nothing here changes the machine.\n")


class SettingError(ValueError):
    """A value that is not one of the choices. Named for the panel to catch."""


def path(home=None):
    """Where the file lives. `home` is a parameter so a test can move it."""
    return os.path.join(home or os.path.expanduser("~"), CONFIG_DIR,
                        CONFIG_FILE)


def _lines(where):
    """The file's lines, or None when there is no file to read."""
    try:
        with open(where, encoding="utf-8", errors="replace") as handle:
            return handle.read().splitlines()
    except OSError:
        return None


def read(home=None):
    """Returns the preferences, with a default for each bad or absent value.

    A file that no program wrote is the normal condition. A file with a value
    that this version does not know is a value from a later version, or a
    spelling error. Neither condition must stop the panel. So this function
    returns the default in both conditions.
    """
    values = dict(DEFAULTS)
    lines = _lines(path(home))
    if lines is None:
        # There is no file with the current name. This can be a first
        # start. It can also be a user who installed the panel before the
        # change of name. See OLD_CONFIG_FILE.
        lines = _lines(os.path.join(home or os.path.expanduser("~"),
                                    CONFIG_DIR, OLD_CONFIG_FILE))
    if lines is None:
        return values
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, sign, value = line.partition("=")
        if not sign or name.strip() not in values:
            continue
        value = value.strip().strip("\"'")
        if name.strip() == THEME and value not in THEMES:
            continue                    # not a choice this version has
        values[name.strip()] = value
    return values


def validate(values):
    theme = values.get(THEME, DEFAULTS[THEME])
    if theme not in THEMES:
        raise SettingError("%s must be one of: %s" % (THEME, ", ".join(THEMES)))


def write(values, home=None):
    """Save them. Written whole, because the whole file is ours.

    Unlike the keyboard layout's file, which is shared with whatever else
    somebody puts in environment.d, nothing but this window ever writes here -
    so there are no lines of anybody else's to preserve.
    """
    validate(values)
    where = path(home)
    os.makedirs(os.path.dirname(where), exist_ok=True)
    text = HEADER + "".join(
        "%s=%s\n" % (key, values.get(key, DEFAULTS[key])) for key in DEFAULTS)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=os.path.dirname(where),
        prefix=".%s." % CONFIG_FILE, delete=False)
    try:
        with handle:
            handle.write(text)
        os.chmod(handle.name, 0o644)
        os.replace(handle.name, where)
    except OSError:
        try:
            os.unlink(handle.name)
        except OSError:                                      # pragma: no cover
            pass
        raise


def theme_choices():
    """(label, value) pairs for the menu, in the order THEMES has them."""
    return tuple((THEME_LABELS[name], name) for name in THEMES)
