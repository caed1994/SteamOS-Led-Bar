# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""What the bar shows while Steam does not control it.

Steam writes the LED state in Game Mode only, so on the desktop the bar keeps
what the last Game Mode session left. This module gives that decision to the
person at the machine instead. The bar is Steam's again when Game Mode
returns.

A scene is a *snapshot*, built as the shim builds one and drawn by the
renderer that draws Steam's. "Breath in this colour" is thus the same
arithmetic for both callers.

The difficult part is the mode of the machine. The service is a system
service with no session of its own, so it asks the process table instead.
gamescope is the compositor of Game Mode. `steamos-utility-center --desktop`
reports what it found.
"""

from __future__ import annotations

import logging
import os
import subprocess

from . import notify
from . import render
from . import shim

LOG = logging.getLogger(__name__)

# Leave the bar to Steam. That is what this service did before this module
# existed, and what it still does until a person asks for a scene.
SCENE_STEAM = "steam"

# The other values are the shim's own effects, under names that describe the
# appearance. This module uses "color" and not "manual": manual is the name in
# the *protocol*, and what a person selects is one colour on the full strip.
SCENE_OFF = "off"
SCENE_COLOR = "color"
SCENE_BREATH = "breath"
SCENE_PATROL = "patrol"
SCENE_RAINBOW = "rainbow"

# And this project's own four effects.
#
# In Game Mode they share the rainbow slot, because the menu entries are in
# Steam's client and a new effect must replace one. The desktop does not use
# that menu, so each of the four is a scene of its own here.
SCENE_FIRE = "fire"
SCENE_AURORA = "aurora"
SCENE_TEMPERATURE = "temperature"
SCENE_LOAD = "load"

# The effect that each scene draws with. The renderer takes a snapshot, so this
# table is the full translation. A scene that is not in this table is not a
# scene, and that is what the validator reports.
#
# The last five scenes share an effect, because they share the slot that the
# renderer replaces. SCENE_SHOWS below separates those five. It goes to the
# renderer with the snapshot.
SCENE_EFFECTS = {
    SCENE_OFF: shim.EFFECT_OFF,
    SCENE_COLOR: shim.EFFECT_MANUAL,
    SCENE_BREATH: shim.EFFECT_BREATH,
    SCENE_PATROL: shim.EFFECT_PATROL,
    SCENE_RAINBOW: shim.EFFECT_RAINBOW,
    SCENE_FIRE: shim.EFFECT_RAINBOW,
    SCENE_AURORA: shim.EFFECT_RAINBOW,
    SCENE_TEMPERATURE: shim.EFFECT_RAINBOW,
    SCENE_LOAD: shim.EFFECT_RAINBOW,
}

# What each of those five puts in the slot. A scene that names one says so
# directly, and that is the difference from Game Mode. In Game Mode, a setting
# elsewhere decides the content of the slot. Here it is the selected scene.
SCENE_SHOWS = {
    SCENE_RAINBOW: render.SHOWS_RAINBOW,
    SCENE_FIRE: render.SHOWS_FIRE,
    SCENE_AURORA: render.SHOWS_AURORA,
    SCENE_TEMPERATURE: render.SHOWS_TEMPERATURE,
    SCENE_LOAD: render.SHOWS_LOAD,
}

SCENES = (SCENE_STEAM,) + tuple(SCENE_EFFECTS)

# The scenes whose colour is the colour that a person sets. This list does not
# have the four that make their own colours, and it does not have "off", which
# has no colour. To offer the colour control for those is to offer a setting
# that does nothing.
SCENES_WITH_COLOUR = (SCENE_COLOR, SCENE_BREATH, SCENE_PATROL)


def _scenes_taking(what):
    """Returns the slot scenes that use one of the two controls of the bar.

    From render.rainbow_takes and not a list here, so this answer and the one
    in Game Mode cannot become different.
    """
    return tuple(scene for scene, shows in SCENE_SHOWS.items()
                 if what in render.rainbow_takes(shows))


# The scenes that light the strip, because that is what the brightness
# controls. The four effects are here: they have a brightness, although they
# select their own colours. The load gauge is not here: its brightness is its
# reading.
SCENES_LIT = SCENES_WITH_COLOUR + _scenes_taking(render.TAKES_BRIGHTNESS)

# And the scenes that move, because that is what the speed controls. A speed
# for one static colour has no meaning.
SCENES_THAT_MOVE = ((SCENE_BREATH, SCENE_PATROL)
                    + _scenes_taking(render.TAKES_SPEED))

# How often to ask whether Game Mode runs. The question is cheap: one directory
# listing and one short read for each process. It is not free, and nothing here
# changes faster than a person changes sessions.
CHECK_SECONDS = 2.0

# The time after the last write by Steam in which the bar is still Steam's.
#
# Protection for the change of mode: at the exit from Game Mode the compositor
# and the LED write stop in the same moment, and which one this module sees
# first is not a value to depend on.
#
# Short, because a person waits it out at each handover. at_rest ends it early
# rather than shortens it. See steam_has_it.
GRACE_SECONDS = 2.0

# And the time while Steam shows something that is not its rest state. On the
# desktop, that is the progress bar of a download.
#
# Much longer, because Steam writes that bar one time for each step it can
# show. Measured on a 100 GB download over a fast line: approximately seven
# seconds between two writes, and longer on a slow line. With the grace time
# above, the desktop effect appeared in each of those intervals.
#
# A long time costs nothing, because a download ends at the write in which
# Steam puts its rest state back and steam_has_it recognises that write. This
# is the limit for a machine with no such state to recognise.
BUSY_SECONDS = 120.0

# And the time after a boot with no Game Mode session and no write from Steam,
# before the scene takes the bar.
#
# The service starts before the session that decides the mode, so a machine
# that boots into Game Mode looks like a desktop until gamescope starts. It
# must thus be longer than a boot into Game Mode and no longer.
#
# It measures the uptime of the machine and not the age of this process, so
# Apply restarting the service does not hold the scene off again.
BOOT_SETTLE = 45.0

# The file with that value. Its first field is the seconds from the boot.
UPTIME = "/proc/uptime"

# What a Game Mode session looks like from outside it. gamescope is its
# compositor and the one process that runs for the full session.
#
# The match is on the start of the name: SteamOS releases name the wrapper
# "gamescope-session" and "gamescope-session-plus", and a list of each name
# becomes old on somebody's machine.
GAME_MODE_PROCESSES = ("gamescope",)

# Where to look for them. It is a parameter and not a constant in the
# function, so that a test can give a directory that it built.
PROC = "/proc"


def delay_for(speed):
    """Returns the `delay` field of the module for a speed multiplier.

    delay is a position on a control and not a duration. The cycle scales
    linearly with it from DELAY_DEFAULT, so a multiplier is its inverse.

    Steam's speed control sets the same field, so "twice as fast" means the
    same thing in a game and on the desktop.
    """
    if speed <= 0:
        return render.DELAY_DEFAULT
    steps = int(round(render.DELAY_DEFAULT / speed))
    return max(0, min(steps, render.DELAY_MAX))


def scene_snapshot(scene, color, brightness, speed=1.0):
    """Returns one scene as a snapshot, or None for "leave it to Steam".

    This function sets each field that the renderer reads. That includes the
    fields that Steam sets for an effect that it animates itself.
    """
    if scene == SCENE_STEAM:
        return None
    if scene not in SCENE_EFFECTS:
        raise ValueError("unknown scene %r" % scene)
    red, green, blue = notify.parse_color(color)
    return shim.make_snapshot(
        effect=SCENE_EFFECTS[scene],
        color=(red, green, blue),
        brightness=max(0, min(int(brightness), 255)),
        delay=delay_for(speed))


def scene_shows(scene):
    """Returns what this scene puts in the renderer's slot, or None.

    None for a scene the renderer does not draw from that slot. It replaces
    the content of a rainbow snapshot only.

    The value goes to the renderer for each frame and is not built into it.
    One renderer serves both modes, and the two disagree about this slot.
    """
    return SCENE_SHOWS.get(scene)


def describe(scene, color, brightness, speed):
    """Returns one scene in one line, with each setting that has an effect.

    Those settings only. A report that omits one reads as a setting with no
    effect, which is how the brightness under a rainbow appeared. A load gauge
    uses neither the brightness nor the speed. See render.rainbow_takes.

    The names are the settings and not the protocol: "color" is what a person
    selects, and "manual" is the shim's name for what it becomes.
    """
    if scene == SCENE_STEAM:
        return scene
    doing = []
    if scene in SCENES_WITH_COLOUR:
        doing.append("colour %s" % color)
    if scene in SCENES_LIT:
        doing.append("brightness %d" % brightness)
    if scene in SCENES_THAT_MOVE:
        doing.append("speed %g (delay %d)" % (speed, delay_for(speed)))
    return scene + (", " + ", ".join(doing) if doing else "")


def running_game_mode(root=PROC):
    """Returns the name of a Game Mode process that runs, or "".

    The name and not a boolean: it separates "no Game Mode session" from "a
    session under a name this module did not expect".
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


