# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""Notification overlay: briefly take over the bar, then hand it back.

Driven by an explicit trigger rather than by any detector of its own: anything
that can write a line into a FIFO can flash the bar. That keeps the light show
testable and the detector replaceable without touching this code.
"""

from __future__ import annotations

import errno
import logging
import math
import os
import stat

from .render import breath_envelope

LOG = logging.getLogger(__name__)

DEFAULT_FIFO = "/run/steamos-led-serial/notify"

# Named triggers. Anything else is parsed as a colour, so a caller can flash an
# arbitrary one with e.g. `--notify '#00ff88'`.
KIND_ACHIEVEMENT = "achievement"
KIND_MESSAGE = "message"
KIND_FRIEND = "friend"
KIND_PHONE = "phone"
KIND_WARNING = "warning"

# Four hues off the wheel, as far apart as four can be while leaving red to
# the warning. They match what the panel offers - a default it does not offer
# would open every colour menu on an entry it had to invent for itself.
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

# What a per-kind style is set to when it should simply follow the general one.
# Not a style itself: it never reaches _STYLES, it only means "not set here".
STYLE_INHERIT = "default"

# The bloom, as fractions of the duration: grow out of the middle, breathe once
# while fully out, then shrink back in.
BLOOM_EXPANDED = 0.28
BLOOM_RETRACT = 0.64
# How far the breath dips. Not to zero - a hard off reads as a blink.
BLOOM_BREATH_FLOOR = 0.08
# Softness of the travelling edge, in units of the half-strip. Without it the
# front is a hard step, which looks blocky on a short bar.
BLOOM_FEATHER = 0.18

# The double flash, in seconds rather than in fractions: what makes it read as
# a flash is that it is *short*, and a shape measured in fractions would slow
# down as the notification is made longer until it was merely blinking.
FLASH_LIT = 0.08            # one blink
FLASH_GAP = 0.08            # the dark between the pair, and what pairs them
FLASH_PERIOD = 1.0          # from one pair to the next
FLASH_PATTERN = FLASH_LIT * 2 + FLASH_GAP
# At most this much of a period may be pair; the rest is the dark that
# separates one pair from the next, and the last flash from whatever follows.
FLASH_FILL = 0.8

# The comet, in fractions of the strip so it looks the same on 17 LEDs as on
# 144. The head is the rising edge, the tail what it drags behind it.
COMET_HEAD = 0.06
COMET_TAIL = 0.33
# Floors in LEDs, for strips too short for the fractions to mean anything.
COMET_MIN_HEAD = 0.8
COMET_MIN_TAIL = 1.0

# The sparkle, in seconds like the flashes: a grain of glitter is a grain of
# glitter whether the notification lasts two seconds or ten, and stretching
# one into a slow fade would turn the whole effect into a lava lamp.
SPARK_LIFE = 0.50           # how long one grain stays visible
# Each LED gets its own period out of this range. Nothing shared, nothing in a
# whole ratio to anything else - that is what stops the strip from settling
# into a rhythm and starting to look like a pattern.
#
# The life and the two ends move together: how densely the strip is covered is
# life divided by period, so scaling all three keeps the same amount of light
# on the bar and only changes how hurried it looks.
SPARK_PERIOD_MIN = 0.90
SPARK_PERIOD_MAX = 2.55
# Two irrationals, used to give each LED a period and a head start. The
# fractional parts of their multiples spread evenly and never repeat, which is
# a well-behaved substitute for randomness - and unlike randomness it draws
# the same picture after a dropped frame instead of jumping.
SPARK_SPREAD_PERIOD = 0.7548776662
SPARK_SPREAD_OFFSET = 0.5698402909

# The alternating halves, in seconds for the same reason as the double flash.
# One period is both sides once, so half a second is two switches a second -
# about the rate an emergency vehicle's wig-wag runs at, and no accident.
ALTERNATE_PERIOD = 0.5
# How much of a period each side is lit. The remainder is a hairline of dark
# between the two, which sharpens the "two sides" read and, at the end of the
# last period, is what leaves the bar dark for whatever comes next.
ALTERNATE_DUTY = 0.45


def _breath(progress):
    """One smooth inhale and exhale across the middle phase, 0..1."""
    span = BLOOM_RETRACT - BLOOM_EXPANDED
    position = (progress - BLOOM_EXPANDED) / span
    # Half a cycle ahead of the shared envelope, which starts dark: this one
    # starts lit, dips to the floor and comes back.
    return breath_envelope(position + 0.5, BLOOM_BREATH_FLOOR)


def bloom_levels(progress, led_count, duration):
    """Per-LED brightness for the bloom, 0..1, at a point in the flash."""
    if led_count < 1:
        return []

    # The front travels one feather past the last LED, or the outermost pair
    # sits exactly on the edge and never lights.
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
        # Nothing to travel across, but keep the timing.
        return [max(0.0, min(radius / full * brightness, 1.0))]

    centre = (led_count - 1) / 2.0
    levels = []
    for index in range(led_count):
        # 0 at the middle, 1 at either end.
        distance = abs(index - centre) / centre
        level = (radius - distance) / BLOOM_FEATHER
        levels.append(max(0.0, min(level, 1.0)) * brightness)
    return levels


def pulse_levels(progress, led_count, duration):
    """Per-LED brightness for the pulse: swell a few times, then fade out."""
    pulse = (1.0 - math.cos(2.0 * math.pi * PULSES * progress)) * 0.5
    if progress > 1.0 - FADE_TAIL:
        pulse *= (1.0 - progress) / FADE_TAIL
    return [pulse] * led_count


def double_flash_levels(progress, led_count, duration):
    """Per-LED brightness for the double flash: blink, blink, wait, again.

    The whole bar at once and with hard edges, which is the opposite of what
    the bloom wants - here the sharpness is the message. Timed in seconds, so
    a longer notification means more pairs rather than slower ones.
    """
    # Whole periods only: a pair cut off halfway would read as a single blink
    # and, at the end, as a flash that forgot to stop.
    periods = max(1, round(duration / FLASH_PERIOD))
    period = duration / periods

    # A period too short for a pair at full speed gets a compressed one rather
    # than a truncated one. Only reachable by hand-editing NOTIFY_DURATION
    # below a second, which the panel no longer offers.
    scale = min(1.0, period * FLASH_FILL / FLASH_PATTERN)
    lit, gap = FLASH_LIT * scale, FLASH_GAP * scale

    elapsed = (progress * duration) % period
    on = elapsed < lit or lit + gap <= elapsed < lit + gap + lit
    return [1.0 if on else 0.0] * led_count


def comet_levels(progress, led_count, duration):
    """Per-LED brightness for the comet: a head with a tail, once across.

    The only shape with a *place*: the others differ in time, this one in
    space, which is what the eye catches without looking straight at it. It
    starts before the first LED and ends past the last, so neither end of the
    bar gets a comet appearing out of nothing or dying on the edge.
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
            # Squared, so the brightness sits near the head and the far end of
            # the tail is a glow rather than half a lit bar.
            levels.append((1.0 - behind / tail) ** 2)
    return levels


