# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""Makes a frame of physical LED colours from a shim snapshot.

The Steam Machine animates the rainbow, breath and patrol effects on its own
microcontroller. The snapshot thus carries only the parameters.

This module makes those animations and sends complete pixels to the ESP. The
firmware stays a simple pixel driver, and it is thus reliable.
"""

from __future__ import annotations

import math

from . import shim

# The cycle lengths at the default delay of the module.
#
# `delay` is not a duration. It is a control from 0 to VALVE_DELAY_RANGE_MAX
# (20), and it starts at VALVE_DELAY_DEFAULT (8). It scales these lengths
# linearly: 0 is the fastest and 20 is 2.5 times slower. SPEED then scales them
# again.
#
# tests/test_shim_abi.py keeps the two constants equal to the module's own.
DELAY_DEFAULT = 8
DELAY_MAX = 20
RAINBOW_CYCLE = 3.5     # one full trip around the hue circle
BREATH_CYCLE = 1.6      # one inhale plus exhale
PATROL_CYCLE = 2.5      # sweep to the far end and back
DEMO_CYCLE = 3.2        # breathing envelope over the rainbow
# A small delay must not make an effect into a strobe light.
MIN_CYCLE_SECONDS = 0.8

BREATH_FLOOR = 0.06
PATROL_WIDTH = 2.2
FACTORY_INTERVAL = 1.0

MAPPING_STRETCH = "stretch"
MAPPING_REPEAT = "repeat"
MAPPING_CROP = "crop"
MAPPINGS = (MAPPING_STRETCH, MAPPING_REPEAT, MAPPING_CROP)


def hsv_to_rgb(hue, saturation, value):
    """Takes hue, saturation and value from 0 to 1. Returns 0 to 255."""
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
    """Returns the seconds of one cycle of an effect, with delay and SPEED.

    A delay of 0 means "as fast as possible" and not "not set". The module
    sets it to DELAY_DEFAULT, so a zero is a value that something wrote.
    MIN_CYCLE_SECONDS keeps the effect slow enough to watch.
    """
    delay = min(snapshot.delay, DELAY_MAX)
    seconds = nominal * (delay / float(DELAY_DEFAULT))
    if speed_scale > 0:
        seconds /= speed_scale
    return max(seconds, MIN_CYCLE_SECONDS)


def _static(snapshot):
    """Returns the pixel colours as Steam wrote them, with the LED levels."""
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
    """Returns a raised cosine over one cycle. It never reaches zero.

    Phase 0 is the dark end. The breath effect, the demo fade and the
    notification bloom all use this function, so a breath looks the same
    everywhere.
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
    # patrol_num is NOT the number of dots. These fields have no documentation,
    # and the fields beside it look like live animation state. It is thus most
    # probably the position of one dot, which is what the real bar shows.
    # PATROL_DOTS gives the number instead.
    scanners = max(1, min(int(options.patrol_dots), 8))
    red, green, blue = snapshot.base_color()

    # Move each dot in time and not in position. A position that goes past the
    # end returns to LED 0 for one frame, and the dot thus jumps.
    heads = []
    for scanner in range(scanners):
        phase = (base + scanner / float(scanners)) % 1.0
        # A triangle wave: to the far end and back again.
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


# What the full bar shows, from cool to hot.
#
# Green, yellow and red is the sequence that each person reads as "worse". A
# mix of these three in RGB follows the same path around the hue circle:
# between two of them only one channel moves. A hue conversion is thus not
# necessary for the same result.
TEMPERATURE_COOL = (0.0, 255.0, 0.0)
TEMPERATURE_WARM = (255.0, 255.0, 0.0)
TEMPERATURE_HOT = (255.0, 0.0, 0.0)

DEFAULT_TEMPERATURE_RANGE = (40.0, 80.0)


def blend_stops(value, stops):
    """Mixes a colour from ((mark, colour), ...), which must be in order.

    Below the first mark and above the last mark, the end colours stay. This
    function does not continue the scale past its own ends. Such a scale
    reports 300 C in ultraviolet.
    """
    if value <= stops[0][0]:
        return stops[0][1]
    for (low_mark, low), (high_mark, high) in zip(stops, stops[1:]):
        if value <= high_mark:
            span = high_mark - low_mark
            # Each caller validates its marks, but a program can also call
            # this function directly. A division by zero here is a stop in
            # the render loop and not a message at the start.
            blend = 0.0 if span <= 0 else (value - low_mark) / span
            return tuple(low[channel] + (high[channel] - low[channel]) * blend
                         for channel in range(3))
    return stops[-1][1]


