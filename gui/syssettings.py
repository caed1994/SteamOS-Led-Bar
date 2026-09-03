# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""Holds the settings that the panel controls and the LED service does not.

The other pages of this window write one file,
`/etc/steamos-utility-center.conf`. Each row on each page is a key in that
file. The settings here are different. They belong to the machine, they are
in the home directory of the user, and no service of this project reads them.

This module is separate from `steamos_utility_center.config` on purpose. That
file belongs to the service, the service validates it, and a change to it
restarts the service. A keyboard layout in that file gives two problems: the
service refuses to start on a value it does not know, and the change causes a
restart with no purpose.

These settings are the simple half of a utility panel, because none of them
needs root. The panel runs as the user, and each step here does the same. It
uses no pkexec, no polkit, and no helper script. A step that needs root
belongs in `scripts/`, as the step that applies the configuration of the
service does.

This module does not use tkinter, and ledpanel.py and preview.py do the same.
A machine with no display must be able to test the values of the settings.
"""

from __future__ import annotations

import os
import re
import tempfile

# -- the keyboard layout ---------------------------------------------------
#
# Game Mode has no keyboard settings. gamescope makes its keymap with
# libxkbcommon. If no other source gives a layout, libxkbcommon uses
# XKB_DEFAULT_LAYOUT. So this variable makes a German keyboard German in Game
# Mode, and also in the keyboard on the screen.
#
# This variable does not control Desktop Mode. Plasma sets its own layout with
# kxkbrc, and Plasma wins there. The page says this. Without that text, a
# setting with no visible result on the desktop looks like a fault.

LAYOUT = "XKB_DEFAULT_LAYOUT"

# What "do not set it" is spelled as. Empty rather than a word, because it is
# the absence of the variable rather than a value it could take: writing
# XKB_DEFAULT_LAYOUT= would hand libxkbcommon an empty layout, which is not
# the same as never having mentioned it.
UNSET = ""

DEFAULTS = {
    LAYOUT: UNSET,
}

# Where systemd's user manager reads environment variables from, relative to
# the home directory. Everything in that directory is read at login and the
# files are merged in name order, which is why ours is numbered.
ENVIRONMENT_D = os.path.join(".config", "environment.d")
KEYBOARD_FILE = "10-keyboard.conf"

# Which file each setting is written into. One entry today; a second utility
# page adds a second line rather than a second copy of read() and write().
FILES = {
    LAYOUT: KEYBOARD_FILE,
}

# The line above each line of this project. It is also the one method to tell
# a line of this project from a line of another program in the same file. To
# remove a setting, remove its mark also, and change no other line. The
# uninstaller uses the same method on the PATH line that it added to a shell
# profile.
MARK = "# written by the SteamOS LED panel"


class SettingError(ValueError):
    """A value that must not be written. Named for the panel to catch."""


# -- what the machine can offer --------------------------------------------

# The files where X keeps the list of layouts that it knows, best first. This
# module reads them and does not hold its own list. The descriptions are then
# the words of the system, in the spelling of the system. A layout that this
# file does not know also gets a name.
XKB_RULES = ("/usr/share/X11/xkb/rules/evdev.lst",
             "/usr/share/X11/xkb/rules/base.lst")

# Which of them to actually put in the menu, and why it is not all of them.
#
# The rules file lists ninety-nine layouts, and the drop-down of the panel
# does not scroll. It takes the size of its entries, and the screen then
# limits it. A measurement on a 1280x800 display, the display of a Steam
# Machine, gave 926 pixels for twenty-eight entries. The last four entries
# were below the edge of the screen, and a click could not reach them. A menu
# that cannot show its last entry is worse than a short menu.
#
# So the list holds nineteen layouts. That is approximately two thirds of that
# screen, and it leaves space for a desktop with a larger font. The list
# holds more European layouts. That is a guess about the users who install an
# LED strip on a Steam Machine, and not a fact.
#
# The guess is safe because the list is not a limit. This module keeps a
# layout that a user wrote into the file manually. The panel adds it to the
# menu as its own entry. A colour that the palette does not hold gets the same
# treatment. See Panel._label_for.
COMMON = ("de", "at", "ch", "us", "gb", "fr", "es", "it", "nl", "be",
          "se", "no", "dk", "fi", "pl", "cz", "hu", "pt", "tr")

# Names for the most important layouts, for a machine where this module
# cannot read the rules file. A machine with no X packages is such a machine,
# and a Wayland-only session can have no X packages. This list holds only the
# layouts that get no name from another source. Each other layout uses its
# code, which is also a correct setting.
SHIPPED_NAMES = {
    "de": "German",
    "at": "German (Austria)",
    "ch": "German (Switzerland)",
    "us": "English (US)",
    "gb": "English (UK)",
    "fr": "French",
    "es": "Spanish",
    "it": "Italian",
}

# What the "leave it alone" entry is called. First in the menu because it is
# the default and because it is what undoes everything below it.
UNSET_LABEL = "Leave it to the system"

# A layout name in the form that libxkbcommon accepts: one code, or more than
# one code with a comma between them for a keyboard that changes between
# them. This module examines the value, because it writes the value into a
# file that each program in the session reads at login. A value with a new
# line or a quotation mark in it is not a layout. It is a method to add
# another line to that file.
_LAYOUT_RE = re.compile(r"^[a-z0-9_]+(,[a-z0-9_]+)*$")


def layout_names(paths=XKB_RULES):
    """{code: description} for every layout the machine knows.

    Empty when there is no rules file to read, which is not an error: the
    codes alone still make a working menu, they just read less well.
    """
    for path in paths:
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                found = _parse_rules(handle)
        except OSError:
            continue
        if found:
            return found
    return {}


def _parse_rules(lines):
    """Returns the `! layout` section of an xkb rules list.

        The file holds sections, and a `! name` line starts each section. Each
        entry is a code and then a description. The other sections hold models,
        variants and options. This function does not read them, and the first of
        them stops the loop.
        """
    found, inside = {}, False
    for line in lines:
        if line.startswith("!"):
            inside = line[1:].strip() == "layout"
            continue
        if not inside or not line.strip():
            continue
        code, _, description = line.strip().partition(" ")
        if code:
            found[code] = description.strip() or code
    return found


def layouts(names=None, extra=()):
    """Menu entries for the keyboard layout, as (label, value).

    `extra` is anything already in the file that COMMON does not list, so a
    layout somebody chose by hand keeps its place in the menu rather than
    being quietly replaced the first time the panel saves.
    """
    if names is None:
        names = layout_names()
    offered = [(UNSET_LABEL, UNSET)]
    seen = set()
    for code in tuple(COMMON) + tuple(extra):
        if code in seen or code == UNSET:
            continue
        seen.add(code)
        described = names.get(code) or SHIPPED_NAMES.get(code)
        offered.append(("%s (%s)" % (described, code) if described else code,
                        code))
    return tuple(offered)


# -- reading and writing ---------------------------------------------------

def directory(home=None):
    """Returns the environment.d directory of the user of the panel.

        `home` is a parameter, and this function does not read the environment
        directly. A test can then give a directory that it made itself. The
        sensor readers and the process readers of the service have the same shape.
        """
    return os.path.join(home or os.path.expanduser("~"), ENVIRONMENT_D)


def path_for(key, home=None):
    """The file one setting lives in."""
    return os.path.join(directory(home), FILES[key])


def read(home=None):
    """Every setting here, as the files on disk have them.

    Missing files and unreadable ones both come back as the defaults. There is
    nothing to repair in a file that is not there: not having set a keyboard
    layout is the ordinary state of a machine, not a fault.
    """
    values = dict(DEFAULTS)
    for key, name in FILES.items():
        try:
            with open(os.path.join(directory(home), name),
                      encoding="utf-8", errors="replace") as handle:
                found = _parse_environment(handle).get(key)
        except OSError:
            continue
        if found is not None:
            values[key] = found
    return values


def _parse_environment(lines):
    """Returns a systemd environment.d file as {name: value}.

        This is not a shell file. It holds NAME=VALUE lines, `#` starts a comment,
        and a value can have quotation marks. The last line for a name wins. The
        generator of systemd does the same with a file that gives a name twice.
        """
    found = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, sign, value = line.partition("=")
        if not sign:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        found[name.strip()] = value
    return found


def validate(values):
    """Refuse anything that must not reach a file read at every login."""
    layout = values.get(LAYOUT, UNSET)
    if layout == UNSET:
        return
    if not _LAYOUT_RE.match(layout):
        raise SettingError(
            "%s must be a layout code such as 'de', or several separated by "
            "commas such as 'de,us' - not %r" % (LAYOUT, layout))


def write(values, home=None):
    """Put these settings into the user's files. Returns what changed.

    This function writes only the lines of this project. A user puts other
        variables into a file in environment.d. This file has a very low number
        for that reason: more than one program can use it. A rewrite of the
        complete file removes the setting of another program.

    Setting one back to "leave it to the system" removes its line rather than
    writing an empty one: an empty XKB_DEFAULT_LAYOUT is a layout that is
    empty, which is not the same as never having said anything.
    """
    validate(values)
    changed = []
    for key, name in FILES.items():
        wanted = values.get(key, DEFAULTS[key])
        if _write_one(os.path.join(directory(home), name), key, wanted):
            changed.append(key)
    return changed


def _write_one(path, key, value):
    """One setting into one file, leaving every other line of it alone."""
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            lines = handle.read().splitlines()
    except OSError:
        lines = []

    kept, replaced = [], False
    for line in lines:
        stripped = line.strip()
        if stripped == MARK:
            continue                    # ours; put back only if still needed
        if stripped.startswith(key) and _parse_environment([line]).get(key) \
                is not None:
            if value == UNSET or replaced:
                continue                # dropped, or a duplicate further down
            kept.extend((MARK, "%s=%s" % (key, value)))
            replaced = True
            continue
        kept.append(line)
    if value != UNSET and not replaced:
        if kept and kept[-1].strip():
            kept.append("")
        kept.extend((MARK, "%s=%s" % (key, value)))

    if [line for line in kept if line.strip()]:
        _replace(path, "\n".join(kept).strip("\n") + "\n")
        return True
    # Nothing left worth keeping. Take the file away rather than leaving an
    # empty one behind: the point of choosing "leave it to the system" is that
    # afterwards there is nothing here saying otherwise.
    try:
        os.unlink(path)
    except FileNotFoundError:
        return False
    except OSError:
        return False
    return True


def _replace(path, text):
    """Write it in one step, so a login never reads half a file.

    environment.d is read by the user manager at login, and a file being
    rewritten in place is a file that can be read empty. Written beside it and
    moved into place instead, which on one filesystem is atomic.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=os.path.dirname(path),
        prefix=".%s." % os.path.basename(path), delete=False)
    try:
        with handle:
            handle.write(text)
        os.chmod(handle.name, 0o644)
        os.replace(handle.name, path)
    except OSError:
        try:
            os.unlink(handle.name)
        except OSError:                                  # pragma: no cover
            pass
        raise
