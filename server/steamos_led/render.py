"""Turns a shim snapshot into a frame of physical LED colours.

The Steam Machine animates rainbow/breath/patrol on its own microcontroller,
so the snapshot only carries the parameters, not the per-frame colours. We
reproduce those animations here and stream finished pixels to the ESP, which
keeps the firmware a dumb (and therefore reliable) pixel driver.
"""

from __future__ import annotations

import math

from . import shim

# How long one full animation cycle takes at the hardware's default delay.
# Valve documents neither the unit nor the scale of `delay`, so rather than
# guess a step duration and derive wildly different cycle lengths per effect,
# the cycle times are stated outright and `delay` only scales them relative to
# its default. SPEED in the config file scales them further.
DEFAULT_DELAY_MS = 20.0
RAINBOW_CYCLE = 8.75    # one full trip around the hue circle
BREATH_CYCLE = 4.0      # one inhale plus exhale
PATROL_CYCLE = 6.25     # sweep to the far end and back
DEMO_CYCLE = 8.0        # breathing envelope over the rainbow
# A small delay must not turn an effect into a strobe light.
MIN_CYCLE_SECONDS = 0.8

BREATH_FLOOR = 0.06
PATROL_WIDTH = 2.2
FACTORY_INTERVAL = 1.0

MAPPING_STRETCH = "stretch"
MAPPING_REPEAT = "repeat"
MAPPING_CROP = "crop"
MAPPINGS = (MAPPING_STRETCH, MAPPING_REPEAT, MAPPING_CROP)


def hsv_to_rgb(hue, saturation, value):
    """hue/saturation/value in 0..1, returns floats in 0..255."""
    hue = hue % 1.0
    sector = int(hue * 6.0) % 6
    offset = hue * 6.0 - int(hue * 6.0)
    p = value * (1.0 - saturation)
    q = value * (1.0 - saturation * offset)
    t = value * (1.0 - saturation * (1.0 - offset))
    red, green, blue = (
        (value, t, p), (q, value, p), (p, value, t),
        (p, q, value), (t, p, value), (value, p, q),
    )[sector]
    return red * 255.0, green * 255.0, blue * 255.0


def _cycle(snapshot, nominal, speed_scale):
    """Seconds for one full cycle of an effect, honouring delay and SPEED."""
    delay = snapshot.delay if snapshot.delay else DEFAULT_DELAY_MS
    seconds = nominal * (delay / DEFAULT_DELAY_MS)
    if speed_scale > 0:
        seconds /= speed_scale
    return max(seconds, MIN_CYCLE_SECONDS)


def _static(snapshot):
    """Per-pixel colours exactly as Steam wrote them, including per-LED level."""
    frame = []
    for red, green, blue, brightness in snapshot.pixels:
        level = brightness / 255.0
        frame.append((red * level, green * level, blue * level))
    return frame


def _rainbow(snapshot, elapsed, options):
    phase = elapsed / _cycle(snapshot, RAINBOW_CYCLE, options.speed_scale)
    shift = snapshot.color_shift / 255.0
    frame = []
    for index in range(shim.LOGICAL_LEDS):
        hue = phase + shift + index / float(shim.LOGICAL_LEDS)
        frame.append(hsv_to_rgb(hue, 1.0, 1.0))
    return frame


def _breath(snapshot, elapsed, options):
    period = _cycle(snapshot, BREATH_CYCLE, options.speed_scale)
    phase = elapsed / period + snapshot.breath_offset / 255.0
    level = (1.0 - math.cos(2.0 * math.pi * phase)) * 0.5
    level = BREATH_FLOOR + (1.0 - BREATH_FLOOR) * level
    red, green, blue = snapshot.base_color()
    return [(red * level, green * level, blue * level)] * shim.LOGICAL_LEDS


def _patrol(snapshot, elapsed, options):
    span = shim.LOGICAL_LEDS - 1
    period = _cycle(snapshot, PATROL_CYCLE, options.speed_scale)
    base = (elapsed / period) % 1.0
    # patrol_num is NOT used as a scanner count. Valve documents none of these
    # fields, and its neighbours (breath_offset, breath_level) look like live
    # animation state rather than settings - so patrol_num is most likely the
    # current position of a single dot, not how many there are. One dot is also
    # what "patrol" looks like on the real bar. PATROL_DOTS overrides it.
    scanners = max(1, min(int(options.patrol_dots), 8))
    red, green, blue = snapshot.base_color()

    # Offset each scanner in time rather than in position: shifting the
    # position and wrapping it would teleport a scanner sitting exactly on the
    # far end back to LED 0 for one frame.
    heads = []
    for scanner in range(scanners):
        phase = (base + scanner / float(scanners)) % 1.0
        # Triangle wave: sweep to the far end and back again.
        heads.append(phase * 2.0 * span if phase < 0.5
                     else (2.0 - phase * 2.0) * span)

    frame = []
    for index in range(shim.LOGICAL_LEDS):
        level = 0.0
        for head in heads:
            level = max(level, math.exp(-((index - head) ** 2) / PATROL_WIDTH))
        frame.append((red * level, green * level, blue * level))
    return frame