def temperature_stops(low, high):
    """Returns (mark, colour) from the two marks: green low, red high.

    Yellow is at the middle and is not a third setting. The middle is the one
    position that needs no explanation. With the default 40 and 80 it is at
    60, and that is the temperature of a machine under load.
    """
    return ((low, TEMPERATURE_COOL),
            ((low + high) / 2.0, TEMPERATURE_WARM),
            (high, TEMPERATURE_HOT))


def temperature_colour(celsius, low=DEFAULT_TEMPERATURE_RANGE[0],
                       high=DEFAULT_TEMPERATURE_RANGE[1]):
    """Returns the colour for a temperature: a mix of the stops."""
    return blend_stops(celsius, temperature_stops(low, high))


def _temperature(snapshot, elapsed, options):
    """Draws the full bar in one colour, from green when cool to red when hot.

    This is not a gauge that fills. A bar that is part dark says two things at
    the same time, its colour and its length, and only one of them is the
    answer. With each LED lit, the colour is the full message.
    """
    celsius = options.temperature.celsius()
    if celsius is None:
        # The rainbow is better than a dark strip. A dark strip looks like a
        # service that stopped. The log gives the reason.
        return _rainbow(snapshot, elapsed, options)

    low, high = options.temperature_range
    return [temperature_colour(celsius, low, high)] * shim.LOGICAL_LEDS


# -- the load gauge --------------------------------------------------------
#
# This gauge uses length and not colour. The load has a real zero and a real
# maximum, so the length of the bar *is* the reading. The temperature has
# neither and uses colour instead.
#
# The colour says which chip, from LOAD_CPU_COLOR and LOAD_GPU_COLOR. The
# defaults are amber and blue, which are almost the maximum distance apart.
# Two colours near each other read as one irregular bar and not as two, and
# the panel says so below the rows.
LOAD_CPU_COLOUR = (255.0, 110.0, 0.0)
LOAD_GPU_COLOUR = (26.0, 159.0, 255.0)


def _as_colour(value, fallback):
    """Returns three floats from 0 to 255, or the default colour.

    It takes a value that a caller parsed and does not parse one itself. The
    form of a colour is the work of notify.parse_color. notify imports from
    this module, so an import in the other direction is a circle.
    """
    if value is None:
        return fallback
    return tuple(max(0.0, min(float(channel), 255.0)) for channel in value)

# The innermost LED of each half never goes fully dark. Without that, an
# idle machine looks like a strip that a person switched off.
LOAD_FLOOR = 0.12


def load_levels(fraction, length):
    """Returns the brightness of each of `length` LEDs, the innermost first.

    The bar uses fractions of an LED. At one third of a half of three LEDs,
    the second LED is half lit. The bar thus does not step one full LED at a
    time. On seventeen LEDs, a bar of full LEDs shows eighths only.
    """
    if length < 1:
        return []
    filled = max(0.0, min(float(fraction), 1.0)) * length
    levels = []
    for index in range(length):
        levels.append(max(0.0, min(filled - index, 1.0)))
    levels[0] = max(levels[0], LOAD_FLOOR)
    return levels


def _load(snapshot, elapsed, options):
    """Draws two bars from the centre, one chip in each direction.

    From the centre and not from one end: the two readings are equal, and two
    bars end to end put one of them at the far side of the strip.

    LOAD_SWAP moves the reading and its colour together, or the colours would
    no longer say which chip is which.

    This function always has a reading. _substitute gives the slot back to the
    rainbow before it runs.
    """
    cpu, gpu = options.reading()
    # A machine whose driver publishes no GPU counter shows the CPU on both
    # halves. One reading in two symmetrical halves still reads as one
    # reading.
    if gpu is None:
        gpu = cpu
    elif cpu is None:
        cpu = gpu

    inner = ((gpu, options.load_gpu_colour, cpu, options.load_cpu_colour)
             if options.load_swap
             else (cpu, options.load_cpu_colour, gpu, options.load_gpu_colour))
    near, near_colour, far, far_colour = inner

    half = shim.LOGICAL_LEDS // 2
    # The centre LED belongs to neither half and stays dark. That is what
    # makes the two sides read as two. The alarm shape uses the same method.
    gap = shim.LOGICAL_LEDS % 2
    frame = [(0.0, 0.0, 0.0)] * shim.LOGICAL_LEDS
    for index, level in enumerate(load_levels(near, half)):
        frame[half - 1 - index] = tuple(channel * level
                                        for channel in near_colour)
    for index, level in enumerate(load_levels(far, half)):
        frame[half + gap + index] = tuple(channel * level
                                          for channel in far_colour)
    return frame


