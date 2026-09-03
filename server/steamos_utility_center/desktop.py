# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""What the bar shows while Steam does not control it.

Steam writes the LED state in Game Mode only. On the desktop, the shim thus
returns what the last Game Mode session left. On a machine with no Game Mode
session after the boot, it returns nothing.

In both cases the bar shows a decision from another place.

This module gives that decision to the person at the machine. Select an
effect, a colour and a brightness for Desktop Mode. The bar shows them until
Game Mode returns. The bar is then Steam's again, with no change. The two
modes do not have to agree, and that is the purpose.

This module draws nothing new. A scene is a *snapshot*. It is built as the
shim builds one, and it goes to the renderer that draws Steam's snapshots.

"Breath in this colour" is thus the same arithmetic for each caller. An effect
that looked different in a game and on the desktop is a defect that nobody can
explain.

For the same reason, the scenes are the effects that the bar has: Steam's own
effects (see shim.EFFECT_*) and this project's four. The desktop gives each of
the four its own scene. It does not select from Steam's menu, so it has no
limit of one slot.

The difficult part is the mode of the machine. The service is a *system*
service. It has no session of its own, and it cannot read the session that a
person is logged in to.

It thus asks the process table. gamescope answers: it is the compositor of Game
Mode. `steamos-utility-center --desktop` reports what it found, on the machine
where the answer matters.
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

# And this project's own four effects. In Game Mode they share one slot.
#
# That limit belongs to Steam's menu and not to the effects. The entries of the
# menu are in the client, so a new effect must replace the rainbow.
# RAINBOW_SHOWS selects which of the four takes it.
#
# The desktop does not select from that menu, so nothing here shares a slot.
# Each of the four is a scene of its own, and "rainbow" here is the rainbow.
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

    This comes from render.rainbow_takes and is not a list here. The answer
    here and the answer in Game Mode thus cannot become different.

    The load gauge ignores both controls in each mode. A page that made them
    grey in one mode and not in the other is wrong in one of the two.
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
# This is protection for the change of mode. At the exit from Game Mode, the
# compositor and the LED write both stop in the same moment. Which of the two
# this module sees first is not a value to depend on.
#
# The scene thus waits a moment in each direction. It does not change between
# the two owners at the handover.
#
# It is not a replacement for the search for gamescope. In a Game Mode session
# where that search failed, this would hold the bar for this time only.
# --desktop is the report for that fault.
#
# It is also what shows the progress bar of a download on the desktop, and this
# module did not intend that. Steam writes the bar as it fills, and each write
# extends this time again.
#
# That behaviour is worth a keep, so this time is now short. The part after the
# last write is thus not a delay that a person watches. See at_rest for how
# that part was removed and not only made shorter.
GRACE_SECONDS = 2.0

# And the time while Steam shows something that is not its rest state. On the
# desktop, that is the progress bar of a download.
#
# This time is much longer, because Steam writes that bar one time for each
# step that it can show, and not more often.
#
# The measurement, on a 100 GB download over a fast line: approximately seven
# seconds between two writes. On a slower line the interval is longer, because
# the number of steps is the same at each speed.
#
# With two seconds of patience, the desktop effect appeared in each of those
# intervals. The bar thus changed between the two effects for the full
# download. A user reported that: two seconds of the download, five seconds of
# the desktop, and again.
#
# A long time costs nothing here, because this is only a limit. What ends a
# download is the write in which Steam puts its rest state back, and this
# module recognises that write exactly. See steam_has_it.
#
# This time covers a machine that has no such state to recognise. It also
# limits how long one write can hold the bar.
BUSY_SECONDS = 120.0

