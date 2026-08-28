# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

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


# What the whole bar shows, cool to hot. Green, yellow, red is the sequence
# everybody already reads as "getting worse", and mixing these three in RGB
# walks the same path around the hue circle: between any two of them only one
# channel moves, so no hue conversion is needed to get the same result.
TEMPERATURE_COOL = (0.0, 255.0, 0.0)
TEMPERATURE_WARM = (255.0, 255.0, 0.0)
TEMPERATURE_HOT = (255.0, 0.0, 0.0)

DEFAULT_TEMPERATURE_RANGE = (40.0, 80.0)


def blend_stops(value, stops):
    """Mix a colour out of ((mark, colour), ...), which must be in order.

    Below the first mark and above the last the end colours are held rather
    than extrapolated: a scale that keeps going past its own ends is one that
    reports 300 C in ultraviolet.
    """
    if value <= stops[0][0]:
        return stops[0][1]
    for (low_mark, low), (high_mark, high) in zip(stops, stops[1:]):
        if value <= high_mark:
            span = high_mark - low_mark
            # Callers validate their marks, but this is also used directly -
            # and dividing by nothing here would be a crash in the render
            # loop rather than a message at startup.
            blend = 0.0 if span <= 0 else (value - low_mark) / span
            return tuple(low[channel] + (high[channel] - low[channel]) * blend
                         for channel in range(3))
    return stops[-1][1]


def temperature_stops(low, high):
    """(mark, colour) from the two marks: green at one end, red at the other.

    Yellow sits halfway between rather than being a third setting. It is the
    only placement that needs no explaining - and with the shipped 40 and 80
    it lands on 60, which is where a machine under load actually sits.
    """
    return ((low, TEMPERATURE_COOL),
            ((low + high) / 2.0, TEMPERATURE_WARM),
            (high, TEMPERATURE_HOT))


def temperature_colour(celsius, low=DEFAULT_TEMPERATURE_RANGE[0],
                       high=DEFAULT_TEMPERATURE_RANGE[1]):
    """The colour for a temperature: the stops, mixed."""
    return blend_stops(celsius, temperature_stops(low, high))


def _temperature(snapshot, elapsed, options):
    """The whole bar in one colour, from green when cool to red when hot.

    Not a filling gauge: a bar that is part dark says two things at once, its
    colour and its length, and only one of them was ever the answer. Lit end
    to end, the colour is the whole message.
    """
    celsius = options.temperature.celsius()
    if celsius is None:
        # Falling back to the rainbow beats a dark strip, which would look
        # like the service had died. The log says what happened.
        return _rainbow(snapshot, elapsed, options)

    low, high = options.temperature_range
    return [temperature_colour(celsius, low, high)] * shim.LOGICAL_LEDS


# -- the load gauge --------------------------------------------------------
#
# Length, not colour, and for once that is the right way round: load has a
# real zero and a real full, so how far the bar has come *is* the reading.
# Temperature has neither, which is why that one uses colour instead.
#
# The colour says which chip, and both are settings - LOAD_CPU_COLOR and
# LOAD_GPU_COLOR. These two are what they ship as: amber and blue sit about as
# far apart as two colours on this strip can, which is what stops the gauge
# reading as one bar that happens to be uneven rather than as two.
#
# Worth knowing when changing them, and the reason the panel says so under the
# rows: what makes this gauge readable is the two halves being told apart at a
# glance. Two colours near each other on the wheel still work as a gauge, they
# just stop working as *two*.
LOAD_CPU_COLOUR = (255.0, 110.0, 0.0)
LOAD_GPU_COLOUR = (26.0, 159.0, 255.0)


def _as_colour(value, fallback):
    """Three floats in 0..255, or the shipped colour when there is nothing.

    Takes what a caller has already parsed rather than parsing itself: the
    spelling of a colour is notify.parse_color's business, and notify imports
    from here, so reaching back the other way is a circle.
    """
    if value is None:
        return fallback
    return tuple(max(0.0, min(float(channel), 255.0)) for channel in value)

# The innermost LED of each half never goes fully dark, or an idle machine
# looks like a strip somebody switched off.
LOAD_FLOOR = 0.12