def sparkle_levels(progress, led_count, duration):
    """Per-LED brightness for the sparkle: grains lighting and dying out.

    The only shape without an order to it. The others all say "here is a
    thing, watch it happen"; this one just glitters, which is why it suits the
    notifications you are glad to get rather than the ones you must act on.

    Each LED runs its own little clock at its own rate, so nothing marches and
    nothing lines up. It fades out at the end rather than stopping, or the
    last grain would be cut off mid-life and read as a fault.
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
        # Lit the instant it starts and decaying away: a grain that faded in
        # would be a slow pulse, and glitter is all attack.
        grain = 0.0 if age >= SPARK_LIFE else (1.0 - age / SPARK_LIFE) ** 2
        levels.append(grain * fade)
    return levels


def _spread(index, irrational):
    """A number in 0..1 for this LED - evenly spread, and always the same."""
    value = index * irrational
    return value - math.floor(value)


def alternate_levels(progress, led_count, duration):
    """Per-LED brightness for the alternating halves: left, right, left.

    The one shape that says "something is wrong" rather than "something
    happened" - two sides in antiphase is what an emergency vehicle does, and
    nothing else on this bar looks remotely like it. On an odd strip the
    middle LED stays dark, which makes the two sides read as two.
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
    # Too short to spare one for the gap: three LEDs would leave one a side.
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


