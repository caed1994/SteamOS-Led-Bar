"""Turns a shim snapshot into a frame of physical LED colours.

The Steam Machine animates rainbow/breath/patrol on its own microcontroller, so
the snapshot carries only the parameters. Those animations are reproduced here
and streamed to the ESP as finished pixels, which keeps the firmware a dumb
(and therefore reliable) pixel driver.
"""

from __future__ import annotations

import math

from . import shim

# Cycle lengths at the module's default delay. `delay` is not a duration but a
# slider from 0 to VALVE_DELAY_RANGE_MAX(20) starting at VALVE_DELAY_DEFAULT(8),
# and it scales these linearly (0 fastest, 20 = 2.5x slower); SPEED scales them
# further. tests/test_shim_abi.py keeps the two constants tied to the module.
DELAY_DEFAULT = 8
DELAY_MAX = 20
RAINBOW_CYCLE = 3.5     # one full trip around the hue circle
BREATH_CYCLE = 1.6      # one inhale plus exhale
PATROL_CYCLE = 2.5      # sweep to the far end and back
DEMO_CYCLE = 3.2        # breathing envelope over the rainbow
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
    """Seconds for one full cycle of an effect, honouring delay and SPEED.

    delay 0 means "as fast as possible", not "unset": the module initialises it
    to DELAY_DEFAULT, so a zero was written. MIN_CYCLE_SECONDS keeps it
    watchable.
    """
    delay = min(snapshot.delay, DELAY_MAX)
    seconds = nominal * (delay / float(DELAY_DEFAULT))
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


def breath_envelope(phase, floor):
    """A raised cosine over one cycle, lifted so it never reaches zero.

    Phase 0 is the dark end. Shared by the breath effect, the demo fade and the
    notification bloom so "breathing" looks the same everywhere.
    """
    swell = (1.0 - math.cos(2.0 * math.pi * phase)) * 0.5
    return floor + (1.0 - floor) * swell


def _breath(snapshot, elapsed, options):
    period = _cycle(snapshot, BREATH_CYCLE, options.speed_scale)
    phase = elapsed / period + snapshot.breath_offset / 255.0
    level = breath_envelope(phase, BREATH_FLOOR)
    red, green, blue = snapshot.base_color()
    return [(red * level, green * level, blue * level)] * shim.LOGICAL_LEDS


def _patrol(snapshot, elapsed, options):
    span = shim.LOGICAL_LEDS - 1
    period = _cycle(snapshot, PATROL_CYCLE, options.speed_scale)
    base = (elapsed / period) % 1.0
    # patrol_num is NOT a scanner count: these fields are undocumented and its
    # neighbours look like live animation state, so it is most likely one dot's
    # position - which is what the real bar shows. PATROL_DOTS decides instead.
    scanners = max(1, min(int(options.patrol_dots), 8))
    red, green, blue = snapshot.base_color()

    # Offset each scanner in time, not in position: a wrapped position would
    # teleport a scanner sitting on the far end back to LED 0 for one frame.
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
    level = breath_envelope(elapsed / period, BREATH_FLOOR)
    return [(r * level, g * level, b * level) for r, g, b in frame]


# Green to red around the hue circle, which passes through yellow and orange -
# the sequence everybody already reads as "getting worse".
GREEN_HUE = 1.0 / 3.0


def _temperature(snapshot, elapsed, options):
    """A gauge: fills as the machine warms, greening to red as it fills.

    Below the cold mark nothing is lit - which is what "starts filling at 40"
    means, and the least distracting thing a cool machine can do.
    """
    celsius = options.temperature.celsius()
    if celsius is None:
        # Falling back to the rainbow beats a dark strip, which would look
        # like the service had died. The log says what happened.
        return _rainbow(snapshot, elapsed, options)

    low, high = options.temperature_range
    fraction = 0.0 if high <= low else (celsius - low) / (high - low)
    fraction = max(0.0, min(fraction, 1.0))

    red, green, blue = hsv_to_rgb(GREEN_HUE * (1.0 - fraction), 1.0, 1.0)

    # Fractional, so the leading LED fades in rather than the bar stepping a
    # notch at a time - at 17 LEDs a step is nearly three degrees.
    lit = fraction * shim.LOGICAL_LEDS
    last = shim.LOGICAL_LEDS - 1
    frame = []
    for index in range(shim.LOGICAL_LEDS):
        # Grows from the far end, the way the other effects run. REVERSE
        # still flips the whole strip on top of this.
        level = max(0.0, min(lit - (last - index), 1.0))
        frame.append((red * level, green * level, blue * level))
    return frame


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
                 speed_scale=1.0, patrol_dots=1, temperature=None,
                 temperature_range=(40.0, 85.0)):
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
        # Something with .celsius(), or None to leave the rainbow alone.
        self.temperature = temperature
        self.temperature_range = temperature_range
        self._gamma_table = self._build_gamma(gamma)
        self._stretch = {}

    @staticmethod
    def _build_gamma(gamma):
        """A 256 entry lookup; the identity table when gamma is off."""
        if abs(gamma - 1.0) < 1e-6:
            return list(range(256))
        return [
            int(round(((value / 255.0) ** gamma) * 255.0))
            for value in range(256)
        ]

    def _stretch_weights(self, source):
        """(low, high, blend) per physical LED - fixed for a given strip."""
        weights = self._stretch.get(source)
        if weights is None:
            span = source - 1
            weights = []
            for index in range(self.led_count):
                position = index * span / float(self.led_count - 1)
                low = int(position)
                weights.append((low, min(low + 1, span), position - low))
            self._stretch[source] = weights
        return weights

    def _gauge_active(self, snapshot):
        """Whether the gauge has taken the rainbow's place for this snapshot.

        Steam's menu cannot be extended - the entries are built into the client
        - so the gauge has to take over one it already offers, and the rainbow
        is the one people are happy to give up.
        """
        return (self.temperature is not None
                and snapshot.effect == shim.EFFECT_RAINBOW)

    def is_animated(self, snapshot):
        """Whether this scene changes from frame to frame.

        The gauge does not: it redraws when the sensor moves, which is orders
        of magnitude slower than a frame, so driving it at the full rate sends
        the same bytes sixty times a second. Without a sensor it falls back to
        the rainbow, which does animate.
        """
        if self._gauge_active(snapshot):
            return self.temperature.celsius() is None
        return snapshot.is_animated

    def render_logical(self, snapshot, elapsed):
        """The 17 logical LEDs of the Steam Machine bar, floats in 0..255."""
        if not snapshot.enabled or snapshot.effect == shim.EFFECT_OFF:
            return [(0.0, 0.0, 0.0)] * shim.LOGICAL_LEDS
        effect = _EFFECTS.get(snapshot.effect, _EFFECTS[shim.EFFECT_MANUAL])
        if self._gauge_active(snapshot):
            effect = _temperature
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
            # Interpolate: a 60 LED strip gets a gradient, not 17 hard steps.
            frame = []
            for low, high, blend in self._stretch_weights(source):
                first, second = logical[low], logical[high]
                inverse = 1.0 - blend
                frame.append((first[0] * inverse + second[0] * blend,
                              first[1] * inverse + second[1] * blend,
                              first[2] * inverse + second[2] * blend))

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

        table = self._gamma_table
        payload = bytearray()
        for pixel in frame:
            for channel in pixel:
                value = int(channel * scale + 0.5)
                value = 0 if value < 0 else (255 if value > 255 else value)
                payload.append(table[value])
        return bytes(payload)
