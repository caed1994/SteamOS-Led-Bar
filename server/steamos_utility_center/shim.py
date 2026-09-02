# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""The reader for the leds-valve-shim character device.

The shim publishes the LED state that Steam writes in Game Mode. The state is
a packed snapshot of 100 bytes.

read() does not block and always returns the current state. poll() reports
EPOLLIN each time the sequence counter increases.
"""

from __future__ import annotations

import os
import select
import struct

DEFAULT_DEVICE = "/dev/valve-leds-shim"

MAGIC = 0x564C4544  # "VLED"
LOGICAL_LEDS = 17

# The initial value of the counter in the module. It keeps this value until
# something writes. A snapshot with this value thus means that Steam did not
# write to the LEDs after the module load. This is the condition for most of a
# boot, and the module reports "off" for all of that time.
UNTOUCHED_SEQ = 1

HEADER_SIZE = 32
PIXEL_SIZE = 4
SNAPSHOT_SIZE = HEADER_SIZE + LOGICAL_LEDS * PIXEL_SIZE  # 100

_HEADER = struct.Struct("<IHHQQBBBBBBBB")

EFFECT_OFF = 0
EFFECT_MANUAL = 1
EFFECT_NORMAL = 2
EFFECT_RAINBOW = 3
EFFECT_BREATH = 4
EFFECT_PATROL = 5
EFFECT_FACTORY = 6
EFFECT_DEMO = 7

EFFECT_NAMES = {
    EFFECT_OFF: "off",
    EFFECT_MANUAL: "manual",
    EFFECT_NORMAL: "normal",
    EFFECT_RAINBOW: "rainbow",
    EFFECT_BREATH: "breath",
    EFFECT_PATROL: "patrol",
    EFFECT_FACTORY: "factory",
    EFFECT_DEMO: "demo",
}

# The effects that the real hardware animates itself. The snapshot changes only
# when Steam writes new settings, so this service animates these effects.
ANIMATED_EFFECTS = frozenset(
    (EFFECT_RAINBOW, EFFECT_BREATH, EFFECT_PATROL, EFFECT_FACTORY, EFFECT_DEMO)
)


class SnapshotError(ValueError):
    """The device returned data that is not a valid snapshot."""


class Snapshot:
    """The decoded LED state that Steam wrote."""

    __slots__ = (
        "seq", "monotonic_ns", "enabled", "effect", "brightness_scale",
        "delay", "breath_offset", "breath_level", "patrol_num", "color_shift",
        "pixels",
    )

    def __init__(self, seq, monotonic_ns, enabled, effect, brightness_scale,
                 delay, breath_offset, breath_level, patrol_num, color_shift,
                 pixels):
        self.seq = seq
        self.monotonic_ns = monotonic_ns
        self.enabled = enabled
        self.effect = effect
        self.brightness_scale = brightness_scale
        self.delay = delay
        self.breath_offset = breath_offset
        self.breath_level = breath_level
        self.patrol_num = patrol_num
        self.color_shift = color_shift
        self.pixels = pixels  # list of (r, g, b, brightness)

    @property
    def effect_name(self):
        return EFFECT_NAMES.get(self.effect, "unknown(%d)" % self.effect)

    @property
    def is_animated(self):
        return bool(self.enabled) and self.effect in ANIMATED_EFFECTS

    def base_color(self):
        """Returns the colour that Steam selected, for an animated effect."""
        for red, green, blue, _brightness in self.pixels:
            if red or green or blue:
                return (red, green, blue)
        return (255, 255, 255)

    def key(self, brightness=True):
        """Returns each field that changes the rendered output.

        The caller can leave the brightness out. That caller asks whether two
        snapshots are the same effect at two brightnesses, and not two
        different effects. desktop.py asks that question, because a fade of an
        effect by Steam is not a change of the effect.

        One list of fields answers both questions. Two lists become different.
        """
        return (
            self.enabled, self.effect,
            self.brightness_scale if brightness else None, self.delay,
            self.breath_offset, self.breath_level, self.patrol_num,
            self.color_shift, tuple(self.pixels),
        )

    def __repr__(self):
        return (
            "Snapshot(seq=%d, enabled=%d, effect=%s, brightness=%d, delay=%d, "
            "breath=%d/%d, patrol=%d, shift=%d, pixels=%r)"
            % (self.seq, self.enabled, self.effect_name, self.brightness_scale,
               self.delay, self.breath_offset, self.breath_level,
               self.patrol_num, self.color_shift, self.pixels)
        )


def parse(data):
    if len(data) < SNAPSHOT_SIZE:
        raise SnapshotError("short snapshot: %d bytes" % len(data))

    (magic, version, size, seq, monotonic_ns, enabled, effect, brightness_scale,
     delay, breath_offset, breath_level, patrol_num,
     color_shift) = _HEADER.unpack_from(data, 0)

    if magic != MAGIC:
        raise SnapshotError("bad magic 0x%08x" % magic)
    if size and size > len(data):
        raise SnapshotError("snapshot claims %d bytes, got %d" % (size, len(data)))

    pixels = []
    for index in range(LOGICAL_LEDS):
        offset = HEADER_SIZE + index * PIXEL_SIZE
        pixels.append(tuple(data[offset:offset + PIXEL_SIZE]))

    del version
    return Snapshot(seq, monotonic_ns, enabled, effect, brightness_scale, delay,
                    breath_offset, breath_level, patrol_num, color_shift, pixels)


class ShimSource:
    def __init__(self, device=DEFAULT_DEVICE):
        self.device = device
        self.fd = -1
        self._poll = select.poll()

    def open(self):
        self.fd = os.open(self.device, os.O_RDONLY)
        self._poll.register(self.fd, select.POLLIN)

    def close(self):
        if self.fd >= 0:
            try:
                self._poll.unregister(self.fd)
            except KeyError:
                pass
            try:
                os.close(self.fd)
            finally:
                self.fd = -1

    def read(self):
        """Reads the current snapshot. Returns None after a read error."""
        data = os.read(self.fd, SNAPSHOT_SIZE)
        if not data:
            return None
        return parse(data)

    def wait(self, timeout):
        """Waits up to ``timeout`` seconds for a change of state."""
        return bool(self._poll.poll(timeout * 1000.0))

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *_exc):
        self.close()


def encode(snapshot, seq=0):
    """Builds a raw snapshot buffer, for the simulator and the tests."""
    header = _HEADER.pack(
        MAGIC, 1, SNAPSHOT_SIZE, seq, 0,
        snapshot.enabled, snapshot.effect, snapshot.brightness_scale,
        snapshot.delay, snapshot.breath_offset, snapshot.breath_level,
        snapshot.patrol_num, snapshot.color_shift,
    )
    body = bytearray()
    for pixel in snapshot.pixels[:LOGICAL_LEDS]:
        body.extend(bytes(pixel))
    body.extend(b"\x00" * (LOGICAL_LEDS * PIXEL_SIZE - len(body)))
    return header + bytes(body)


def make_snapshot(effect=EFFECT_MANUAL, color=(255, 255, 255), brightness=255,
                  enabled=1, delay=8, patrol_num=3, color_shift=5):
    """Makes a snapshot for the simulator and the self tests.

    The defaults are the defaults of the module: VALVE_DELAY_DEFAULT,
    VALVE_PATROL_NUM_DEFAULT and VALVE_COLOR_SHIFT_DEFAULT. The simulated
    output thus agrees with a device that nothing wrote to.
    """
    pixels = [(color[0], color[1], color[2], 255) for _ in range(LOGICAL_LEDS)]
    return Snapshot(0, 0, enabled, effect, brightness, delay, 0, 0,
                    patrol_num, color_shift, pixels)