def load_levels(fraction, length):
    """How brightly each of `length` LEDs is lit at this load, innermost first.

    The bar is fractional: at a third of the way up a three LED half, the
    second LED is half lit rather than the bar jumping a whole LED at a time.
    On seventeen LEDs a whole-LED bar could only ever show eighths.
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
    """Two bars growing out of the middle: one chip each way.

    Out of the centre rather than from one end, because the two readings are
    peers - a bar for each, meeting in the middle, says that. Stacking them
    end to end would put one of them on the far side of the strip, which is
    where you look last.

    Which chip is on which side is LOAD_SWAP. The reading and the colour move
    together, so swapping puts the CPU's bar *and* its colour on the right -
    a swap that moved only one of the two would be a gauge whose colours had
    stopped saying which chip is which.

    Only ever reached with a reading in hand: a gauge with nothing to read is
    not this effect at all, and _substitute hands the slot back to the
    rainbow before it gets here.
    """
    cpu, gpu = options.reading()
    # A machine whose driver publishes no GPU counter shows the CPU on both
    # halves. One reading drawn symmetrically still reads as one reading.
    if gpu is None:
        gpu = cpu
    elif cpu is None:
        cpu = gpu

    inner = ((gpu, options.load_gpu_colour, cpu, options.load_cpu_colour)
             if options.load_swap
             else (cpu, options.load_cpu_colour, gpu, options.load_gpu_colour))
    near, near_colour, far, far_colour = inner

    half = shim.LOGICAL_LEDS // 2
    # The odd middle LED belongs to neither and stays dark, which is what
    # makes the two sides read as two - the same trick the alarm shape uses.
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

# Embers at the bottom through to the white-hot tips. Not a hue sweep: a fire
# desaturates as it gets hotter rather than changing colour, which is why the
# top of the scale is nearly white and a rainbow never looks like flame.
FIRE_STOPS = ((0.00, (40.0, 0.0, 0.0)),
              (0.35, (170.0, 20.0, 0.0)),
              (0.65, (255.0, 90.0, 0.0)),
              (0.88, (255.0, 170.0, 20.0)),
              (1.00, (255.0, 230.0, 150.0)))

FIRE_CYCLE = 4.0        # one trip through the slowest of the waves

# Three waves, deliberately not in any whole ratio to each other: their sum
# never repeats, so the flicker has no beat you can catch. The pairs are
# (how many humps across the strip, how fast that wave drifts).
FIRE_WAVES = ((0.9, 1.00), (2.3, -1.71), (5.7, 2.53))


def _fire(snapshot, elapsed, options):
    """Flame, drawn as drifting heat rather than as flickering pixels.

    Random per-LED brightness looks like a fault, not a fire. What reads as
    flame is heat that moves along the strip, so this sums a few travelling
    waves and colours the result - and being a function of time rather than a
    random draw, it also survives a dropped frame without a visible jump.
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
        # Three waves land in -3..3; centre it high so the strip is mostly
        # burning and only occasionally dips to embers.
        heat = 0.58 + heat / 6.4
        frame.append(blend_stops(max(0.0, min(heat, 1.0)), FIRE_STOPS))
    return frame


# -- aurora ----------------------------------------------------------------

# The hue window the northern lights actually occupy: green through cyan into
# violet, never the warm half of the circle. That restraint is the whole
# effect - a rainbow already exists, and it is the one people turn off.
# The two waves below sum to -2..2, so the hue lands within AURORA_SPREAD of
# AURORA_HUE: 0.33 is pure green and 0.71 is violet-blue, and staying inside
# that is what keeps it from wandering into the yellows and becoming a slow
# rainbow.
AURORA_HUE = 0.52
AURORA_SPREAD = 0.19

AURORA_CYCLE = 9.0          # slow: this is the calm one

# Two waves for the hue and one for the brightness, all at different speeds,
# so the colour bands and the bright bands drift apart instead of moving as
# one lit block.
AURORA_HUE_WAVES = ((0.7, 1.0), (1.9, -0.43))
AURORA_LEVEL_WAVE = (1.3, 0.61)
AURORA_FLOOR = 0.35         # the curtain thins, it does not go out


def _aurora(snapshot, elapsed, options):
    """Slow curtains of green and violet - a rainbow that learned patience."""
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


# What the rainbow entry shows. Steam's LED menu cannot be extended - the
# entries are built into the client - so anything new has to take over one it
# already offers, and the rainbow is the one people are happy to give up.
# Rather than spending that single slot on one feature, it holds whichever of
# these is chosen; SHOWS_RAINBOW leaves Steam's own effect alone.
SHOWS_RAINBOW = "rainbow"
SHOWS_TEMPERATURE = "temperature"
SHOWS_LOAD = "load"
SHOWS_FIRE = "fire"
SHOWS_AURORA = "aurora"

# What a scene's brightness and speed can reach. Named because the two of
# them are not the same kind of thing to every effect: to most, brightness is
# how bright it looks, and to a gauge it is half of what it is saying.
TAKES_BRIGHTNESS = "brightness"
TAKES_SPEED = "speed"
TAKES_BOTH = frozenset((TAKES_BRIGHTNESS, TAKES_SPEED))

# The load gauge answers to neither. It fills each half of the bar to say how
# busy that chip is, and the floor under the innermost LED is what says "idle"
# rather than "off" - dimming that changes the reading rather than the look.
# Its glide runs on the clock instead of on the delay field, for the same
# reason: a gauge at half speed is a gauge showing where the load was.
#
# MAX_BRIGHTNESS still applies to it. That one is about how much current the
# strip may draw, which is not a matter of taste.
TAKES_NOTHING = frozenset()

# The temperature gauge answers to brightness and not to speed. It is the whole
# bar in one colour, redrawn when the sensor moves - there is no cycle for a
# multiplier to scale, which is why is_animated calls it still. Dimming it is
# a dimmer bar of the same colour, and the colour is the whole of the reading,
# so unlike the load gauge that one does not change what it says.
#
# With no sensor it falls back to the rainbow, which does move. That is an
# error path with a log line under it, not a speed setting.
TAKES_LIGHT = frozenset((TAKES_BRIGHTNESS,))

