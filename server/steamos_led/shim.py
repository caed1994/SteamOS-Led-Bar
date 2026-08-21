# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""Reader for the leds-valve-shim character device.

The shim exposes the LED state Steam writes in Game Mode as a packed 100 byte
snapshot. read() is non-blocking and always returns the current state; poll()
reports EPOLLIN whenever the sequence counter advanced.
"""

from __future__ import annotations

import os
import select
import struct

DEFAULT_DEVICE = "/dev/valve-leds-shim"

MAGIC = 0x564C4544  # "VLED"
LOGICAL_LEDS = 17

# Where the module starts its counter, and where it stays until something
# writes. A snapshot still carrying it means Steam has not touched the LEDs
# since the module loaded - which is most of a boot, and the module reports
# "off" all the while.
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

# Effects the real hardware animates on its own. The snapshot only changes when
# Steam writes new settings, so these have to be animated locally.
ANIMATED_EFFECTS = frozenset(
    (EFFECT_RAINBOW, EFFECT_BREATH, EFFECT_PATROL, EFFECT_FACTORY, EFFECT_DEMO)
)


class SnapshotError(ValueError):
    """Raised when the device returns something that is not a valid snapshot."""


class Snapshot:
    """Decoded LED state as written by Steam."""

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
        """The colour Steam picked, used as the tint for animated effects."""
        for red, green, blue, _brightness in self.pixels:
            if red or green or blue:
                return (red, green, blue)
        return (255, 255, 255)

    def key(self):
        """Everything that changes the rendered output, for change detection."""
        return (
            self.enabled, self.effect, self.brightness_scale, self.delay,
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
        """Read the current snapshot; returns None on a transient read error."""
        data = os.read(self.fd, SNAPSHOT_SIZE)
        if not data:
            return None
        return parse(data)

    def wait(self, timeout):
        """Block up to ``timeout`` seconds for a state change."""
        return bool(self._poll.poll(timeout * 1000.0))

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *_exc):
        self.close()


def encode(snapshot, seq=0):
    """Build a raw snapshot buffer. Used by the simulator and the tests."""
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
    """Convenience constructor for simulation and self tests.

    Defaults mirror the module's own (VALVE_DELAY_DEFAULT,
    VALVE_PATROL_NUM_DEFAULT, VALVE_COLOR_SHIFT_DEFAULT), so simulated output
    matches an untouched device.
    """
    pixels = [(color[0], color[1], color[2], 255) for _ in range(LOGICAL_LEDS)]
    return Snapshot(0, 0, enabled, effect, brightness, delay, 0, 0,
                    patrol_num, color_shift, pixels)
