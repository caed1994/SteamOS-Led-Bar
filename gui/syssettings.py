# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""Settings the panel manages that are not the LED service's.

The rest of this window edits one file, `/etc/steamos-led-serial.conf`, and
every row on every page is a key in it. These are not: they are the machine's
own settings, they live in the user's home, and no service of ours reads them.

Kept apart from `steamos_led.config` on purpose, rather than added to it. That
file is the service's, it is validated by the service, and the service is
restarted when it changes - a keyboard layout in it would be a setting the
service would refuse to start over and a restart nobody needed.

What makes these the easy half of a utility panel is that none of them need
root. The panel runs as you, and so does everything here: no pkexec, no
polkit, no helper script that has to be short enough to read. Anything that
does need root belongs on the other side of `scripts/`, the way applying the
service's config already does.

No tkinter in here, the same way ledpanel.py and preview.py avoid it - what
the settings *are* should be testable on a machine with no display.
"""

from __future__ import annotations

import os
import re
import tempfile

# -- the keyboard layout ---------------------------------------------------
#
# Game Mode has no keyboard settings. gamescope builds its keymap through
# libxkbcommon, which falls back to XKB_DEFAULT_LAYOUT when nothing else has
# said otherwise - so setting that variable for the session is how a German
# keyboard becomes German in Game Mode, the on-screen keyboard included.
#
# Desktop Mode is a different matter and this does not govern it: Plasma sets
# its own layout through kxkbrc and wins there. Said on the page, because a
# setting that visibly does nothing on the desktop you are looking at is
# otherwise a setting that looks broken.

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

# The line above ours, and the whole of how a line of ours is told from a line
# somebody else put in the same file. Removing a setting removes its mark too,
# and nothing else in the file is touched - the same discipline the uninstaller
# uses on the PATH line it once added to a shell profile.
MARK = "# written by the SteamOS LED panel"


class SettingError(ValueError):
    """A value that must not be written. Named for the panel to catch."""


# -- what the machine can offer --------------------------------------------

# Where X keeps the list of layouts it knows, best first. Read rather than
# listed so the descriptions are the system's own words, in the system's own
# spelling - and so a layout this file has never heard of still gets a name.
XKB_RULES = ("/usr/share/X11/xkb/rules/evdev.lst",
             "/usr/share/X11/xkb/rules/base.lst")

# Which of them to actually put in the menu, and why it is not all of them.
#
# The rules file lists ninety-nine, and the panel's drop-down does not scroll:
# it sizes itself to its entries and is then clamped to the screen. Measured on
# a 1280x800 display - a Steam Machine's own - twenty-eight entries came to 926
# pixels, so the last four were drawn past the bottom edge and could not be
# clicked at all. A menu that cannot reach its own last entry is worse than a
# short one.
#
# So: nineteen, which measures about two thirds of that screen and leaves room
# for a desktop whose font is larger than this one's. They are Europe-weighted,
# which is a guess about who fits an LED strip to a Steam Machine rather than a
# fact - and the reason the guess is survivable is that it is not a limit.
# A layout written into the file by hand is not overwritten and not dropped:
# the panel adds it to the menu as an entry of its own, the same way a colour
# the palette does not have becomes one. See Panel._label_for.
COMMON = ("de", "at", "ch", "us", "gb", "fr", "es", "it", "nl", "be",
          "se", "no", "dk", "fi", "pl", "cz", "hu", "pt", "tr")

# Names for the few worth having if the rules file cannot be read at all -
# a machine with no X packages installed, which a Wayland-only session can be.
# Only the ones a name would otherwise be missing for; the rest fall back to
# their code, which is still a working setting.
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

# A layout name as libxkbcommon will take it: one code, or several separated
# by commas for a keyboard that switches between them. Checked because this
# is written into a file read at login by everything in the session - a value
# with a newline or a quote in it is not a layout, it is a way to put another
# line in that file.
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
    """The `! layout` section of an xkb rules list.

    The file is sections headed by `! name`, each entry a code and then its
    description. Anything else in it - models, variants, options - is not
    what this asks for and stops the walk.
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
    """The environment.d directory of whoever is running the panel.

    `home` is a parameter rather than read straight from the environment so a
    test can hand over a directory it built itself - the same shape the
    service's sensor and process readers take.
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
    """A systemd environment.d file as {name: value}.

    Not a shell: these are NAME=VALUE lines, `#` starts a comment, and a value
    may be quoted. The last mention of a name wins, which is what systemd's
    own generator does with a file that says something twice.
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

    Only the lines that are ours. A file in environment.d is a place people
    put other variables, and this one is numbered so low precisely so that it
    can be shared - rewriting it wholesale would take somebody else's setting
    away to change ours.

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
