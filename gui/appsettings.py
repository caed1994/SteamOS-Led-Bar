# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""The panel's own preferences - about the window, not about the machine.

A third kind of setting, and the smallest. The LED service's live in /etc and
are validated by the service; the machine's live in the user's home and change
how the computer runs; these change nothing but how this window looks, and
nothing outside the window ever reads them.

So they get a file of their own rather than a corner of somebody else's. It is
the user's, needs no rights, and losing it costs a preference rather than a
setting - which is why nothing here refuses to start over a file it cannot
read.

No tkinter, the same as the rest of the gui modules that are not the window.
"""

from __future__ import annotations

import os
import tempfile

# How the window is coloured, independent of the desktop.
#
# Three, not two. Following the desktop is what this panel did until the look
# was rebuilt around a dark window, and it is a perfectly good answer - it is
# just no longer the only one. Keeping it means the setting adds a choice
# rather than taking one away.
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
CONFIG_FILE = "steamos-led-panel.conf"

HEADER = ("# Settings for the SteamOS LED bar control panel itself.\n"
          "# Only this window reads them; nothing here changes the machine.\n")


class SettingError(ValueError):
    """A value that is not one of the choices. Named for the panel to catch."""


def path(home=None):
    """Where the file lives. `home` is a parameter so a test can move it."""
    return os.path.join(home or os.path.expanduser("~"), CONFIG_DIR,
                        CONFIG_FILE)


def read(home=None):
    """The preferences, defaults for anything missing or unreadable.

    A file that has never been written is the ordinary state, and a file with
    a value this version has never heard of is somebody's future or somebody's
    typo - neither is a reason for the panel not to open, so both come back as
    the default.
    """
    values = dict(DEFAULTS)
    try:
        with open(path(home), encoding="utf-8", errors="replace") as handle:
            lines = handle.read().splitlines()
    except OSError:
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