# Which renderer each choice puts in the rainbow's place; the Renderer
# attribute that has to be there for the choice to mean anything, for the two
# that read hardware; and which of the bar's settings reach it. config
# validates against this table, so a new entry is all a further effect needs.
_SUBSTITUTES = {
    SHOWS_TEMPERATURE: (_temperature, "temperature", TAKES_LIGHT),
    SHOWS_LOAD: (_load, "load", TAKES_NOTHING),
    SHOWS_FIRE: (_fire, None, TAKES_BOTH),
    SHOWS_AURORA: (_aurora, None, TAKES_BOTH),
}
RAINBOW_CHOICES = (SHOWS_RAINBOW,) + tuple(_SUBSTITUTES)


def rainbow_takes(rainbow_shows):
    """Which of the bar's settings reach whatever holds the rainbow slot.

    Asked by anything that describes a setting to somebody - a report naming
    a brightness that the thing on the bar ignores is how you come to move a
    slider and see nothing happen. Steam's own rainbow takes both, which is
    also the answer for a name this does not know.
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
    """Snapshot + elapsed time -> bytes ready for the wire."""

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
            # Handing over a sensor and nothing else has always meant "show
            # the gauge", and that reading predates there being a choice.
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
        # Something with .celsius(), or None when nothing reads a sensor.
        self.temperature = temperature
        self.temperature_range = temperature_range
        # Something with .fractions(), likewise.
        self.load = load
        # Which chip each half of the gauge stands for. Already parsed by
        # whoever built this - see _as_colour.
        self.load_cpu_colour = _as_colour(load_cpu_colour, LOAD_CPU_COLOUR)
        self.load_gpu_colour = _as_colour(load_gpu_colour, LOAD_GPU_COLOUR)
        # Which chip is on which side. Both the reading and the colour move.
        self.load_swap = bool(load_swap)
        self.rainbow_shows = rainbow_shows
        self._gamma_table = self._build_gamma(gamma)
        self._stretch = {}
        # This frame's load, taken once - see reading().
        self._reading = None

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

    def reading(self, fresh=False):
        """This frame's load, taken once however often it is asked for.

        fractions() moves the glide on a little every time it is called, so
        asking it twice for one frame would run the bar at twice the rate.
        The frame takes the reading; everything that has to know what came
        back - whether there is a gauge to draw at all, and which of the
        bar's settings reach it - reads the same answer.
        """
        if fresh:
            self._reading = (None if self.load is None
                             else self.load.fractions())
        return self._reading

    def shown_by(self, shows):
        """Which effect a `shows` argument asks for; the setting by default.

        Every entry point takes `shows` so that one caller can say what a
        frame is of without that being a property of the renderer. Game Mode
        never does - what it draws is whatever the rainbow slot was set to,
        which is the whole reason the slot exists. Desktop Mode does, because
        there is no slot there to be limited by: the desktop picks its scene
        outright, and passes it down a frame at a time.
        """
        return self.rainbow_shows if shows is None else shows

    def _substitute(self, snapshot, shows=None):
        """The renderer standing in for the rainbow here, or None.

        None also covers a choice whose hardware is missing, and a load gauge
        that has nothing to read - the first few frames of a fresh start, or
        a machine with no counters at all. A bar drawn from no reading would
        be a reading of nothing, and Steam's own rainbow says "not this"
        better than a dark strip does. Decided here rather than inside the
        gauge so that everything else agrees about what is on the bar: a
        rainbow standing in for the load answers to brightness and speed the
        way a rainbow does, and it could not if the swap were hidden.
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
        """Whether this scene changes from frame to frame.

        The temperature gauge does not: it redraws when the sensor moves,
        which is orders of magnitude slower than a frame, so driving it at the
        full rate would send the same bytes sixty times a second. With nothing
        to read it falls back to the rainbow, which does animate.

        The load gauge is the other way round. Its value glides towards each
        new reading instead of stepping onto it, so it has something new to
        draw every frame - and at the idle rate that glide would be four jumps
        a second, which is exactly what it exists to avoid.
        """
        effect = self._substitute(snapshot, shows)
        if effect is _temperature:
            return self.temperature.celsius() is None
        if effect is not None:
            return True
        return snapshot.is_animated

    def render_logical(self, snapshot, elapsed, shows=None):
        """The 17 logical LEDs of the Steam Machine bar, floats in 0..255."""
        if not snapshot.enabled or snapshot.effect == shim.EFFECT_OFF:
            return [(0.0, 0.0, 0.0)] * shim.LOGICAL_LEDS
        # Before anything asks what is being drawn, and once for the frame.
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

    def render(self, snapshot, elapsed, shows=None):
        """Return the RGB byte payload for the physical strip.

        The frame first, because what was drawn decides how bright it goes
        out: a gauge whose brightness is its reading is not dimmed by the
        setting that dims everything else - see rainbow_takes.
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