def _factory(_snapshot, elapsed, _options):
    colours = ((255.0, 0.0, 0.0), (0.0, 255.0, 0.0), (0.0, 0.0, 255.0),
               (255.0, 255.0, 255.0))
    colour = colours[int(elapsed / FACTORY_INTERVAL) % len(colours)]
    return [colour] * shim.LOGICAL_LEDS


def _demo(snapshot, elapsed, options):
    frame = _rainbow(snapshot, elapsed, options)
    period = _cycle(snapshot, DEMO_CYCLE, options.speed_scale)
    level = BREATH_FLOOR + (1.0 - BREATH_FLOOR) * (
        (1.0 - math.cos(2.0 * math.pi * elapsed / period)) * 0.5
    )
    return [(r * level, g * level, b * level) for r, g, b in frame]


_EFFECTS = {
    shim.EFFECT_MANUAL: lambda snap, t, options: _static(snap),
    shim.EFFECT_NORMAL: lambda snap, t, options: _static(snap),
    shim.EFFECT_RAINBOW: _rainbow,
    shim.EFFECT_BREATH: _breath,
    shim.EFFECT_PATROL: _patrol,
    shim.EFFECT_FACTORY: _factory,
    shim.EFFECT_DEMO: _demo,
}


class Renderer:
    """Snapshot + elapsed time -> bytes ready for the wire."""

    def __init__(self, led_count, mapping=MAPPING_STRETCH, reverse=False,
                 max_brightness=255, min_brightness=0, gamma=1.0,
                 speed_scale=1.0, patrol_dots=1):
        if led_count < 1:
            raise ValueError("led_count must be >= 1")
        if mapping not in MAPPINGS:
            raise ValueError("unknown mapping %r" % mapping)
        self.led_count = led_count
        self.mapping = mapping
        self.reverse = reverse
        self.max_brightness = max(0, min(int(max_brightness), 255))
        self.min_brightness = max(0, min(int(min_brightness), 255))
        self.speed_scale = speed_scale
        self.patrol_dots = max(1, min(int(patrol_dots), 8))
        self._gamma_table = self._build_gamma(gamma)

    @staticmethod
    def _build_gamma(gamma):
        if abs(gamma - 1.0) < 1e-6:
            return None
        return [
            int(round(((value / 255.0) ** gamma) * 255.0))
            for value in range(256)
        ]

    def render_logical(self, snapshot, elapsed):
        """The 17 logical LEDs of the Steam Machine bar, floats in 0..255."""
        if not snapshot.enabled or snapshot.effect == shim.EFFECT_OFF:
            return [(0.0, 0.0, 0.0)] * shim.LOGICAL_LEDS
        effect = _EFFECTS.get(snapshot.effect, _EFFECTS[shim.EFFECT_MANUAL])
        return effect(snapshot, elapsed, self)

    def _map_to_strip(self, logical):
        count = self.led_count
        source = len(logical)

        if self.mapping == MAPPING_CROP:
            frame = [logical[index % source] if index < source else (0.0, 0.0, 0.0)
                     for index in range(count)]
        elif self.mapping == MAPPING_REPEAT:
            frame = [logical[index % source] for index in range(count)]
        elif count == 1:
            frame = [logical[0]]
        else:
            # Linear interpolation so a 60 LED strip shows smooth gradients
            # instead of 17 hard steps.
            frame = []
            for index in range(count):
                position = index * (source - 1) / float(count - 1)
                low = int(math.floor(position))
                high = min(low + 1, source - 1)
                blend = position - low
                first, second = logical[low], logical[high]
                frame.append(tuple(
                    first[channel] * (1.0 - blend) + second[channel] * blend
                    for channel in range(3)
                ))

        if self.reverse:
            frame.reverse()
        return frame

    def render(self, snapshot, elapsed):
        """Return the RGB byte payload for the physical strip."""
        frame = self._map_to_strip(self.render_logical(snapshot, elapsed))

        if snapshot.enabled and snapshot.effect != shim.EFFECT_OFF:
            level = max(snapshot.brightness_scale, self.min_brightness)
        else:
            level = 0
        scale = (level / 255.0) * (self.max_brightness / 255.0)

        payload = bytearray()
        for red, green, blue in frame:
            for channel in (red, green, blue):
                value = int(channel * scale + 0.5)
                value = 0 if value < 0 else (255 if value > 255 else value)
                if self._gamma_table is not None:
                    value = self._gamma_table[value]
                payload.append(value)
        return bytes(payload)
