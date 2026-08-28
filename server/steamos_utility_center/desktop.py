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
the effects the bar already has: Steam's own - see shim.EFFECT_* - and this
project's four, which the desktop offers one apiece because it is not picking
them out of Steam's menu and so is not held to its one free slot.

The one genuinely hard part is knowing which mode the machine is in. The
service is a *system* service: it has no session of its own to ask, and the
one you are logged into is not readable from here - so the question is put
to the process table instead, and answered by gamescope, the compositor Game
Mode runs under. `steamos-utility-center --desktop` reports what it found, on
the machine where it matters.
"""

from __future__ import annotations

import logging
import os
import subprocess

from . import notify
from . import render
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

# And this project's own four, which in Game Mode have to share one slot.
#
# That sharing is a limit of Steam's menu, not of the effects: the entries in
# it are built into the client, so a new one has to take over the rainbow, and
# RAINBOW_SHOWS is which of the four gets it. Nothing on the desktop is
# choosing from that menu, so nothing here has to share - each of the four is
# a scene of its own, and "rainbow" here means the rainbow.
SCENE_FIRE = "fire"
SCENE_AURORA = "aurora"
SCENE_TEMPERATURE = "temperature"
SCENE_LOAD = "load"

# Which effect each scene draws with. The renderer is handed a snapshot, so
# this table is the whole of the translation - and a scene that is not in it
# is not a scene, which is what the validator says.
#
# The last five share an effect because they share the slot the renderer
# substitutes into: what tells those five apart is SCENE_SHOWS below, handed
# to the renderer alongside the snapshot.
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

# What each of those five puts in the slot. A scene that names one is saying
# so outright, which is the whole difference from Game Mode: there the slot's
# tenant is a setting somewhere else, and here it is the scene you picked.
SCENE_SHOWS = {
    SCENE_RAINBOW: render.SHOWS_RAINBOW,
    SCENE_FIRE: render.SHOWS_FIRE,
    SCENE_AURORA: render.SHOWS_AURORA,
    SCENE_TEMPERATURE: render.SHOWS_TEMPERATURE,
    SCENE_LOAD: render.SHOWS_LOAD,
}

SCENES = (SCENE_STEAM,) + tuple(SCENE_EFFECTS)

# Scenes whose colour is the colour you set. The four that make their own and
# "off", which has none, are left out: offering the picker against those would
# be offering a setting that does nothing.
SCENES_WITH_COLOUR = (SCENE_COLOR, SCENE_BREATH, SCENE_PATROL)


def _scenes_taking(what):
    """The slot scenes that answer to one of the bar's two sliders.

    Read off render.rainbow_takes rather than listed, so the answer here and
    the answer in Game Mode cannot drift apart: the load gauge ignores both
    sliders wherever it is drawn, and a page that greyed them in one mode and
    not the other would be the page lying in one of them.
    """
    return tuple(scene for scene, shows in SCENE_SHOWS.items()
                 if what in render.rainbow_takes(shows))


# Scenes that light the strip at all, which is what brightness is about - the
# four included, since they have a brightness even though they pick their own
# colours. All but the load gauge, whose brightness is what it is saying.
SCENES_LIT = SCENES_WITH_COLOUR + _scenes_taking(render.TAKES_BRIGHTNESS)

# And the ones that move, which is what speed is about. One colour standing
# still has a speed the way a photograph has a frame rate.
SCENES_THAT_MOVE = ((SCENE_BREATH, SCENE_PATROL)
                    + _scenes_taking(render.TAKES_SPEED))

# How often to ask whether Game Mode is running. Cheap - one directory
# listing and a short read per process - but not free, and nothing here
# changes faster than somebody can switch sessions.
CHECK_SECONDS = 2.0

# How long after Steam's last write to go on treating the bar as Steam's.
#
# Belt and braces for the switch itself. Leaving Game Mode, the compositor
# and the LED write both stop within a moment of each other, and which of the
# two this notices first is not worth depending on - so the scene waits out
# a moment either way rather than flickering between the two owners at the
# handover. It is not a substitute for finding gamescope: in a Game Mode
# session where that failed, this would only hold the bar until the grace ran
# out. That is what --desktop is for.
#
# It also turned out to be what shows a download's progress bar on the
# desktop, which nothing here set out to do: Steam writes the bar as it fills
# and every write pushes the grace out again. Worth keeping, so this is now
# short enough that the tail after the last write is not something you sit
# and watch - and see at_rest for how the tail was got rid of rather than
# merely shortened.
GRACE_SECONDS = 2.0

# And how long while Steam is showing something that is not the state it rests
# at, which on the desktop means a download's progress bar.
#
# Far longer, because Steam writes that bar once per step it can show and no
# oftener. Measured on a 100 GB download over a fast line: about seven seconds
# between writes - and the gap grows in proportion on a slower one, there being
# the same number of steps whatever the speed. Two seconds of patience meant
# the desktop's own effect broke through in every one of those gaps, and the
# bar swapped back and forth for the whole download. Which is what was
# reported: two seconds of the download, five of the desktop, over and over.
#
# Long costs nothing here, because it is only a backstop. What really ends a
# download is Steam putting back the state it was resting at, and that is
# recognised exactly - see steam_has_it. This covers the machine that has no
# such state to recognise, and bounds how long one stray write can hold the
# bar.
BUSY_SECONDS = 120.0

# And how long a machine that has only just booted may go with neither a Game
# Mode session nor a write from Steam before the scene takes the bar.
#
# The service is a system service - After=multi-user.target - so it is up and
# talking to the strip well before the session that decides which mode this is.
# In between, a machine booting into Game Mode looks exactly like a desktop:
# gamescope has not started, Steam has written nothing. So the scene came up in
# the middle of the boot and the startup breath resumed when gamescope finally
# appeared, which is what was reported: the boot effect, then the desktop's,
# then the boot effect again, then Steam.
#
# It costs nothing on that boot, the startup breath being what it holds. What
# it costs is on a machine booting straight into Desktop Mode, where the scene
# now waits this out - so it wants to be long enough to outlast a boot into
# Game Mode and no longer.
#
# Measured against the machine's uptime rather than this process's, so it is
# the boot that is waited out and not a restart: Apply restarts the service on
# a machine that has been up for hours, and the scene should come up at once.
BOOT_SETTLE = 45.0

# Where that is read from. The first field is the seconds since boot.
UPTIME = "/proc/uptime"

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


def delay_for(speed):
    """The module's `delay` field for a speed multiplier.

    Steam's own speed slider sets that field, and a scene's sets it the same
    way - so "twice as fast" means the same thing in a game and on the
    desktop, and neither needs a special case in the renderer.

    delay is not a duration but a position on a slider: the cycle scales
    linearly with it from DELAY_DEFAULT, so a multiplier is simply that the
    other way up. SPEED on the Strip page then scales this the way it scales
    a game's, which is what keeps it one knob for "everything a bit slower".
    """
    if speed <= 0:
        return render.DELAY_DEFAULT
    steps = int(round(render.DELAY_DEFAULT / speed))
    return max(0, min(steps, render.DELAY_MAX))


def scene_snapshot(scene, color, brightness, speed=1.0):
    """One scene as a snapshot, or None for "leave it to Steam".

    Everything the renderer reads is set here, including the fields Steam
    would have set for an effect it was animating itself.
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
    """What this scene puts in the renderer's substitutable slot, or None.

    None for the scenes that are not drawn out of that slot at all, where the
    renderer's own setting is moot: it only ever substitutes into a rainbow
    snapshot, so there is nothing for an answer here to change.

    Handed to the renderer a frame at a time rather than built into one. There
    is a single renderer, and it is Steam's as much as the desktop's - the two
    modes disagreeing about what the rainbow slot holds is the point, and a
    renderer can only hold one answer.
    """
    return SCENE_SHOWS.get(scene)