# And the time that a machine after a boot can have with no Game Mode session
# and no write from Steam, before the scene takes the bar.
#
# The service is a system service, with After=multi-user.target. It thus runs
# and drives the strip before the session that decides the mode.
#
# In that interval, a machine that boots into Game Mode looks the same as a
# desktop: gamescope did not start, and Steam wrote nothing.
#
# The scene thus appeared in the middle of the boot, and the start-up breath
# returned when gamescope started. A user reported that sequence: the boot
# effect, the desktop effect, the boot effect again, then Steam.
#
# This time costs nothing on that boot, because the start-up breath continues.
# It costs on a machine that boots into Desktop Mode, where the scene now waits
# for it. It must thus be longer than a boot into Game Mode and no longer.
#
# The measurement uses the uptime of the machine and not the age of this
# process. It thus waits for a boot and not for a restart. Apply restarts the
# service on a machine that has run for hours, and the scene must appear
# immediately there.
BOOT_SETTLE = 45.0

# The file with that value. Its first field is the seconds from the boot.
UPTIME = "/proc/uptime"

# What a Game Mode session looks like from outside it.
#
# gamescope is the compositor of Steam's Game Mode. It is the one process that
# runs for the full session.
#
# The match uses the start of the name and not the full name. A wrapper starts
# the session, and different SteamOS releases name that wrapper
# "gamescope-session" and "gamescope-session-plus". A list of each name becomes
# old on somebody's machine.
GAME_MODE_PROCESSES = ("gamescope",)

# Where to look for them. It is a parameter and not a constant in the
# function, so that a test can give a directory that it built.
PROC = "/proc"


def delay_for(speed):
    """Returns the `delay` field of the module for a speed multiplier.

    Steam's own speed control sets that field, and the control of a scene sets
    it in the same way. "Twice as fast" thus means the same thing in a game
    and on the desktop, and the renderer needs no special case.

    delay is not a duration. It is a position on a control. The cycle scales
    linearly with it from DELAY_DEFAULT, so a multiplier is the inverse of it.

    SPEED on the Strip page then scales this value as it scales the value of a
    game. It thus stays one control for "each effect a little slower".
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

    It returns None for a scene that the renderer does not draw from that slot.
    The renderer's own setting then has no effect: the renderer replaces the
    content of a rainbow snapshot only, so an answer here changes nothing.

    The value goes to the renderer for each frame and is not built into the
    renderer. There is one renderer, and it belongs to Steam and to the desktop
    equally.

    The two modes disagree about the content of the rainbow slot, and that is
    the purpose. A renderer can hold one answer only.
    """
    return SCENE_SHOWS.get(scene)