def machine_uptime(path=UPTIME):
    """Returns the seconds from the boot, or None if it cannot read them.

    None and not zero. Zero is a machine that started now, and a guess of that
    holds the scene off on each machine with no /proc/uptime.
    """
    try:
        with open(path) as handle:
            return float(handle.read().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def resting_key(snapshot):
    """Returns what makes two snapshots the same rest state of Steam.

    It uses each field that the shim reports except the brightness. Steam makes
    its own effect dim at the start of a download and bright again at the end.
    See Ownership.steam_has_it. A dim rainbow is the same rainbow.
    """
    return None if snapshot is None else snapshot.key(brightness=False)


def steam_wrote_ago(snapshot, now):
    """Returns the seconds from the last write by Steam, or None.

    The shim marks each write with ktime_get_ns(), which time.monotonic()
    reads, so the subtraction needs no conversion.

    The counter says whether a write occurred at all. The module marks the
    time at its load, and without the counter that time reads as a write now.
    """
    if snapshot is None or snapshot.seq <= shim.UNTOUCHED_SEQ:
        return None
    return now - snapshot.monotonic_ns / 1e9


# The words in both of the lines below, and what finds them again. It is one
# string in both places. A report that looked for words that the log no longer
# writes answers "nothing here" always, and that reads as a machine whose Game
# Mode this service never recognised.
GAME_MODE_MARK = "Game Mode"
IN_GAME_MODE = GAME_MODE_MARK + " is running (%s) - the bar is Steam's"
ON_THE_DESKTOP = "no " + GAME_MODE_MARK + " session - the bar is the desktop's"

JOURNAL_UNIT = "steamos-utility-center.service"
JOURNAL_SINCE = "-2 days"
# Sufficient for the last changes, and not for a full day of boots.
JOURNAL_LINES = 6


def journal_command(unit=JOURNAL_UNIT, since=JOURNAL_SINCE):
    return ["journalctl", "-u", unit, "--since", since, "--no-pager",
            "-o", "short-iso"]


def read_journal(text):
    """Returns the ownership lines from a journal dump, the newest last."""
    return [line.strip() for line in (text or "").splitlines()
            if GAME_MODE_MARK in line][-JOURNAL_LINES:]


def journal_ownership(command=None):
    """Returns what the service recorded about the mode: (lines, why not).

    Game Mode has no terminal, so the journal is the only way to see that half
    and this reads it rather than leave it to a command a person types.

    A message in place of the lines means "try again with sudo". Empty lines
    mean "this machine never recognised Game Mode".
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
    """The owner of the bar now. The loop can ask at each frame.

    The bar is Steam's while Game Mode runs and for some seconds after its
    last write. It reads the process table one time in each CHECK_SECONDS,
    because the loop asks sixty times each second.
    """

    def __init__(self, grace=GRACE_SECONDS, interval=CHECK_SECONDS, look=None,
                 busy=BUSY_SECONDS, boot_settle=BOOT_SETTLE, uptime=None):
        self.grace = grace
        self.busy = busy
        self.interval = interval
        self.boot_settle = boot_settle
        # The caller gives this function and does not call it here. A test can
        # thus drive this class with no process table. The clock for the boot
        # is a parameter for the same reason.
        self.look = running_game_mode if look is None else look
        self.uptime = machine_uptime if uptime is None else uptime
        self.found = ""
        self._asked = None
        self._booted_at = None
        # What the shim held while the scene had the bar. It is the rest state
        # of Steam. See steam_has_it.
        self.at_rest = None
        # The last write of Steam, as (seq, key, resting key). Two writes
        # beside each other are what tells a fade from a download. See
        # _learn_from_a_fade.
        self._last_write = None

    def game_mode(self, now):
        """Returns the Game Mode process that runs now, or "". From the cache."""
        first = self._asked is None
        if first or now - self._asked >= self.interval:
            found = self.look()
            if first or found != self.found:
                # One line at the start and one at each change. Game Mode has
                # no terminal, so the journal is the only way to see it. It
                # answers "why does my scene not appear" and "why does the bar
                # ignore Steam", which look the same at the bar.
                LOG.info(IN_GAME_MODE % found if found else ON_THE_DESKTOP)
            self.found = found
            self._asked = now
        return self.found

    def still_booting(self, now):
        """Returns whether the machine is too new to know its own mode.

        It reads the uptime one time and keeps the moment: the answer changes
        in one direction only. A machine that reports no uptime counts as old.
        """
        if self._booted_at is None:
            up = self.uptime()
            self._booted_at = now - (self.boot_settle if up is None else up)
        return now - self._booted_at < self.boot_settle

    def _learn_from_a_fade(self, snapshot):
        """Learns the rest state of Steam from a fade, while none is known.

        A Game Mode session forgets that state, and the usual way to learn it
        again is the silence after Steam stops writing. A download gives no
        such silence, so a handover at the end of one cost the full grace time.

        Steam fades its own effect at each end of a download, one step in each
        thirty milliseconds. Two writes that differ in the brightness and in
        nothing else are thus a fade, and the effect under them is the rest
        state.

        The progress bar itself repeats one write unchanged, so "the same
        twice" is not the signature. A Steam that does not fade teaches this
        nothing, and the grace time ends the download as before.
        """
        if snapshot is None or snapshot.seq <= shim.UNTOUCHED_SEQ:
            return
        seen = (snapshot.seq, snapshot.key(), resting_key(snapshot))
        was, self._last_write = self._last_write, seen
        if self.at_rest is not None or was is None or seen[0] == was[0]:
            return
        if seen[2] == was[2] and seen[1] != was[1]:
            self.at_rest = seen[2]

    def steam_has_it(self, snapshot, now):
        """Returns whether to leave the bar alone because Steam drives it.

        True in Game Mode, and on the desktop while Steam continues to write.
        Each write of a download extends the time, so the bar stays Steam's
        while the download fills it.

        A write that puts the remembered rest state back ends that time
        instead of extending it. Steam that restores what was already there
        gives the bar up. Without this, the Game Mode effect appeared for the
        full grace time after every download.

        The comparison ignores the brightness. Steam fades its own effect at
        each end of a download, one step in each thirty milliseconds, and a
        full comparison read every step as Steam that takes the bar.

        The time to wait is the grace time while Steam rests and BUSY_SECONDS
        while it shows something of its own. Before both there is the boot.
        See still_booting, and _learn_from_a_fade for the other reading of the
        same fade.
        """
        if self.game_mode(now):
            # No value from the desktop stays valid after a Game Mode session.
            # The rest state of Steam after such a session is what that session
            # leaves, and the handover below finds that state itself.
            #
            # An old value reads as Steam that shows something of its own, and
            # it holds the bar for the full time below.
            self.at_rest = None
            # And the pair of writes that _learn_from_a_fade compares. A write
            # from before the session is not the neighbour of a write after
            # it.
            self._last_write = None
            return True
        self._learn_from_a_fade(snapshot)
        ago = steam_wrote_ago(snapshot, now)
        if ago is None and self.still_booting(now):
            # Neither mode gives an answer: there is no Game Mode session, and
            # nothing wrote to the LEDs. On a machine this new, that is the
            # boot and not an answer. The start-up breath thus belongs on the
            # bar until one of the two appears.
            return True
        resting = resting_key(snapshot)
        if ago is not None:
            busy = self.at_rest is not None and resting != self.at_rest
            if ago < (self.busy if busy else self.grace):
                return busy or self.at_rest is None
        # The scene has the bar. What the shim holds while that is true is
        # what Steam puts back at the end of a download.
        self.at_rest = resting
        return False