def describe(scene, color, brightness, speed):
    """One scene in a line, naming every setting that is doing something.

    And only those. A report that left one out reads as a setting that is not
    in play - which is what the brightness looked like under a rainbow, the
    one lit scene that has no colour of yours, and "the slider does nothing"
    is what came back.

    Which is also why the four that draw themselves have a say. A load gauge
    answers to neither brightness nor speed: what it draws is a reading, and
    both would change what it says rather than how it looks - see
    render.rainbow_takes.

    Named as the settings name it rather than as the protocol does: "color" is
    what you picked and "manual" is what the shim calls the effect it becomes,
    and only one of the two is a word you can go and look up.
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


def machine_uptime(path=UPTIME):
    """Seconds since the machine booted, or None if that cannot be read.

    None rather than nought, because the two mean opposite things here: one is
    a machine that has just started and the other is a question this cannot
    answer, and guessing "just started" at it would hold the scene off on every
    machine without a /proc/uptime to read.
    """
    try:
        with open(path) as handle:
            return float(handle.read().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def resting_key(snapshot):
    """What makes two snapshots the same thing Steam is resting at.

    Everything the shim reports except the brightness, because Steam dims its
    own effect on the way into a download and brings it back up on the way
    out - see Ownership.steam_has_it. Half a lit rainbow is the same rainbow.
    """
    return None if snapshot is None else snapshot.key(brightness=False)


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

JOURNAL_UNIT = "steamos-utility-center.service"
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

    def __init__(self, grace=GRACE_SECONDS, interval=CHECK_SECONDS, look=None,
                 busy=BUSY_SECONDS, boot_settle=BOOT_SETTLE, uptime=None):
        self.grace = grace
        self.busy = busy
        self.interval = interval
        self.boot_settle = boot_settle
        # Given rather than called for, which is what lets a test drive this
        # without a process table of its own - and the same for the clock the
        # boot is measured against.
        self.look = running_game_mode if look is None else look
        self.uptime = machine_uptime if uptime is None else uptime
        self.found = ""
        self._asked = None
        self._booted_at = None
        # What was on the shim while the scene had the bar - Steam's state at
        # rest. See steam_has_it.
        self.at_rest = None

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

    def still_booting(self, now):
        """Whether the machine is too young to have decided which mode it is.

        Read once and kept as a moment on the loop's own clock: the answer
        only ever goes one way, and this is asked sixty times a second.

        A machine that will not say how long it has been up is taken as up
        already, which is what this did before the question was asked at all.
        """
        if self._booted_at is None:
            up = self.uptime()
            self._booted_at = now - (self.boot_settle if up is None else up)
        return now - self._booted_at < self.boot_settle

    def steam_has_it(self, snapshot, now):
        """Whether to leave the bar alone because Steam is driving it.

        Game Mode outright, and on the desktop for as long as Steam keeps
        writing. That second half is what puts a download's progress bar on
        the bar: every write pushes the grace out again, so the bar stays
        Steam's for as long as the download is filling it.

        And then there is the write that ends it. When the download finishes,
        Steam puts back the effect that was set in Game Mode - so the last
        thing it writes is the state it was resting at before any of this
        started, and the grace would go on showing that for its full length.
        Reported, and it is a strange few seconds: a Game Mode effect nobody
        asked for, between the download and the desktop's own.

        So a write that restores what Steam left behind ends the grace
        instead of extending it. Steam putting back what was already there is
        Steam letting go, not Steam taking over - and it is exact rather than
        a guess about how often Steam writes, which is a thing this cannot
        know. If the restored state is not the one remembered, nothing is
        lost: the grace runs out the way it always did.

        "What was already there" ignores the brightness, and that is the
        whole of the second half of this. Measured on a Steam Machine: Steam
        brackets a download with a fade of its own effect, dimming the
        rainbow to nothing before the progress bar appears and bringing it
        back up afterwards, a step every thirty milliseconds. Every step of
        both fades differs from the state at rest in the brightness and in
        nothing else - so compared on the whole snapshot each one read as
        Steam taking the bar, and the Game Mode effect flashed at both ends
        of every download. It is the same effect, dimmed; it is not Steam
        showing something else.

        Which leaves how long to wait between Steam's writes, and there are
        two answers to that. While Steam is resting it is the short grace,
        which is for the handover. While it is showing something of its own it
        is BUSY_SECONDS, because a download's progress bar is written once per
        step it can show - seven seconds apart on a fast line, further on a
        slow one - and the bar is Steam's for the whole of that gap and not
        for the first two seconds of it. Waiting only the grace there had the
        two effects trading the bar back and forth for a whole download.

        And before either of those there is the boot, where the honest answer
        is neither: see still_booting.
        """
        if self.game_mode(now):
            # Nothing worked out on the desktop outlives a Game Mode session:
            # what Steam rests at afterwards is whatever this session leaves
            # behind, and the handover below works that out for itself. Kept,
            # a stale one would read as Steam showing something of its own and
            # hold the bar for the whole of the patience below.
            self.at_rest = None
            return True
        ago = steam_wrote_ago(snapshot, now)
        if ago is None and self.still_booting(now):
            # Neither mode has had its say: no Game Mode session, and nothing
            # ever written to the LEDs. On a machine this young that is the
            # boot itself and not an answer, and the startup breath is what
            # belongs on the bar until one of the two turns up.
            return True
        resting = resting_key(snapshot)
        if ago is not None:
            busy = self.at_rest is not None and resting != self.at_rest
            if ago < (self.busy if busy else self.grace):
                return busy or self.at_rest is None
        # The scene has the bar. Whatever is on the shim while that is true is
        # what Steam will put back when it has finished.
        self.at_rest = resting
        return False