# -- fire ------------------------------------------------------------------

# From the embers at the low end to the white tips at the high end.
#
# This is not a hue sweep. A fire loses saturation as it becomes hotter. It
# does not change its hue. The high end of the scale is thus almost white, and
# a rainbow never looks like a flame.
FIRE_STOPS = ((0.00, (40.0, 0.0, 0.0)),
              (0.35, (170.0, 20.0, 0.0)),
              (0.65, (255.0, 90.0, 0.0)),
              (0.88, (255.0, 170.0, 20.0)),
              (1.00, (255.0, 230.0, 150.0)))

FIRE_CYCLE = 4.0        # one trip through the slowest of the waves

# Three waves, deliberately with no whole ratio between them. Their sum thus
# never repeats, and the flicker has no rhythm that a person hears. Each pair
# is (the number of humps on the strip, the speed of that wave).
FIRE_WAVES = ((0.9, 1.00), (2.3, -1.71), (5.7, 2.53))


def _fire(snapshot, elapsed, options):
    """Draws a flame as heat that moves, and not as pixels that flicker.

    A random brightness for each LED looks like a fault and not like a fire.
    What a person reads as a flame is heat that moves along the strip.

    This function thus adds some waves that move and gives the sum a colour.
    It is a function of the time and not a random value, so a lost frame
    causes no visible jump.
    """
    period = _cycle(snapshot, FIRE_CYCLE, options.speed_scale)
    phase = elapsed / period
    span = float(shim.LOGICAL_LEDS)

    frame = []
    for index in range(shim.LOGICAL_LEDS):
        heat = 0.0
        for humps, speed in FIRE_WAVES:
            heat += math.sin(2.0 * math.pi
                             * (index / span * humps + phase * speed))
        # The three waves give a sum from -3 to 3. The centre is high, so the
        # strip mostly burns and goes to the embers only sometimes.
        heat = 0.58 + heat / 6.4
        frame.append(blend_stops(max(0.0, min(heat, 1.0)), FIRE_STOPS))
    return frame


# -- aurora ----------------------------------------------------------------

# The hue range of the real northern lights: green through cyan to violet, and
# never the warm half of the circle. That limit is the effect. A rainbow
# already exists.
#
# The two waves below sum from -2 to 2, so the hue stays within AURORA_SPREAD
# of AURORA_HUE. 0.33 is pure green and 0.71 is violet-blue.
AURORA_HUE = 0.52
AURORA_SPREAD = 0.19

AURORA_CYCLE = 9.0          # slow: this is the calm one

# Two waves for the hue and one for the brightness, each at a different speed.
# The colour bands and the bright bands thus move apart and not as one block.
AURORA_HUE_WAVES = ((0.7, 1.0), (1.9, -0.43))
AURORA_LEVEL_WAVE = (1.3, 0.61)
AURORA_FLOOR = 0.35         # the curtain thins, it does not go out


def _aurora(snapshot, elapsed, options):
    """Draws slow green and violet curtains."""
    period = _cycle(snapshot, AURORA_CYCLE, options.speed_scale)
    phase = elapsed / period
    span = float(shim.LOGICAL_LEDS)
    # Steam's colour picker still moves it, the same way it shifts the
    # rainbow: the effect keeps its character, you choose where it sits.
    shift = snapshot.color_shift / 255.0

    frame = []
    for index in range(shim.LOGICAL_LEDS):
        drift = 0.0
        for humps, speed in AURORA_HUE_WAVES:
            drift += math.sin(2.0 * math.pi
                              * (index / span * humps + phase * speed))
        humps, speed = AURORA_LEVEL_WAVE
        swell = math.sin(2.0 * math.pi
                         * (index / span * humps + phase * speed))
        level = AURORA_FLOOR + (1.0 - AURORA_FLOOR) * (0.5 + swell * 0.5)
        frame.append(hsv_to_rgb(AURORA_HUE + shift + AURORA_SPREAD * drift / 2.0,
                                0.9, level))
    return frame


# What the rainbow entry shows.
#
# SteamOS does not permit new entries in its LED menu: the entries are in the
# client. A new effect must thus replace an entry that the menu has, and the
# rainbow is the entry that people give up.
#
# That one slot does not belong to one feature. It holds the effect that a
# person selects. SHOWS_RAINBOW leaves Steam's own effect there.
SHOWS_RAINBOW = "rainbow"
SHOWS_TEMPERATURE = "temperature"
SHOWS_LOAD = "load"
SHOWS_FIRE = "fire"
SHOWS_AURORA = "aurora"

