# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""The notification overlay. It takes the bar for some seconds and returns it.

A trigger drives it. It has no detector of its own. Each program that can write
a line into a FIFO can thus flash the bar.

The light show stays testable, and a new detector needs no change to this
module.
"""

from __future__ import annotations

import errno
import logging
import math
import os
import stat

from .render import breath_envelope

LOG = logging.getLogger(__name__)

DEFAULT_FIFO = "/run/steamos-utility-center/notify"

# The named triggers. This module reads each other word as a colour, so a
# caller can flash any colour with `--notify '#00ff88'`.
KIND_ACHIEVEMENT = "achievement"
KIND_MESSAGE = "message"
KIND_FRIEND = "friend"
KIND_PHONE = "phone"
KIND_WARNING = "warning"

# Four hues from the wheel, at the maximum distance for four hues that leave
# red for the warning.
#
# They are the hues that the panel offers. A default that the panel does not
# offer opens each colour menu on an entry that the menu must add itself.
KINDS = {
    KIND_ACHIEVEMENT: (255, 255, 0),    # yellow
    KIND_MESSAGE: (128, 0, 255),        # purple
    KIND_FRIEND: (0, 255, 0),           # green
    KIND_PHONE: (0, 255, 255),          # cyan: nothing on the machine is
    KIND_WARNING: (255, 0, 0),          # red, and only ever red
}

PULSES = 3          # how often the bar swells during one notification
FADE_TAIL = 0.25    # fraction of the duration spent fading back out

STYLE_BLOOM = "bloom"
STYLE_PULSE = "pulse"
STYLE_DOUBLE_FLASH = "double_flash"
STYLE_COMET = "comet"
STYLE_ALTERNATE = "alternate"
STYLE_SPARKLE = "sparkle"

# The value of a style for one kind when that kind follows the general style.
# It is not a style: it never reaches _STYLES. It means "not set here".
STYLE_INHERIT = "default"

# The bloom, as fractions of the duration. It grows from the centre, breathes
# one time at its full length, and then decreases again.
BLOOM_EXPANDED = 0.28
BLOOM_RETRACT = 0.64
# The low point of the breath. It is not zero. A full off reads as a blink.
BLOOM_BREATH_FLOOR = 0.08
# The softness of the moving edge, in units of half of the strip. Without it,
# the edge is a hard step, and that is visible on a short bar.
BLOOM_FEATHER = 0.18

# The double flash, in seconds and not in fractions.
#
# A person reads this shape as a flash because it is *short*. A shape in
# fractions becomes slower as the notification becomes longer, until it is only
# a slow blink.
FLASH_LIT = 0.08            # one blink
FLASH_GAP = 0.08            # the dark between the pair, and what pairs them
FLASH_PERIOD = 1.0          # from one pair to the next
FLASH_PATTERN = FLASH_LIT * 2 + FLASH_GAP
# The maximum part of a period that the pair takes. The remainder is the dark
# part. It separates one pair from the next pair, and the last flash from the
# effect after it.
FLASH_FILL = 0.8

# The comet, in fractions of the strip. It thus looks the same on 17 LEDs and
# on 144. The head is the leading edge, and the tail is behind it.
COMET_HEAD = 0.06
COMET_TAIL = 0.33
# The minimum sizes in LEDs, for a strip that is too short for the fractions.
COMET_MIN_HEAD = 0.8
COMET_MIN_TAIL = 1.0

# The sparkle, in seconds as the flashes are. A point of light is the same at a
# notification of two seconds and at one of ten seconds. To make each point
# into a slow fade changes the effect completely.
SPARK_LIFE = 0.50           # how long one grain stays visible
# Each LED takes its own period from this range. No period is shared, and no
# two periods have a whole ratio. That is what stops the strip from reaching a
# rhythm and looking like a pattern.
#
# The life and the two limits change together. The density of the light is the
# life divided by the period. To scale all three thus keeps the same quantity
# of light on the bar and changes only the speed.
SPARK_PERIOD_MIN = 0.90
SPARK_PERIOD_MAX = 2.55
# Two irrational numbers. They give each LED a period and a start offset.
#
# The fractional parts of their multiples are evenly spread and never repeat.
# They are thus a good substitute for random numbers. They also draw the same
# picture after a lost frame, and random numbers do not.
SPARK_SPREAD_PERIOD = 0.7548776662
SPARK_SPREAD_OFFSET = 0.5698402909

# The two halves in sequence, in seconds for the reason of the double flash.
#
# One period is both sides one time. Half a second is thus two changes each
# second. That is the rate of the lights of an emergency vehicle, and the same
# rate here is deliberate.
ALTERNATE_PERIOD = 0.5
# The part of a period that each side is lit. The remainder is a thin dark part
# between the two sides. It makes the "two sides" clearer. At the end of the
# last period it also leaves the bar dark for the next effect.
ALTERNATE_DUTY = 0.45


def _breath(progress):
    """One smooth inhale and exhale across the middle phase, 0..1."""
    span = BLOOM_RETRACT - BLOOM_EXPANDED
    position = (progress - BLOOM_EXPANDED) / span
    # Half a cycle ahead of the shared envelope, which starts dark: this one
    # starts lit, dips to the floor and comes back.
    return breath_envelope(position + 0.5, BLOOM_BREATH_FLOOR)


def bloom_levels(progress, led_count, duration):
    """Returns the brightness of each LED for the bloom, at one point."""
    if led_count < 1:
        return []

    # The edge moves one feather past the last LED. Without that, the
    # outermost pair is exactly on the edge and never lights.
    full = 1.0 + BLOOM_FEATHER
    brightness = 1.0

    if progress < BLOOM_EXPANDED:
        radius = full * progress / BLOOM_EXPANDED
    elif progress < BLOOM_RETRACT:
        radius = full
        brightness = _breath(progress)
    else:
        radius = full * (1.0 - (progress - BLOOM_RETRACT) / (1.0 - BLOOM_RETRACT))

    if led_count == 1:
        # There is no distance to move, but keep the timing.
        return [max(0.0, min(radius / full * brightness, 1.0))]

    centre = (led_count - 1) / 2.0
    levels = []
    for index in range(led_count):
        # 0 at the centre, 1 at each end.
        distance = abs(index - centre) / centre
        level = (radius - distance) / BLOOM_FEATHER
        levels.append(max(0.0, min(level, 1.0)) * brightness)
    return levels


def pulse_levels(progress, led_count, duration):
    """Returns the brightness for the pulse: three increases, then a fade."""
    pulse = (1.0 - math.cos(2.0 * math.pi * PULSES * progress)) * 0.5
    if progress > 1.0 - FADE_TAIL:
        pulse *= (1.0 - progress) / FADE_TAIL
    return [pulse] * led_count


def double_flash_levels(progress, led_count, duration):
    """Returns the brightness for the double flash: flash, flash, wait, again.

    It uses the full bar at one time and with hard edges. That is the opposite
    of the bloom. Here the hard edge is the message.

    The timing is in seconds, so a longer notification gives more pairs and
    not slower pairs.
    """
    # Whole periods only: a pair cut off halfway would read as a single blink
    # and, at the end, as a flash that forgot to stop.
    periods = max(1, round(duration / FLASH_PERIOD))
    period = duration / periods

    # A period that is too short for a pair at full speed gets a compressed
    # pair and not a pair that stops in the middle. This occurs only after an
    # edit of NOTIFY_DURATION below one second, which the panel does not
    # offer.
    scale = min(1.0, period * FLASH_FILL / FLASH_PATTERN)
    lit, gap = FLASH_LIT * scale, FLASH_GAP * scale

    elapsed = (progress * duration) % period
    on = elapsed < lit or lit + gap <= elapsed < lit + gap + lit
    return [1.0 if on else 0.0] * led_count


def comet_levels(progress, led_count, duration):
    """Returns the brightness for the comet: a head and a tail, one time.

    This is the one shape with a *position*. The others change in time. This
    one changes in space, and the eye sees that without direct attention.

    It starts before the first LED and ends after the last LED. Neither end of
    the bar thus shows a comet that appears from nothing or stops at the edge.
    """
    if led_count < 1:
        return []

    head = max(COMET_MIN_HEAD, COMET_HEAD * led_count)
    tail = max(COMET_MIN_TAIL, COMET_TAIL * led_count)
    travel = (led_count - 1) + tail + head
    position = progress * travel - head

    levels = []
    for index in range(led_count):
        behind = position - index
        if behind < -head or behind > tail:
            levels.append(0.0)          # not reached yet, or already passed
        elif behind <= 0.0:
            levels.append((behind + head) / head)       # the rising front
        else:
            # The square, so the brightness is near the head. The far end of
            # the tail is thus a low light and not half of a lit bar.
            levels.append((1.0 - behind / tail) ** 2)
    return levels


def sparkle_levels(progress, led_count, duration):
    """Returns the brightness for the sparkle: points that light and fade.

    This is the one shape with no order. Each other shape says "here is an
    event, watch it". This one only glitters. It thus suits the notifications
    that a person is glad to receive, and not the ones that need an action.

    Each LED runs its own clock at its own rate. Nothing moves together and
    nothing is in line.

    It fades at the end and does not stop. Without the fade, the last point
    stops in the middle of its life and reads as a fault.
    """
    if led_count < 1:
        return []

    elapsed = progress * duration
    span = SPARK_PERIOD_MAX - SPARK_PERIOD_MIN
    fade = 1.0
    if progress > 1.0 - FADE_TAIL:
        fade = max(0.0, (1.0 - progress) / FADE_TAIL)

    levels = []
    for index in range(led_count):
        period = SPARK_PERIOD_MIN + span * _spread(index, SPARK_SPREAD_PERIOD)
        offset = period * _spread(index, SPARK_SPREAD_OFFSET)
        age = (elapsed + offset) % period
        # Each point is lit at its start and then fades. A point that also
        # faded in is a slow pulse, and this effect needs a sharp start.
        grain = 0.0 if age >= SPARK_LIFE else (1.0 - age / SPARK_LIFE) ** 2
        levels.append(grain * fade)
    return levels


def _spread(index, irrational):
    """Returns a number from 0 to 1 for this LED. It is stable and spread."""
    value = index * irrational
    return value - math.floor(value)


def alternate_levels(progress, led_count, duration):
    """Returns the brightness for the two halves: left, right, left.

    This is the one shape that says "something is wrong" and not "something
    occurred". Two sides in opposite phase is what an emergency vehicle does,
    and no other effect on this bar looks like it.

    On a strip with an odd length, the centre LED stays dark. That makes the
    two sides read as two.
    """
    if led_count < 1:
        return []

    periods = max(1, round(duration / ALTERNATE_PERIOD))
    period = duration / periods
    elapsed = (progress * duration) % period

    if elapsed < period * ALTERNATE_DUTY:
        side = 0
    elif period * 0.5 <= elapsed < period * (0.5 + ALTERNATE_DUTY):
        side = 1
    else:
        return [0.0] * led_count        # the hairline between the two

    half = led_count // 2
    # The strip is too short for a dark centre LED: three LEDs would then
    # leave one LED for each side.
    gap = 1 if led_count % 2 and led_count >= 5 else 0
    levels = []
    for index in range(led_count):
        if gap and index == half:
            levels.append(0.0)
        elif (index < half) == (side == 0):
            levels.append(1.0)
        else:
            levels.append(0.0)
    return levels


# The shapes that a notification can take. config validates NOTIFY_STYLE
# against STYLES, so a new shape needs one entry here.
#
# Each shape takes the position in the flash, the length of the strip and the
# length of the full flash. It takes the last value because a shape can need
# its own rate and not a rate that changes with the setting.
_STYLES = {
    STYLE_BLOOM: bloom_levels,
    STYLE_PULSE: pulse_levels,
    STYLE_DOUBLE_FLASH: double_flash_levels,
    STYLE_COMET: comet_levels,
    STYLE_ALTERNATE: alternate_levels,
    STYLE_SPARKLE: sparkle_levels,
}
STYLES = tuple(_STYLES)

# The kinds that always look the same, whatever the configuration says.
#
# A warning is the one notification that a person must recognise with no
# learning. It must mean the same thing on each machine, so red and the alarm
# shape are not a choice. Each other kind is a choice.
FIXED_KINDS = {KIND_WARNING: STYLE_ALTERNATE}


# A trigger can also give what makes it different, after an "@".
#
# Nothing about the flash changes. It is only the key of the repeat gap. Two
# different messages are thus two flashes, and two copies of one message are
# one flash.
#
# This is necessary because the important triggers are not different by
# themselves. Each notification from the phone is the word "phone", or one
# colour if the app has a rule.
#
# Without this key, the second message of a conversation was a repeat of the
# first. A WhatsApp message and a Signal message in the same seconds also
# collided.
TAG_SEPARATOR = "@"


def split_tag(text):
    """(what makes this one distinct or None, the trigger itself)."""
    trigger, separator, tag = str(text).strip().partition(TAG_SEPARATOR)
    if not separator:
        return None, str(text).strip()
    return tag.strip() or None, trigger.strip()


# A trigger can name the shape for that one flash: "comet:#1a9fff".
#
# This module stores nothing. It is how a person compares the shapes with no
# write to the configuration and no restart.
#
# Only a known shape counts as a prefix. A colour that contains a colon thus
# still fails, which is correct.
SHAPE_SEPARATOR = ":"


def split_shape(text):
    """Returns (shape or None, the remainder) for this trigger."""
    shape, separator, rest = str(text).strip().partition(SHAPE_SEPARATOR)
    if separator and shape.strip().lower() in STYLES:
        return shape.strip().lower(), rest
    return None, text


def parse_color(text, kinds=None):
    """Accepts a kind name, '#rrggbb', 'rrggbb' or 'r,g,b'.

    `kinds` is the table of names. The built-in table is the default only. A
    service with a different gold can thus give its own table.
    """
    kinds = KINDS if kinds is None else kinds
    value = str(text).strip().lower()
    if value in kinds:
        return kinds[value]

    if "," in value:
        parts = [part.strip() for part in value.split(",")]
        if len(parts) != 3:
            raise ValueError("expected three components in %r" % text)
        try:
            channels = [int(part, 0) for part in parts]
        except ValueError:
            raise ValueError("not a number in %r" % text)
    else:
        hexed = value[1:] if value.startswith("#") else value
        if len(hexed) != 6:
            raise ValueError("expected #rrggbb, got %r" % text)
        try:
            channels = [int(hexed[index:index + 2], 16) for index in (0, 2, 4)]
        except ValueError:
            raise ValueError("not a hex colour: %r" % text)

    if any(channel < 0 or channel > 255 for channel in channels):
        raise ValueError("colour components must be 0..255: %r" % text)
    return tuple(channels)


class Notification:
    """One flash that runs now."""

    def __init__(self, color, duration, started, style=STYLE_BLOOM, kind=""):
        # The trigger word. The overlay keeps it, so that it can separate
        # two flashes with no comparison of colours. Two kinds can share
        # one colour.
        self.kind = kind
        self.color = color
        self.duration = max(float(duration), 0.1)
        self.started = started
        self.style = style if style in STYLES else STYLE_BLOOM

    def progress(self, now):
        """Returns the position in the flash from 0 to 1, or None at the end."""
        elapsed = now - self.started
        if elapsed < 0 or elapsed >= self.duration:
            return None
        return elapsed / self.duration

    def levels(self, now, led_count):
        """Returns the brightness of each LED, or None at the end of the flash."""
        progress = self.progress(now)
        if progress is None:
            return None
        return _STYLES[self.style](progress, led_count, self.duration)


# The number of flashes that can wait.
#
# A repeat never goes into the queue. To reach this limit thus means that
# several different events occurred together. A bar that works through one
# minute of queue no longer reports events. It repeats them.
MAX_PENDING = 4

# The quiet time after a flash before the same trigger can flash again. It is
# in addition to the flash itself.
#
# Without it, a person who writes one message each second holds the bar lit for
# an unlimited time. The first flash already said "you have messages", and each
# flash after it says the same thing.
DEFAULT_REPEAT_GAP = 10.0


class NotificationOverlay:
    """Holds the active flash and draws it over a rendered frame.

    Flashes go into a queue and do not replace each other. An achievement and
    a message in the same moment left only the second one: the bar said
    "message" and did not report the achievement.
    """

    def __init__(self, enabled=True, duration=3.5, led_count=17,
                 style=STYLE_BLOOM, colors=None,
                 repeat_gap=DEFAULT_REPEAT_GAP, styles=None, reverse=False,
                 max_brightness=255):
        self.enabled = enabled
        self.duration = duration
        self.led_count = led_count
        # A flash reaches the strip and does not pass the renderer. This class
        # thus applies REVERSE also.
        #
        # REVERSE made no difference while each shape was symmetric. A comet
        # that moves against each other effect is the same fault that the
        # temperature gauge had.
        self.reverse = reverse
        # It also applies the brightness limit, for a reason that is not
        # appearance. People set that limit because the strip uses the USB line
        # of the ESP, and a flash is the maximum load: the full bar is lit at
        # one time. To ignore the limit thus reduces the voltage on the strips
        # that the setting protects.
        #
        # It applies one of the three settings only. MIN_BRIGHTNESS is a
        # minimum under what Steam asked for, and a flash asks for nothing. A
        # flash must also reach zero at both ends, or two flashes in sequence
        # join together. GAMMA changes how the colours of Steam appear, and a
        # flash is not a state of Steam.
        self.brightness = max(0, min(int(max_brightness), 255)) / 255.0
        self.style = style if style in STYLES else STYLE_BLOOM
        self.repeat_gap = max(0.0, float(repeat_gap))
        # A trigger word stays the interface. A caller asks for
        # "achievement" and not for a colour. Which gold that is thus stays a
        # decision of this module.
        self.colors = dict(KINDS, **(colors or {}))
        for kind in FIXED_KINDS:
            # Fixed means fixed. This method discards a colour for one of
            # these kinds. No later step can thus make a warning look like
            # something that is not a warning.
            self.colors[kind] = KINDS[kind]
        # The same rule for the shape. Only the kinds that need their own
        # shape are here. Each other kind, and each colour, uses `style`.
        self.styles = {kind: shape for kind, shape
                       in (styles or {}).items() if shape in STYLES}
        self.current = None
        # (kind, colour, shape), in the order of arrival
        self.pending = []
        self._quiet_until = {}      # kind -> when it may be shown again

    @property
    def active(self):
        return self.current is not None or bool(self.pending)

    def trigger(self, text, now):
        """Shows a flash, or puts it in the queue. `text` is a name or a colour.

        It returns whether the bar shows the flash. Three inputs give False: an
        unknown word, a repeat of a recent flash, and a full queue. It writes each
        of the three to the log and discards it. It raises nothing.
        """
        if not self.enabled:
            return False
        tag, kind = split_tag(text)
        shape, wanted = split_shape(kind)
        try:
            color = parse_color(wanted, self.colors)
        except ValueError as exc:
            LOG.warning("ignoring notification %r: %s", text, exc)
            return False

        # The key of the quiet window. It is the trigger word, unless the
        # caller gave a key of its own. See split_tag.
        #
        # Without that key, each notification from the phone is the word
        # "phone", and the second message of a conversation is a repeat of
        # the first.
        key = tag or kind

        if now < self._quiet_until.get(key, 0.0):
            # The bar shows this flash now, or it finished it a moment ago. A
            # repeat adds nothing. A restart in the middle of a flash makes the
            # bar dark and then grows it again, and that is what a group of
            # messages looked like.
            LOG.debug("skipping %r, the bar just said that", text)
            return False
        if any(waiting == key for waiting, _c, _s, _k in self.pending):
            return False
        if self.current is None:
            self._start(kind, color, now, shape, key)
            return True
        if len(self.pending) >= MAX_PENDING:
            LOG.info("dropping %r, %d flashes are already waiting",
                     kind, len(self.pending))
            return False

        self.pending.append((key, color, shape, kind))
        LOG.info("notification queued: %s (%d waiting)", kind,
                 len(self.pending))
        return True

    def _start(self, kind, color, now, shape=None, key=None):
        """Puts one flash on the bar. `key` is the key of its quiet window.

        It is the trigger word, unless the caller gave a key of its own.
        """
        LOG.info("notification: %s", kind)
        # A shape in the trigger has priority, and it also has priority over
        # a fixed kind. Only a person who writes into the pipe can give one.
        #
        # No detector of a warning names a shape. The automatic warning thus
        # still looks the same on each machine, and that is the important
        # property.
        style = shape or FIXED_KINDS.get(kind) or self.styles.get(kind,
                                                                 self.style)
        self.current = Notification(color, self.duration, now, style, kind)
        # The window starts at the start of the flash. One window thus covers
        # the flash and the gap after it.
        #
        # This method removes the entries that expired. Without that, one
        # colour for each flash grows this table without a limit.
        self._quiet_until = {name: until for name, until
                             in self._quiet_until.items() if until > now}
        self._quiet_until[key or kind] = now + self.duration + self.repeat_gap

    def frame(self, now):
        """Returns the frame of the flash, or None when no flash runs.

        It is separate from apply(), so that the caller can render nothing
        below it. A flash covers the full bar, so that frame is discarded.
        """
        levels = None
        while self.current is not None:
            levels = self.current.levels(now, self.led_count)
            if levels is not None:
                break
            # That flash is finished. The next flash starts at that point. Two
            # flashes thus never mix: a flash starts dark and ends dark.
            self.current = None
            if self.pending:
                key, color, shape, kind = self.pending.pop(0)
                self._start(kind, color, now, shape, key)
        if self.current is None:
            return None

        red, green, blue = self.current.color
        if self.reverse:
            levels = levels[::-1]
        red *= self.brightness
        green *= self.brightness
        blue *= self.brightness
        frame = bytearray()
        for level in levels:
            frame.append(int(red * level + 0.5))
            frame.append(int(green * level + 0.5))
            frame.append(int(blue * level + 0.5))
        return bytes(frame)

    def apply(self, payload, now):
        """Returns the frame to send: the flash if one runs, else `payload`."""
        frame = self.frame(now)
        return payload if frame is None else frame


class FifoTrigger:
    """A named pipe. Each program can write a trigger word into it.

    It is deliberately simple: one line is one flash. A desktop script, a
    hook in a launcher and the achievement watcher can thus drive it with no
    knowledge of this service.
    """

    def __init__(self, path=DEFAULT_FIFO, mode=0o666):
        self.path = path
        self.mode = mode
        self.fd = -1
        self._buffer = b""

    def open(self):
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)

        if os.path.exists(self.path):
            if not stat.S_ISFIFO(os.stat(self.path).st_mode):
                raise OSError(errno.EEXIST,
                              "%s exists and is not a FIFO" % self.path)
        else:
            os.mkfifo(self.path, self.mode)
        # umask changes the mode of mkfifo, so set the mode again here.
        os.chmod(self.path, self.mode)

        # O_RDWR keeps the pipe open between two writers. With O_RDONLY, the
        # read gives a permanent EOF after the first writer closes.
        self.fd = os.open(self.path, os.O_RDWR | os.O_NONBLOCK)
        LOG.info("notification trigger listening on %s", self.path)

    def read(self):
        """Returns the trigger words that arrived after the last call."""
        if self.fd < 0:
            return []
        try:
            chunk = os.read(self.fd, 4096)
        except BlockingIOError:
            return []
        except OSError as exc:
            if exc.errno in (errno.EINTR, errno.EAGAIN):
                return []
            raise
        if not chunk:
            return []

        self._buffer += chunk
        # A limit, so that a writer with no newlines cannot grow the buffer.
        if len(self._buffer) > 4096:
            self._buffer = self._buffer[-4096:]

        lines = self._buffer.split(b"\n")
        self._buffer = lines.pop()
        return [line.decode("utf-8", "replace").strip()
                for line in lines if line.strip()]

    def close(self):
        if self.fd >= 0:
            try:
                os.close(self.fd)
            finally:
                self.fd = -1

    def unlink(self):
        self.close()
        try:
            os.unlink(self.path)
        except OSError:
            pass


def send(path, kind):
    """Writes a trigger from the command line into the FIFO of the service."""
    try:
        fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            raise OSError(errno.ENOENT,
                          "%s does not exist - is the service running?" % path)
        if exc.errno == errno.ENXIO:
            raise OSError(errno.ENXIO,
                          "nobody is listening on %s - is the service running?"
                          % path)
        raise
    try:
        os.write(fd, (kind + "\n").encode("utf-8"))
    finally:
        os.close(fd)