# The shapes a notification can take; config validates NOTIFY_STYLE against
# STYLES, so a new one only has to be registered here. Each takes the position
# within the flash, the strip length and how long the whole flash lasts - the
# last one because a shape may want a tempo of its own rather than one that
# stretches with the setting.
_STYLES = {
    STYLE_BLOOM: bloom_levels,
    STYLE_PULSE: pulse_levels,
    STYLE_DOUBLE_FLASH: double_flash_levels,
    STYLE_COMET: comet_levels,
    STYLE_ALTERNATE: alternate_levels,
    STYLE_SPARKLE: sparkle_levels,
}
STYLES = tuple(_STYLES)

# Kinds that always look the same, whatever the configuration says. A warning
# is the one notification you must not have to recognise - it has to mean the
# same thing on every machine, so red and the alarm shape are not offered as a
# choice. Everything else is yours to arrange.
FIXED_KINDS = {KIND_WARNING: STYLE_ALTERNATE}


# A trigger may name the shape to use for that one flash: "comet:#1a9fff".
# Nothing is stored - it is how you compare the shapes without first writing
# one into the config and restarting, which is the wrong way round for
# choosing. Only a known shape counts as a prefix, so a colour that happens to
# contain a colon still fails as the nonsense it is.
SHAPE_SEPARATOR = ":"


def split_shape(text):
    """(shape or None, the rest) - the shape asked for by this trigger."""
    shape, separator, rest = str(text).strip().partition(SHAPE_SEPARATOR)
    if separator and shape.strip().lower() in STYLES:
        return shape.strip().lower(), rest
    return None, text