# What the brightness and the speed of a scene reach.
#
# This table has a name because the two values are not the same kind of thing
# for each effect. For most effects, the brightness is how bright the effect
# looks. For a gauge, the brightness is half of the reading.
TAKES_BRIGHTNESS = "brightness"
TAKES_SPEED = "speed"
TAKES_BOTH = frozenset((TAKES_BRIGHTNESS, TAKES_SPEED))

# The load gauge uses neither. The minimum brightness of the innermost LED is
# what says "idle" and not "off", so a dim gauge changes the reading. Its
# movement uses the clock and not the delay field, for the same reason.
#
# MAX_BRIGHTNESS still applies. That setting limits the current of the strip.
TAKES_NOTHING = frozenset()

# The temperature gauge uses the brightness and not the speed. It is the full
# bar in one colour, so there is no cycle for a multiplier to scale and
# is_animated calls it static. The colour is the whole reading, so a dim gauge
# does not change its meaning. With no sensor it gives the slot to the
# rainbow.
# That is an error path with a log line, and not a speed setting.
TAKES_LIGHT = frozenset((TAKES_BRIGHTNESS,))

# For each value: the renderer that replaces the rainbow, the Renderer
# attribute that must be present for the value to have a meaning, and which of
# the settings of the bar reach it. The attribute applies to the two values
# that read hardware.
#
# config validates against this table. A new effect thus needs one new entry.
_SUBSTITUTES = {
    SHOWS_TEMPERATURE: (_temperature, "temperature", TAKES_LIGHT),
    SHOWS_LOAD: (_load, "load", TAKES_NOTHING),
    SHOWS_FIRE: (_fire, None, TAKES_BOTH),
    SHOWS_AURORA: (_aurora, None, TAKES_BOTH),
}
RAINBOW_CHOICES = (SHOWS_RAINBOW,) + tuple(_SUBSTITUTES)


