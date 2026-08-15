# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""What our own effects look like, for the panel to draw before you commit.

Only the ones this project added: the four that can stand in the rainbow slot
and the six notification shapes. Steam's own effects are not here - you can
already see those by picking them - and neither are the standby and startup
breaths, which are states the machine puts itself into rather than settings.

No tkinter in here on purpose, the same way ledpanel.py avoids it: this is the
part that decides what the pixels are, and it should be testable on a machine
with no display. The panel owns the canvas.

Frames come from render.py and notify.py, so the preview cannot drift from the
strip - and because it reads the settings currently in the window rather than
the ones on disk, it shows what Apply is about to do rather than what is
already running.
"""

from __future__ import annotations

from steamos_led import config as config_module
from steamos_led import notify, render, shim

# One flash, then a beat of dark before it starts again, so a shape is seen to
# end rather than looping into itself.
FLASH_PAUSE = 0.7

# How long the two gauges take to walk their demonstration and return. Long
# enough to read, short enough that nobody waits for the interesting part.
SWEEP_SECONDS = 12.0

# What the temperature sweep covers, in degrees. Wider than the marks so both
# ends of the scale are reached whatever they are set to.
SWEEP_LOW = 20.0
SWEEP_HIGH = 100.0

# Idle, a menu, a game, and back. The GPU spikier than the CPU, as it is.
LOAD_WALK = ((0.05, 0.02), (0.22, 0.10), (0.35, 0.62), (0.78, 0.96),
             (0.55, 0.88), (0.18, 0.09), (0.05, 0.02))


class _Scripted:
    """A sensor reading whatever the preview has walked to.

    The real sources read hardware and smooth it; here the walk is the whole
    point, so it is handed over undamped.
    """

    def __init__(self):
        self.celsius_value = SWEEP_LOW
        self.load_value = (0.0, 0.0)

    def celsius(self, now=None):
        return self.celsius_value

    def fractions(self, now=None):
        return self.load_value


def _there_and_back(fraction):
    """0 -> 1 -> 0 across one cycle, so a sweep loops without a jump."""
    walk = (fraction % 1.0) * 2.0
    return walk if walk <= 1.0 else 2.0 - walk


def _along(waypoints, fraction):
    """Walk a list of tuples, interpolating between neighbours."""
    place = (fraction % 1.0) * (len(waypoints) - 1)
    first = min(int(place), len(waypoints) - 2)
    blend = place - first
    return tuple(waypoints[first][axis]
                 + (waypoints[first + 1][axis] - waypoints[first][axis]) * blend
                 for axis in range(len(waypoints[0])))


class Preview:
    """The pixels of one effect at one moment, from the real renderers.

    `settings` is whatever the panel has in its widgets right now, missing
    keys falling back to the shipped defaults - so the temperature marks, the
    notification colours and the flash duration all show up in the picture.
    """

    def __init__(self, settings=None):
        self.settings = settings or {}
        self.sensor = _Scripted()
        self._overlay = None
        self._overlay_key = None

    def setting(self, key):
        return self.settings.get(key, config_module.DEFAULTS[key])

    # -- the four that can stand in the rainbow slot -----------------------

    def _renderer(self, shows):
        return render.Renderer(
            led_count=shim.LOGICAL_LEDS,
            mapping=render.MAPPING_CROP,
            speed_scale=self.setting("SPEED"),
            rainbow_shows=shows,
            temperature=self.sensor,
            load=self.sensor,
            temperature_range=(self.setting("TEMPERATURE_MIN"),
                               self.setting("TEMPERATURE_MAX")))

    def slot_frame(self, shows, elapsed):
        """One frame of `fire`, `aurora`, `temperature` or `load`."""
        if shows == render.SHOWS_TEMPERATURE:
            self.sensor.celsius_value = (
                SWEEP_LOW + _there_and_back(elapsed / SWEEP_SECONDS)
                * (SWEEP_HIGH - SWEEP_LOW))
        elif shows == render.SHOWS_LOAD:
            self.sensor.load_value = _along(LOAD_WALK,
                                            elapsed / SWEEP_SECONDS)
        snapshot = shim.make_snapshot(shim.EFFECT_RAINBOW)
        return self._renderer(shows).render_logical(snapshot, elapsed)

    # -- the six notification shapes ---------------------------------------

    def shape_frame(self, shape, colour, elapsed):
        """One frame of a flash, looping with a pause between repeats.

        A fresh overlay per cycle rather than one that is retriggered: the
        overlay is the thing holding where a flash has got to, and starting it
        again is exactly what a new one means. It also keeps this to the same
        public API the service uses.

        `repeat_gap` is nought here, which it never is in the service. There
        the gap stops a burst of the same notification from holding the bar;
        here the repetition *is* the point, and the real gap would show the
        shape once and then leave the strip dark for ten seconds.

        MAX_BRIGHTNESS is deliberately not applied. It would only reach the
        shapes - render_logical, which draws the other half of this tab, has
        no ceiling in it - and a preview where one half dims and the other
        does not is worse than one that shows both at full.
        """
        duration = self.setting("NOTIFY_DURATION")
        cycle = duration + FLASH_PAUSE
        started = (elapsed // cycle) * cycle
        key = (shape, colour, duration, started)
        if key != self._overlay_key:
            self._overlay_key = key
            self._overlay = notify.NotificationOverlay(
                duration=duration, led_count=shim.LOGICAL_LEDS, style=shape,
                repeat_gap=0.0)
            self._overlay.trigger(colour, started)

        payload = self._overlay.frame(elapsed)
        if payload is None:
            return [(0.0, 0.0, 0.0)] * shim.LOGICAL_LEDS
        return [(payload[led * 3], payload[led * 3 + 1], payload[led * 3 + 2])
                for led in range(shim.LOGICAL_LEDS)]


# What the tab offers, in the order it offers it. Steam's own effects are left
# out deliberately; so are the standby and startup breaths, which nobody picks.
SLOT_EFFECTS = (
    ("Fire", render.SHOWS_FIRE, "Flame drifting along the strip"),
    ("Aurora", render.SHOWS_AURORA, "Slow curtains, green to violet"),
    ("Temperature", render.SHOWS_TEMPERATURE,
     "Colour carries the reading, cool to hot"),
    ("Load", render.SHOWS_LOAD, "CPU left in amber, GPU right in blue"),
)

SHAPE_BLURBS = {
    notify.STYLE_BLOOM: "Out of the middle, one breath, back",
    notify.STYLE_PULSE: "The whole bar swells three times",
    notify.STYLE_DOUBLE_FLASH: "Blink, blink, pause - and again",
    notify.STYLE_COMET: "A head with a tail, once across",
    notify.STYLE_ALTERNATE: "Two halves in antiphase - the warning",
    notify.STYLE_SPARKLE: "Grains light up and die out",
}


def shape_effects():
    """(label, style, blurb) for every shape the service implements."""
    return tuple((style.replace("_", " ").capitalize(), style,
                  SHAPE_BLURBS.get(style, ""))
                 for style in notify.STYLES)


def entries():
    """Everything the tab can show: (label, kind, name, blurb)."""
    listing = [(label, "slot", name, blurb)
               for label, name, blurb in SLOT_EFFECTS]
    listing += [(label, "shape", name, blurb)
                for label, name, blurb in shape_effects()]
    return tuple(listing)


# -- drawing it, without knowing what it is drawn on ------------------------
#
# The panel owns the canvas; these two only turn pixels into the strings a
# canvas wants. Kept here so the arithmetic is testable without a display.

BACKDROP = "#0d0e11"        # the stage a strip is judged against: a dim room


def hex_colour(pixel):
    """One rendered pixel as #rrggbb, clamped."""
    return "#%02x%02x%02x" % tuple(
        max(0, min(int(channel), 255)) for channel in pixel)


def toward(pixel, amount, backdrop=BACKDROP):
    """`pixel` mixed this far towards it, as #rrggbb.

    A canvas has no alpha and no blur, so a glow is drawn as a solid colour
    that has already been mixed with the background it sits on. That only
    works because the background is one known flat colour, which is why the
    stage does not follow the desktop theme.
    """
    base = [int(backdrop[index:index + 2], 16) for index in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(
        max(0, min(int(ground + (channel - ground) * amount), 255))
        for ground, channel in zip(base, pixel))