def parse_color(text, kinds=None):
    """Accept a kind name, '#rrggbb', 'rrggbb' or 'r,g,b'.

    `kinds` is the name table; the built-in one is only the default, so a
    service told to flash a different gold can hand in its own.
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
    """One flash in progress."""

    def __init__(self, color, duration, started, style=STYLE_BLOOM, kind=""):
        # The trigger word, kept so the overlay can tell one flash from
        # another without comparing colours - two kinds may share one.
        self.kind = kind
        self.color = color
        self.duration = max(float(duration), 0.1)
        self.started = started
        self.style = style if style in STYLES else STYLE_BLOOM

    def progress(self, now):
        """Position within the flash in 0..1, or None once it is over."""
        elapsed = now - self.started
        if elapsed < 0 or elapsed >= self.duration:
            return None
        return elapsed / self.duration

    def levels(self, now, led_count):
        """Per-LED brightness, or None once the flash is over."""
        progress = self.progress(now)
        if progress is None:
            return None
        return _STYLES[self.style](progress, led_count, self.duration)


# How many flashes may wait their turn. Repeats never queue, so reaching this
# means several genuinely different things happened at once - and a bar
# working through a minute of backlog has stopped reporting and started
# reciting.
MAX_PENDING = 4

# Quiet time after a flash before the same trigger may fire again, on top of
# the flash itself. Without it, someone typing at you once a second holds the
# bar lit indefinitely: the first flash already said "you have messages", and
# every one after it says the same thing again.
DEFAULT_REPEAT_GAP = 10.0


class NotificationOverlay:
    """Holds the active flash and paints it over a rendered frame.

    Flashes queue rather than replace one another. An achievement and a
    message arriving in the same tick used to leave only the second: the bar
    said "message" and never mentioned the achievement at all.
    """

    def __init__(self, enabled=True, duration=3.5, led_count=17,
                 style=STYLE_BLOOM, colors=None,
                 repeat_gap=DEFAULT_REPEAT_GAP, styles=None, reverse=False,
                 max_brightness=255):
        self.enabled = enabled
        self.duration = duration
        self.led_count = led_count
        # A flash goes to the strip without passing the renderer, so REVERSE
        # has to be honoured here too. It made no difference while every shape
        # was symmetric; a comet running against every other effect is exactly
        # the complaint the temperature gauge earned.
        self.reverse = reverse
        # And so does the brightness ceiling, for a better reason than looks:
        # people cap it because the strip runs off the ESP's USB rail, and a
        # flash is the worst case there - the whole bar lit at once. Ignoring
        # it browns out exactly the strips the setting exists to protect.
        #
        # Only this one of the three. MIN_BRIGHTNESS is a floor under what
        # Steam asked for, and a flash asks for nothing - it also has to reach
        # zero at both ends or two in a row would run together. GAMMA reshapes
        # how Steam's own colours are presented; a flash is not Steam's state.
        self.brightness = max(0, min(int(max_brightness), 255)) / 255.0
        self.style = style if style in STYLES else STYLE_BLOOM
        self.repeat_gap = max(0.0, float(repeat_gap))
        # A trigger word stays the interface - callers ask for "achievement",
        # not for a colour - so which gold that is stays a local decision.
        self.colors = dict(KINDS, **(colors or {}))
        for kind in FIXED_KINDS:
            # Fixed means fixed: a colour handed in for one of these is
            # dropped rather than honoured, so nothing downstream can quietly
            # make a warning look like anything but a warning.
            self.colors[kind] = KINDS[kind]
        # Same again for the shape. Only kinds that want their own are in
        # here; everything else, an arbitrary colour included, uses `style`.
        self.styles = {kind: shape for kind, shape
                       in (styles or {}).items() if shape in STYLES}
        self.current = None
        # (kind, colour, shape), in the order they arrived
        self.pending = []
        self._quiet_until = {}      # kind -> when it may be shown again

    @property
    def active(self):
        return self.current is not None or bool(self.pending)

    def trigger(self, kind, now):
        """Show a flash, or put it in the queue. `kind` is a name or a colour.

        Returns whether it will be shown at all: unknown input, a repeat of
        something the bar just said, and a full queue are each logged and
        dropped rather than raised.
        """
        if not self.enabled:
            return False
        shape, wanted = split_shape(kind)
        try:
            color = parse_color(wanted, self.colors)
        except ValueError as exc:
            LOG.warning("ignoring notification %r: %s", kind, exc)
            return False

        if now < self._quiet_until.get(kind, 0.0):
            # Still showing this one, or only just finished it. Repeating adds
            # nothing, and restarting it mid-flash blinks the bar out and
            # regrows it - which is what a burst used to look like.
            LOG.debug("skipping %r, the bar just said that", kind)
            return False
        if any(waiting == kind for waiting, _color, _shape in self.pending):
            return False
        if self.current is None:
            self._start(kind, color, now, shape)
            return True
        if len(self.pending) >= MAX_PENDING:
            LOG.info("dropping %r, %d flashes are already waiting",
                     kind, len(self.pending))
            return False

        self.pending.append((kind, color, shape))
        LOG.info("notification queued: %s (%d waiting)", kind,
                 len(self.pending))
        return True

    def _start(self, kind, color, now, shape=None):
        LOG.info("notification: %s", kind)
        # An explicit shape wins, including over a fixed kind: it can only
        # come from someone who wrote it into the pipe by hand. Nothing that
        # detects a warning ever names one, so the automatic warning still
        # looks the same everywhere - which is the property that matters.
        style = shape or FIXED_KINDS.get(kind) or self.styles.get(kind,
                                                                 self.style)
        self.current = Notification(color, self.duration, now, style, kind)
        # Measured from the start, so one window covers the flash and the gap
        # after it. Expired entries are dropped here, or an arbitrary colour
        # per flash would grow this map without end.
        self._quiet_until = {name: until for name, until
                             in self._quiet_until.items() if until > now}
        self._quiet_until[kind] = now + self.duration + self.repeat_gap

    def frame(self, now):
        """The flash's own frame, or None when no flash is running.

        Separate from apply() so the caller can skip rendering underneath: a
        flash covers the whole bar, so that frame would only be thrown away.
        """
        levels = None
        while self.current is not None:
            levels = self.current.levels(now, self.led_count)
            if levels is not None:
                break
            # That one is over. The next in line starts where it left off,
            # which is why they never blend: a flash both starts and ends dark.
            self.current = None
            if self.pending:
                kind, color, shape = self.pending.pop(0)
                self._start(kind, color, now, shape)
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
        """Return the frame to send: the flash while one runs, else `payload`."""
        frame = self.frame(now)
        return payload if frame is None else frame


class FifoTrigger:
    """A named pipe anything can write a trigger word into.

    Deliberately dumb - one line, one flash - so a desktop script, a launcher
    hook or the achievement watcher can drive it without knowing this service.
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
        # mkfifo is subject to umask, so set the mode explicitly.
        os.chmod(self.path, self.mode)

        # O_RDWR keeps the pipe open across writers; O_RDONLY would leave us in
        # permanent EOF as soon as one closes.
        self.fd = os.open(self.path, os.O_RDWR | os.O_NONBLOCK)
        LOG.info("notification trigger listening on %s", self.path)

    def read(self):
        """Return the trigger words written since the last call."""
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
        # Cap it so a writer spamming without newlines cannot grow it.
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
    """Write a trigger from the command line into a running service's FIFO."""
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
