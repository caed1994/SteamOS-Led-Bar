# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""What the bar shows while Steam is not looking.

Steam writes the LED state in Game Mode and nowhere else, so on the desktop
the shim keeps handing out whatever the last Game Mode session left behind -
or, on a machine that has not been in one since it booted, nothing at all.
Either way the bar is stuck showing a decision somebody made somewhere else.

So this lets you make that decision: pick an effect, a colour and a
brightness for Desktop Mode, and the bar shows them until Game Mode comes
back. Then it is Steam's again, unchanged - the whole point is that the two
do not have to agree.

Nothing new is drawn here. A scene is a *snapshot*, built the way the shim
would have built one, and handed to the same renderer that draws Steam's:
"breath in this colour" is the same eight lines of maths whoever asked for
it, and an effect that looked one way in a game and another on the desktop
would be a bug nobody could explain. That is also why the scenes are exactly
the effects the bar already has - see shim.EFFECT_*.

The one genuinely hard part is knowing which mode the machine is in. The
service is a *system* service: it has no session of its own to ask, and the
one you are logged into is not readable from here - so the question is put
to the process table instead, and answered by gamescope, the compositor Game
Mode runs under. `steamos-led-serial --desktop` reports what it found, on
the machine where it matters.
"""

from __future__ import annotations

import logging
import os
import subprocess

from . import notify
from . import shim

LOG = logging.getLogger(__name__)

# Leave the bar to Steam, which is what it did before this existed and what
# it still does unless somebody asks for something else.
SCENE_STEAM = "steam"

# The rest are the shim's own effects under names that say what they look
# like. "color" rather than "manual" because manual is what the *protocol*
# calls it; what you are picking is one colour, all the way along.
SCENE_OFF = "off"
SCENE_COLOR = "color"
SCENE_BREATH = "breath"
SCENE_PATROL = "patrol"
SCENE_RAINBOW = "rainbow"

# Which effect each scene draws with. The renderer is handed a snapshot, so
# this table is the whole of the translation - and a scene that is not in it
# is not a scene, which is what the validator says.
SCENE_EFFECTS = {
    SCENE_OFF: shim.EFFECT_OFF,
    SCENE_COLOR: shim.EFFECT_MANUAL,
    SCENE_BREATH: shim.EFFECT_BREATH,
    SCENE_PATROL: shim.EFFECT_PATROL,
    SCENE_RAINBOW: shim.EFFECT_RAINBOW,
}

SCENES = (SCENE_STEAM,) + tuple(SCENE_EFFECTS)

# Scenes whose colour is the colour you set. The rainbow makes its own and
# "off" has none, so offering the picker against those two would be offering
# a setting that does nothing.
SCENES_WITH_COLOUR = (SCENE_COLOR, SCENE_BREATH, SCENE_PATROL)

# How often to ask whether Game Mode is running. Cheap - one directory
# listing and a short read per process - but not free, and nothing here
# changes faster than somebody can switch sessions.
CHECK_SECONDS = 2.0

# How long after Steam's last write to go on treating the bar as Steam's.
#
# Belt and braces for the switch itself. Leaving Game Mode, the compositor
# and the LED write both stop within a moment of each other, and which of the
# two this notices first is not worth depending on - so the scene waits out
# a few seconds either way rather than flickering between the two owners at
# the handover. It is not a substitute for finding gamescope: in a Game Mode
# session where that failed, this would only hold the bar until the grace ran
# out. That is what --desktop is for.
GRACE_SECONDS = 8.0

# What a Game Mode session looks like from outside it.
#
# gamescope is the compositor Steam's Game Mode runs under, and the one
# process that is there for the whole of it. Matched on the front of the name
# rather than in full: the session is started through a wrapper that has been
# spelled "gamescope-session" and "gamescope-session-plus" in different
# SteamOS releases, and a list that had to name every spelling would be a
# list that goes stale on somebody's machine.
GAME_MODE_PROCESSES = ("gamescope",)

# Where to look for them. A parameter rather than a constant in the function
# so a test can hand over a directory it built itself.
PROC = "/proc"


def scene_snapshot(scene, color, brightness):
    """One scene as a snapshot, or None for "leave it to Steam".

    Everything the renderer reads is set here, including the fields Steam
    would have set for an effect it was animating itself: the delay is the
    module's own default, so the desktop's breath runs at the same rate as a
    game's, and SPEED scales both.
    """
    if scene == SCENE_STEAM:
        return None
    if scene not in SCENE_EFFECTS:
        raise ValueError("unknown scene %r" % scene)
    red, green, blue = notify.parse_color(color)
    return shim.make_snapshot(
        effect=SCENE_EFFECTS[scene],
        color=(red, green, blue),
        brightness=max(0, min(int(brightness), 255)))


def running_game_mode(root=PROC):
    """The name of a Game Mode process that is running, or "" if none is.

    The name rather than a yes: it is the difference between "this machine
    has no Game Mode session" and "it has one and it is called something this
    did not expect", and only the second of those is ours to fix.
    """
    try:
        entries = os.listdir(root)
    except OSError:                                     # pragma: no cover
        return ""
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            with open(os.path.join(root, entry, "comm")) as handle:
                name = handle.read().strip()
        except OSError:
            continue            # it exited between the listing and the read
        if any(name.startswith(front) for front in GAME_MODE_PROCESSES):
            return name
    return ""


def steam_wrote_ago(snapshot, now):
    """Seconds since Steam last set the LEDs, or None if it never has.

    The shim stamps every write with ktime_get_ns(), which is the same clock
    time.monotonic() reads - so the two subtract without conversion. The
    counter is what says whether there was a write at all: the module starts
    it at UNTOUCHED_SEQ and stamps the time at load, so an untouched device
    carries a timestamp that means "when the module loaded" and would
    otherwise read as a write that just happened.
    """
    if snapshot is None or snapshot.seq <= shim.UNTOUCHED_SEQ:
        return None
    return now - snapshot.monotonic_ns / 1e9


# The phrase both of the lines below carry, and what finds them again. One
# string in both places: a report that searched for wording the log had
# stopped using would answer "nothing here" forever, which reads exactly like
# a machine whose Game Mode was never recognised.
GAME_MODE_MARK = "Game Mode"
IN_GAME_MODE = GAME_MODE_MARK + " is running (%s) - the bar is Steam's"
ON_THE_DESKTOP = "no " + GAME_MODE_MARK + " session - the bar is the desktop's"

JOURNAL_UNIT = "steamos-led-serial.service"
JOURNAL_SINCE = "-2 days"
# Enough to show the last few switches without pasting a day of boots.
JOURNAL_LINES = 6


def journal_command(unit=JOURNAL_UNIT, since=JOURNAL_SINCE):
    return ["journalctl", "-u", unit, "--since", since, "--no-pager",
            "-o", "short-iso"]


def read_journal(text):
    """The ownership lines out of a journal dump, most recent last."""
    return [line.strip() for line in (text or "").splitlines()
            if GAME_MODE_MARK in line][-JOURNAL_LINES:]


def journal_ownership(command=None):
    """What the service recorded about which mode it was in: (lines, why not).

    Read here rather than left as a command for somebody to type, because it
    is the only way to see the Game Mode half at all. There is no terminal in
    Game Mode, so what the service decided during a session can only be looked
    at afterwards - and an instruction to pipe journalctl through grep is a
    step at which most people stop.

    A complaint instead of lines when the journal could not be read, which is
    a different answer from "it has nothing to say": one means look again with
    sudo, the other means this machine's Game Mode was never recognised.
    """
    try:
        done = subprocess.run(command or journal_command(),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, timeout=15)
    except FileNotFoundError:
        return [], "there is no journalctl on this machine"
    except (OSError, subprocess.SubprocessError) as exc:
        return [], "journalctl did not answer (%s)" % exc
    if done.returncode != 0:
        said = (done.stderr or "").strip().splitlines()
        return [], ("journalctl refused: %s"
                    % (said[-1] if said else "no reason given"))
    return read_journal(done.stdout), ""


class Ownership:
    """Whose the bar is at this moment, asked as often as the loop likes.

    Steam's while Game Mode is running, and for a few seconds after the last
    thing Steam wrote; the desktop's otherwise. The process table is only
    read every CHECK_SECONDS - the answer cannot change faster than somebody
    can switch sessions, and the loop asks sixty times a second.
    """

    def __init__(self, grace=GRACE_SECONDS, interval=CHECK_SECONDS, look=None):
        self.grace = grace
        self.interval = interval
        # Given rather than called for, which is what lets a test drive this
        # without a process table of its own.
        self.look = running_game_mode if look is None else look
        self.found = ""
        self._asked = None

    def game_mode(self, now):
        """The Game Mode process running now, "" if none - from the cache."""
        first = self._asked is None
        if first or now - self._asked >= self.interval:
            found = self.look()
            if first or found != self.found:
                # The journal is the only way to see this happen, because the
                # only place you can watch it from is Game Mode and there is
                # no terminal there. So: once when the service starts, and
                # once on every change after it.
                #
                # It is the answer to both "why is my scene not showing" and
                # "why is the bar ignoring Steam", and those two look
                # identical from in front of the bar.
                LOG.info(IN_GAME_MODE % found if found else ON_THE_DESKTOP)
            self.found = found
            self._asked = now
        return self.found

    def steam_has_it(self, snapshot, now):
        """Whether to leave the bar alone because Steam is driving it."""
        if self.game_mode(now):
            return True
        ago = steam_wrote_ago(snapshot, now)
        return ago is not None and ago < self.grace