def describe(scene, color, brightness, speed):
    """Returns one scene in one line, with each setting that has an effect.

    It names those settings only. A report that omits one reads as a setting
    with no effect.

    That was the appearance of the brightness under a rainbow. A rainbow is the
    one lit scene with no colour of the person's, and a user reported that "the
    control does nothing".

    The four effects that draw themselves thus also have an answer here. A load
    gauge uses neither the brightness nor the speed: it draws a reading, and
    both settings would change the reading and not the appearance. See
    render.rainbow_takes.

    The names are the names of the settings and not the names of the protocol.
    "color" is what a person selects. "manual" is the shim's name for the
    effect that it becomes. Only the first name is one that a person can find.
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

    It returns the name and not a boolean. The name separates two states:
    "this machine has no Game Mode session" and "it has one with a name that
    this module did not expect". Only the second state is a fault to repair
    here.
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

    It returns None and not zero. The two values have opposite meanings here.
    Zero is a machine that started now. None is a question that this cannot
    answer.

    A guess of "started now" holds the scene off on each machine with no
    /proc/uptime.
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

    The shim marks each write with ktime_get_ns(). time.monotonic() reads the
    same clock, so a subtraction of the two needs no conversion.

    The counter says whether a write occurred. The module sets the counter to
    UNTOUCHED_SEQ and marks the time at its load. A device with no write thus
    carries a time that means "the load of the module". Without the counter,
    that time reads as a write that occurred now.
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

    This function reads the journal, and this project does not leave the read
    to a command that a person types. It is the only way to see the Game Mode
    half.

    Game Mode has no terminal. A person can thus read the decisions of the
    service only after the session. An instruction to send journalctl through
    grep is a step at which most people stop.

    It returns a message in place of the lines when it cannot read the journal.
    That is a different answer from "the journal has nothing". The first means
    "try again with sudo". The second means "this machine never recognised Game
    Mode".
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

    The bar is Steam's while Game Mode runs, and for some seconds after the
    last write by Steam. It is the desktop's at each other time.

    This class reads the process table one time in each CHECK_SECONDS. The
    answer cannot change faster than a person changes sessions, and the loop
    asks sixty times each second.
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

    def game_mode(self, now):
        """Returns the Game Mode process that runs now, or "". From the cache."""
        first = self._asked is None
        if first or now - self._asked >= self.interval:
            found = self.look()
            if first or found != self.found:
                # The journal is the only way to see this change, because the
                # only place to watch it from is Game Mode, and Game Mode has
                # no terminal. This thus writes one line at the start of the
                # service and one line at each change after it.
                #
                # It is the answer to two questions: "why does my scene not
                # appear" and "why does the bar ignore Steam". The two look
                # the same to a person in front of the bar.
                LOG.info(IN_GAME_MODE % found if found else ON_THE_DESKTOP)
            self.found = found
            self._asked = now
        return self.found

    def still_booting(self, now):
        """Returns whether the machine is too new to know its own mode.

        It reads the uptime one time and keeps a moment on the clock of the
        loop. The answer changes in one direction only, and the loop asks
        sixty times each second.

        A machine that does not report its uptime counts as an old machine.
        That is the behaviour of this class before this question existed.
        """
        if self._booted_at is None:
            up = self.uptime()
            self._booted_at = now - (self.boot_settle if up is None else up)
        return now - self._booted_at < self.boot_settle

    def steam_has_it(self, snapshot, now):
        """Returns whether to leave the bar alone because Steam drives it.

        The answer is True in Game Mode. On the desktop it is True while Steam
        continues to write.

        That second part is what puts the progress bar of a download on the
        bar. Each write extends the time again, so the bar stays Steam's while
        the download fills it.

        Then there is the write that ends it. At the end of a download, Steam
        puts back the effect from Game Mode. Its last write is thus the rest
        state from before the download, and the grace time would show that
        state for its full length.

        A user reported those seconds: a Game Mode effect that nobody asked
        for, between the download and the desktop effect.

        A write that puts back the remembered state thus ends the grace time
        and does not extend it. Steam that puts back what was already there is
        Steam that gives up the bar, and not Steam that takes it.

        This test is exact. It is not a guess about the interval between two
        writes by Steam, which this class cannot know. If the restored state
        is not the remembered state, nothing is lost: the grace time ends as
        before.

        "What was already there" ignores the brightness, and that is the
        second half of this method.

        The measurement, on a Steam Machine: Steam puts a fade of its own
        effect at each end of a download. It makes the rainbow dim before the
        progress bar and bright again after it, one step in each thirty
        milliseconds.

        Each step of both fades is different from the rest state in the
        brightness and in nothing else. A comparison of the full snapshot thus
        read each step as Steam that takes the bar, and the Game Mode effect
        appeared at both ends of each download. It is the same effect, dim. It
        is not Steam that shows something else.

        The last question is the time to wait between two writes by Steam, and
        there are two answers.

        While Steam rests, it is the short grace time, which is for the
        handover.

        While Steam shows something of its own, it is BUSY_SECONDS. Steam
        writes a progress bar one time for each step that it can show: seven
        seconds apart on a fast line, and more on a slow line. The bar is
        Steam's for that full interval and not for its first two seconds. With
        the grace time only, the two effects took the bar from each other for
        the full download.

        Before both of those there is the boot, where the answer is neither.
        See still_booting.
        """
        if self.game_mode(now):
            # No value from the desktop stays valid after a Game Mode session.
            # The rest state of Steam after such a session is what that session
            # leaves, and the handover below finds that state itself.
            #
            # An old value reads as Steam that shows something of its own, and
            # it holds the bar for the full time below.
            self.at_rest = None
            return True
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