def rainbow_takes(rainbow_shows):
    """Returns which settings of the bar reach the effect in the slot.

    Each program that describes a setting to a person asks this, so that a
    control which changes nothing is not offered as one that does.

    Steam's own rainbow takes both, which is also the answer for a name this
    function does not know.
    """
    return _SUBSTITUTES.get(rainbow_shows, (None, None, TAKES_BOTH))[2]


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
    """Makes the bytes for the wire from a snapshot and the elapsed time."""

    def __init__(self, led_count, mapping=MAPPING_STRETCH, reverse=False,
                 max_brightness=255, min_brightness=0, gamma=1.0,
                 speed_scale=1.0, patrol_dots=1, temperature=None,
                 temperature_range=DEFAULT_TEMPERATURE_RANGE, load=None,
                 rainbow_shows=None, load_cpu_colour=None,
                 load_gpu_colour=None, load_swap=False):
        if led_count < 1:
            raise ValueError("led_count must be >= 1")
        if mapping not in MAPPINGS:
            raise ValueError("unknown mapping %r" % mapping)
        if rainbow_shows is None:
            # A caller that gives a sensor and nothing else means "show the
            # gauge". That reading is older than the list of choices.
            rainbow_shows = (SHOWS_TEMPERATURE if temperature is not None
                             else SHOWS_LOAD if load is not None
                             else SHOWS_RAINBOW)
        if rainbow_shows not in RAINBOW_CHOICES:
            raise ValueError("unknown rainbow effect %r" % rainbow_shows)
        self.led_count = led_count
        self.mapping = mapping
        self.reverse = reverse
        self.max_brightness = max(0, min(int(max_brightness), 255))
        self.min_brightness = max(0, min(int(min_brightness), 255))
        self.speed_scale = speed_scale
        self.patrol_dots = max(1, min(int(patrol_dots), 8))
        # An object with .celsius(), or None if nothing reads a sensor.
        self.temperature = temperature
        self.temperature_range = temperature_range
        # An object with .fractions(), or None for the same reason.
        self.load = load
        # The chip that each half of the gauge shows. The caller parsed
        # these values. See _as_colour.
        self.load_cpu_colour = _as_colour(load_cpu_colour, LOAD_CPU_COLOUR)
        self.load_gpu_colour = _as_colour(load_gpu_colour, LOAD_GPU_COLOUR)
        # Which chip is on which side. The reading and the colour move
        # together.
        self.load_swap = bool(load_swap)
        self.rainbow_shows = rainbow_shows
        self._gamma_table = self._build_gamma(gamma)
        self._stretch = {}
        # The load of this frame, read one time. See reading().
        self._reading = None

    @staticmethod
    def _build_gamma(gamma):
        """Returns a table of 256 entries. It is the identity table with no gamma."""
        if abs(gamma - 1.0) < 1e-6:
            return list(range(256))
        return [
            int(round(((value / 255.0) ** gamma) * 255.0))
            for value in range(256)
        ]

    def _stretch_weights(self, source):
        """Returns (low, high, blend) for each LED. It is constant for one strip."""
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

    def reading(self, fresh=False):
        """Returns the load of this frame. It reads it one time for each frame.

        fractions() moves the value at each call, so two calls in one frame
        move the bar at twice the rate. Two callers need the answer: the one
        that decides whether there is a gauge, and the one that decides which
        settings reach it.
        """
        if fresh:
            self._reading = (None if self.load is None
                             else self.load.fractions())
        return self._reading

    def shown_by(self, shows):
        """Returns the effect that a `shows` argument asks for.

        With no argument, the setting. Each entry point takes `shows`, so the
        effect is not a property of the renderer.

        Game Mode gives no argument and uses the rainbow slot. The desktop has
        no slot: it selects a scene and passes it down for each frame.
        """
        return self.rainbow_shows if shows is None else shows

    def _substitute(self, snapshot, shows=None):
        """Returns the renderer that replaces the rainbow here, or None.

        None also covers absent hardware and a load gauge with nothing to
        read, which happens in the first frames after a start. Steam's own
        rainbow says "not this effect" better than a dark strip does.

        The decision is here and not in the gauge, so each other part agrees
        about what is on the bar: a rainbow in that slot must take the
        brightness and the speed as a rainbow does.
        """
        if snapshot.effect != shim.EFFECT_RAINBOW:
            return None
        effect, needs, _takes = _SUBSTITUTES.get(self.shown_by(shows),
                                                 (None, None, TAKES_BOTH))
        if effect is None or (needs and getattr(self, needs) is None):
            return None
        if effect is _load and self.reading() is None:
            return None
        return effect

    def is_animated(self, snapshot, shows=None):
        """Returns whether this scene changes from frame to frame.

        The temperature gauge does not: it is redrawn when the sensor changes,
        which is much slower than a frame.

        The load gauge does. Its value moves towards each new reading rather
        than step onto it, and at the idle rate that movement becomes four
        steps each second.
        """
        effect = self._substitute(snapshot, shows)
        if effect is _temperature:
            return self.temperature.celsius() is None
        if effect is not None:
            return True
        return snapshot.is_animated

    def render_logical(self, snapshot, elapsed, shows=None):
        """Returns the 17 logical LEDs of the bar, as floats from 0 to 255."""
        if not snapshot.enabled or snapshot.effect == shim.EFFECT_OFF:
            return [(0.0, 0.0, 0.0)] * shim.LOGICAL_LEDS
        # Before any caller asks what this draws, and one time for the frame.
        self.reading(fresh=True)
        effect = (self._substitute(snapshot, shows)
                  or _EFFECTS.get(snapshot.effect, _EFFECTS[shim.EFFECT_MANUAL]))
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
            # Interpolate. A strip of 60 LEDs thus gets a gradient and not 17
            # steps.
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

    def render(self, snapshot, elapsed, shows=None):
        """Returns the RGB byte payload for the physical strip.

        It renders the frame first, because the effect decides the brightness
        of the output. A gauge whose brightness is its reading does not take
        the setting that dims each other effect. See rainbow_takes.
        """
        frame = self._map_to_strip(
            self.render_logical(snapshot, elapsed, shows))

        if not snapshot.enabled or snapshot.effect == shim.EFFECT_OFF:
            level = 0
        elif (self._substitute(snapshot, shows) is not None
                and TAKES_BRIGHTNESS
                not in rainbow_takes(self.shown_by(shows))):
            level = 255
        else:
            level = max(snapshot.brightness_scale, self.min_brightness)
        scale = (level / 255.0) * (self.max_brightness / 255.0)

        table = self._gamma_table
        payload = bytearray()
        for pixel in frame:
            for channel in pixel:
                value = int(channel * scale + 0.5)
                value = 0 if value < 0 else (255 if value > 255 else value)
                payload.append(table[value])
        return bytes(payload)
