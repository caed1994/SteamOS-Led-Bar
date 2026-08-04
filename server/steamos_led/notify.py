"""Notification overlay: briefly take over the bar, then hand it back.

Detecting a Steam achievement is the unsolved half of this feature - Steam
exposes no documented local signal - so the overlay is driven by an explicit
trigger instead: anything that can write a line into a FIFO can flash the bar.
That keeps the light show testable and lets the detector be swapped out later
without touching this code.
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
KINDS = {
    "achievement": (255, 215, 0),    # gold
    "message": (0, 120, 255),        # blue
    "friend": (0, 200, 80),          # green
    "warning": (255, 60, 0),         # orange-red
}

PULSES = 3          # how often the bar swells during one notification
FADE_TAIL = 0.25    # fraction of the duration spent fading back out

STYLE_BLOOM = "bloom"
STYLE_PULSE = "pulse"

# The bloom, as fractions of the notification's duration: grow out of the
# middle, breathe once while fully out, then shrink back into the middle.
BLOOM_EXPANDED = 0.28
BLOOM_RETRACT = 0.64
# How far down the breath dips. Not to zero: a hard off reads as a blink, and
# the point is that it should look like the bar is taking a breath.
BLOOM_BREATH_FLOOR = 0.08
# How soft the travelling edge is, in units of the half-strip. Without it the
# front is a hard step, which looks blocky on a short bar.
BLOOM_FEATHER = 0.18


def _breath(progress):
    """One smooth inhale and exhale across the middle phase, 0..1."""
    span = BLOOM_RETRACT - BLOOM_EXPANDED
    position = (progress - BLOOM_EXPANDED) / span
    # The shared envelope starts dark; this one starts lit, dips to the floor
    # and comes back, so it runs half a cycle ahead. It never switches off - a
    # hard off would read as a blink rather than a breath.
    return breath_envelope(position + 0.5, BLOOM_BREATH_FLOOR)


def bloom_levels(progress, led_count):
    """Per-LED brightness for the bloom, 0..1, at a point in the flash."""
    if led_count < 1:
        return []

    # The front has to travel one feather past the last LED, otherwise the
    # outermost pair sits exactly on the edge and never lights at all.
    full = 1.0 + BLOOM_FEATHER
    brightness = 1.0

    if progress < BLOOM_EXPANDED:
        radius = full * progress / BLOOM_EXPANDED    # growing out of the middle
    elif progress < BLOOM_RETRACT:
        radius = full                                # fully out, breathing
        brightness = _breath(progress)
    else:
        radius = full * (1.0 - (progress - BLOOM_RETRACT) / (1.0 - BLOOM_RETRACT))

    if led_count == 1:
        # Nothing to travel across, but the timing should still match.
        return [max(0.0, min(radius / full * brightness, 1.0))]

    centre = (led_count - 1) / 2.0
    levels = []
    for index in range(led_count):
        # 0 at the middle, 1 at either end.
        distance = abs(index - centre) / centre
        level = (radius - distance) / BLOOM_FEATHER
        levels.append(max(0.0, min(level, 1.0)) * brightness)
    return levels


def pulse_levels(progress, led_count):
    """Per-LED brightness for the pulse: swell a few times, then fade out.

    The tail keeps it from ending on a hard edge.
    """
    pulse = (1.0 - math.cos(2.0 * math.pi * PULSES * progress)) * 0.5
    if progress > 1.0 - FADE_TAIL:
        pulse *= (1.0 - progress) / FADE_TAIL
    return [pulse] * led_count


# The shapes a notification can take. config validates NOTIFY_STYLE against
# STYLES, so this table is the only place a new one has to be registered.
_STYLES = {
    STYLE_BLOOM: bloom_levels,
    STYLE_PULSE: pulse_levels,
}
STYLES = tuple(_STYLES)


def parse_color(text):
    """Accept a kind name, '#rrggbb', 'rrggbb' or 'r,g,b'."""
    value = str(text).strip().lower()
    if value in KINDS:
        return KINDS[value]

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

    def __init__(self, color, duration, started, style=STYLE_BLOOM):
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
        return _STYLES[self.style](progress, led_count)


class NotificationOverlay:
    """Holds the active flash and paints it over a rendered frame."""

    def __init__(self, enabled=True, duration=3.5, led_count=17,
                 style=STYLE_BLOOM):
        self.enabled = enabled
        self.duration = duration
        self.led_count = led_count
        self.style = style if style in STYLES else STYLE_BLOOM
        self.current = None

    @property
    def active(self):
        return self.current is not None

    def trigger(self, kind, now):
        """Start a flash. `kind` is a name or a colour; unknown input is logged."""
        if not self.enabled:
            return False
        try:
            color = parse_color(kind)
        except ValueError as exc:
            LOG.warning("ignoring notification %r: %s", kind, exc)
            return False
        LOG.info("notification: %s", kind)
        self.current = Notification(color, self.duration, now, self.style)
        return True

    def apply(self, payload, now):
        """Return the frame to send: the flash while one runs, else `payload`."""
        if self.current is None:
            return payload
        levels = self.current.levels(now, self.led_count)
        if levels is None:
            self.current = None
            return payload

        red, green, blue = self.current.color
        frame = bytearray()
        for level in levels:
            frame.append(int(red * level + 0.5))
            frame.append(int(green * level + 0.5))
            frame.append(int(blue * level + 0.5))
        return bytes(frame)

    def clear(self):
        self.current = None


class FifoTrigger:
    """A named pipe anything can write a trigger word into.

    Kept deliberately dumb: one line, one flash. A desktop script, a game
    launcher hook or a future achievement watcher can all drive it without
    knowing anything about this service.
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

        # O_RDWR keeps the pipe open across writers; with O_RDONLY every writer
        # that closes would leave us in permanent EOF.
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
        # Cap the buffer so a writer spamming without newlines cannot grow it.
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
