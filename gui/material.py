# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""Calculates the Material Design 3 colour roles from one seed colour.

Material 3 does not paint a window from a small set of selected colours. It
paints the window from *tones* of a small number of palettes: one accent, one
near-grey with the hue of the accent, and one for errors. Each surface, each
label and each outline is then a role with a name. `surface_container_high`
and `on_surface_variant` are two such roles. Each role takes one fixed step
of one of those ladders.

Two results follow, and they are the reason for the method. First, the steps
give the contrast, and no person selects it. A label therefore cannot become
unreadable in a dark scheme. Second, a raised surface is a lighter *tone* and
not a border or a shadow. A window can then hold twelve groups of controls
without twelve boxes around them.

The seed is the accent colour of the desktop, so this is not a second theme
against the first one. Plasma gives the colour of the machine and the light
or dark condition. This module makes the ladders from those two values. A
KDE-blue desktop gets a blue-grey window, and an orange desktop gets a
warm-grey window. This module lists neither colour.

This module calculates the tones in OKLab and not in HSL. HSL lightness is
not lightness. #0000ff and #ffff00 both have the HSL lightness 50. From one
step number, an HSL ladder therefore gives a blue scheme dark surfaces and a
yellow scheme bright ones. The L of OKLab is near to the value that an eye
reports. One number then means one brightness for each hue, which is the
purpose of a tonal ladder.

The Material specification uses HCT, which is CAM16 with a tone axis. OKLab
is near enough for a widget set. It also needs one page of arithmetic and no
dependency. That is important here, because SteamOS removes each package
that a package manager installs.

This module does not use tkinter. It calculates colours, and a machine with
no display can test it. The panel does the paint step.
"""

from __future__ import annotations

import math

# -- sRGB <-> OKLab ---------------------------------------------------------
#
# Björn Ottosson's OKLab, in the usual two steps: sRGB to linear light, then
# the LMS-and-cube-root pair that makes the space perceptual.


def _unpack(colour):
    return tuple(int(colour[index:index + 2], 16) / 255.0
                 for index in (1, 3, 5))


def _pack(channels):
    return "#%02x%02x%02x" % tuple(
        max(0, min(255, int(round(value * 255)))) for value in channels)


def _to_linear(channel):
    return (channel / 12.92 if channel <= 0.04045
            else ((channel + 0.055) / 1.055) ** 2.4)


def _from_linear(channel):
    return (channel * 12.92 if channel <= 0.0031308
            else 1.055 * channel ** (1 / 2.4) - 0.055)


def to_oklab(colour):
    """"#rrggbb" as (L, a, b): lightness 0..1 and two opponent axes."""
    red, green, blue = (_to_linear(channel) for channel in _unpack(colour))
    long = 0.4122214708 * red + 0.5363325363 * green + 0.0514459929 * blue
    medium = 0.2119034982 * red + 0.6806995451 * green + 0.1073969566 * blue
    short = 0.0883024619 * red + 0.2817188376 * green + 0.6299787005 * blue
    long, medium, short = (_cbrt(value) for value in (long, medium, short))
    return (0.2104542553 * long + 0.7936177850 * medium - 0.0040720468 * short,
            1.9779984951 * long - 2.4285922050 * medium + 0.4505937099 * short,
            0.0259040371 * long + 0.7827717662 * medium - 0.8086757660 * short)


def _cbrt(value):
    return math.copysign(abs(value) ** (1 / 3.0), value)


def _oklab_channels(lightness, green_red, blue_yellow):
    """The linear-light channels for one OKLab colour, unclamped."""
    long = lightness + 0.3963377774 * green_red + 0.2158037573 * blue_yellow
    medium = lightness - 0.1055613458 * green_red - 0.0638541728 * blue_yellow
    short = lightness - 0.0894841775 * green_red - 1.2914855480 * blue_yellow
    long, medium, short = (value ** 3 for value in (long, medium, short))
    return (4.0767416621 * long - 3.3077115913 * medium + 0.2309699292 * short,
            -1.2684380046 * long + 2.6097574011 * medium - 0.3413193965 * short,
            -0.0041960863 * long - 0.7034186147 * medium + 1.7076147010 * short)


def from_oklab(lightness, green_red, blue_yellow):
    """(L, a, b) back to "#rrggbb", clamped into the display's range."""
    return _pack(_from_linear(max(0.0, min(1.0, channel)))
                 for channel in _oklab_channels(lightness, green_red,
                                                blue_yellow))


def _in_gamut(lightness, chroma, hue):
    channels = _oklab_channels(lightness,
                               chroma * math.cos(hue), chroma * math.sin(hue))
    return all(-0.0001 <= channel <= 1.0001 for channel in channels)


def to_oklch(colour):
    """"#rrggbb" as (lightness, chroma, hue in radians)."""
    lightness, green_red, blue_yellow = to_oklab(colour)
    return (lightness, math.hypot(green_red, blue_yellow),
            math.atan2(blue_yellow, green_red))


def from_oklch(lightness, chroma, hue):
    """Returns a colour at this lightness and hue, with the maximum chroma.

        A request for more chroma than sRGB holds at a lightness is normal,
        because there is no strong near-white colour. So this function removes the
        excess chroma. The clamp in from_oklab would move the hue instead.
        Sixteen bisection steps put the edge inside one rounding step of one
        channel.
        """
    if not _in_gamut(lightness, chroma, hue):
        low, high = 0.0, chroma
        for _ in range(16):
            chroma = (low + high) / 2.0
            if _in_gamut(lightness, chroma, hue):
                low = chroma
            else:
                high = chroma
        chroma = low
    return from_oklab(lightness, chroma * math.cos(hue),
                      chroma * math.sin(hue))


def blend(background, foreground, amount):
    """Puts `amount` of one colour over another colour, both "#rrggbb".

        This is plain sRGB compositing, because a translucent layer on a screen
        does the same. It is not a perceptual mix, and it must not be one.
        """
    if amount <= 0:
        return background
    if amount >= 1:
        return foreground
    back, front = _unpack(background), _unpack(foreground)
    return _pack(back[index] * (1 - amount) + front[index] * amount
                 for index in range(3))


# -- the ladders ------------------------------------------------------------
#
# Material names its rungs 0..100, 0 being black and 100 white, so the numbers
# below read the same as they do in the spec.

# The chroma of each ladder. The accent keeps the chroma of the desktop. If
# that value is too low to read as an accent, the accent uses the floor. The
# two greys have very little colour, and that is why a Material window looks
# calm. The window is not grey. It holds three per cent of the accent.
PRIMARY_CHROMA_FLOOR = 0.11
SECONDARY_CHROMA = 0.05
NEUTRAL_CHROMA = 0.012
NEUTRAL_VARIANT_CHROMA = 0.026


def lightness_of(tone):
    """Returns the Material tone 0 to 100 as an OKLab lightness.

        The result is not tone/100. The Material tone is CIE L*, and the L of
        OKLab is the cube root of the luminance. Tone 50 is therefore OKLab 0.57.
        A direct use of the tone makes each surface darker than the specification
        gives, and the dark scheme the most of all. Tone 6 then becomes almost
        black, and not a dark grey with visible detail.

        The luminance connects the two scales. L* to Y is the standard CIE pair,
        with the linear segment at the low end. That segment keeps the curve
        flat at black. For a neutral colour, the L of OKLab is the cube root of
        Y. So the conversion becomes one line for most of the range.
        """
    if tone > 8:
        return (tone + 16.0) / 116.0
    return (tone / 903.3) ** (1 / 3.0)


class Ladder:
    """One palette: the same hue at any tone from 0 to 100."""

    def __init__(self, seed, chroma=None, floor=None):
        lightness, own, hue = to_oklch(seed)
        self.hue = hue
        self.chroma = own if chroma is None else chroma
        if floor is not None:
            self.chroma = max(self.chroma, floor)
        # A seed with no hue is a pure grey accent. Without this step, such a
        # seed takes its hue from the rounding error of atan2.
        if own < 0.002:
            self.chroma = min(self.chroma, own)
        self.lightness = lightness

    def __call__(self, tone):
        """The rung at this tone, 0 (black) to 100 (white)."""
        return from_oklch(lightness_of(max(0.0, min(100.0, tone))),
                          self.chroma, self.hue)


# -- the roles --------------------------------------------------------------
#
# Which rung of which ladder each named role is, light scheme and dark. Taken
# from the Material 3 spec; the pairs are what guarantee the contrast, so they
# are written out rather than worked out.
#
# ("ladder", light tone, dark tone)
ROLES = {
    "primary": ("primary", 40, 80),
    "on_primary": ("primary", 100, 20),
    "primary_container": ("primary", 90, 30),
    "on_primary_container": ("primary", 10, 90),
    "inverse_primary": ("primary", 80, 40),

    "secondary": ("secondary", 40, 80),
    "on_secondary": ("secondary", 100, 20),
    "secondary_container": ("secondary", 90, 30),
    "on_secondary_container": ("secondary", 10, 90),

    # The five containers are the whole elevation story: a card is not a box
    # with a line round it, it is one of these instead of `surface`.
    "surface": ("neutral", 98, 6),
    "surface_dim": ("neutral", 87, 6),
    "surface_bright": ("neutral", 98, 24),
    "surface_container_lowest": ("neutral", 100, 4),
    "surface_container_low": ("neutral", 96, 10),
    "surface_container": ("neutral", 94, 12),
    "surface_container_high": ("neutral", 92, 17),
    "surface_container_highest": ("neutral", 90, 22),
    "on_surface": ("neutral", 10, 90),
    "inverse_surface": ("neutral", 20, 90),
    "inverse_on_surface": ("neutral", 95, 20),

    # Secondary text and icons, and the two weights of line. outline_variant is
    # for a divider that separates without being noticed; outline is for a
    # control's own edge, which has to be seen.
    "on_surface_variant": ("neutral_variant", 30, 80),
    "outline": ("neutral_variant", 50, 60),
    "outline_variant": ("neutral_variant", 80, 30),

    "error": ("error", 40, 80),
    "on_error": ("error", 100, 20),
    "error_container": ("error", 90, 30),
    "on_error_container": ("error", 10, 90),

    # This role is not in Material. Material has no colour for a good
    # condition. A checklist of green ticks needs one role with the same
    # behaviour as the other roles.
    "positive": ("positive", 40, 80),
    "positive_container": ("positive", 90, 30),
    "on_positive_container": ("positive", 10, 90),
}

# What a hover or a press does: a wash of the content colour over whatever is
# underneath, at these strengths. One mechanism for every control, so a hovered
# button and a hovered tab lift by the same amount.
STATE_HOVER = 0.08
STATE_FOCUS = 0.10
STATE_PRESSED = 0.10
STATE_DRAGGED = 0.16

# A disabled control keeps its shape and loses its weight: the label at 38% and
# the container at 12%, both of the colour they would have had.
DISABLED_CONTENT = 0.38
DISABLED_CONTAINER = 0.12

# Material's shape scale, in pixels. Rounder than the panel used to be, and
# consistently so: the corner radius says how large a thing is.
SHAPE_EXTRA_SMALL = 4
SHAPE_SMALL = 8
SHAPE_MEDIUM = 12
SHAPE_LARGE = 16
SHAPE_EXTRA_LARGE = 28
SHAPE_FULL = 999          # clamped to half the short side by roundrect

# The 4dp grid everything is spaced on.
SPACE = 4

# -- how big a control is ---------------------------------------------------
#
# The sizes are not fixed. A switch with the correct size for a ten point
# desktop font is too small for a thirteen point font. A settings row holds
# three types of control: a switch, a slider and a drop-down. They agree on a
# height only when the code makes them agree. Without this code they measured
# 32, 24 and 42 pixels, and a column of such rows looks irregular at each
# spacing. So this module calculates all three from the font, and all three
# get the same height.

# How much taller than its text a control is: Material's own field is the text
# plus a comfortable thumb's worth above and below.
CONTROL_PADDING = 20

# Floors, for a desktop set to a very small font. These are what keeps every
# target above the ~20 px that guidelines call the smallest sensible one, on a
# machine that is also used handheld.
CONTROL_FLOOR = 36
SWITCH_FLOOR = 30
KNOB_FLOOR = 24
RADIO_FLOOR = 22

# A switch is wider than it is tall by approximately this factor. The
# Material switch is 52 by 32.
SWITCH_RATIO = 1.65
# The thumb, as a fraction of the switch's height. It grows when the switch
# goes on, so the state can be read from the shape and not only the colour.
THUMB_ON = 0.36
THUMB_OFF = 0.25


def control_sizes(linespace):
    """Returns the pixel sizes of each control, for a font of this height.

        `linespace` is the measured height of the font, and only tkinter can give
        it. The caller passes it in. A machine with no display can then test the
        arithmetic.
        """
    control = max(CONTROL_FLOOR, linespace + CONTROL_PADDING)
    # The same height as a drop-down, and not some pixels less. The rows
    # have a fixed pitch. A control with less height therefore gets more
    # space around it than its neighbours get. That made a switch above a
    # drop-down look incorrectly spaced, although the rows were equal.
    switch = max(SWITCH_FLOOR, control)
    knob = max(KNOB_FLOOR, control - 12)
    # The groove is drawn inside an image as tall as the knob, so an odd
    # difference would leave it half a pixel off centre.
    track = knob // 2
    if (knob - track) % 2:
        track -= 1
    return {
        "control": control,
        "switch_width": int(round(switch * SWITCH_RATIO)),
        "switch_height": switch,
        "thumb_on": int(round(switch * THUMB_ON)),
        "thumb_off": int(round(switch * THUMB_OFF)),
        "knob": knob,
        "track": track,
        "radio": max(RADIO_FLOOR, control - 12),
        # The space that a drop-down needs above and below its text, to get
        # the height of the other controls. This is four pixels less than
        # the arithmetic gives, because ttk adds a field border of eight
        # pixels. No style option changes that border.
        "field_padding": max(3, (control - linespace) // 2 - 4),
    }


def ladders(seed, error=None, positive=None):
    """The palettes one seed colour implies."""
    return {
        "primary": Ladder(seed, floor=PRIMARY_CHROMA_FLOOR),
        "secondary": Ladder(seed, chroma=SECONDARY_CHROMA),
        "neutral": Ladder(seed, chroma=NEUTRAL_CHROMA),
        "neutral_variant": Ladder(seed, chroma=NEUTRAL_VARIANT_CHROMA),
        "error": Ladder(error or "#ba1a1a", floor=PRIMARY_CHROMA_FLOOR),
        "positive": Ladder(positive or "#27ae60", floor=PRIMARY_CHROMA_FLOOR),
    }


def scheme(seed, dark=False, error=None, positive=None):
    """Every role as "#rrggbb", for one seed and one brightness."""
    built = ladders(seed, error=error, positive=positive)
    return {role: built[ladder](dark_tone if dark else light_tone)
            for role, (ladder, light_tone, dark_tone) in ROLES.items()}


def layer(roles, container, content, opacity):
    """Returns a state layer: `content` over `container` at this strength.

        Both parameters are role names and not colours. That keeps the states
        consistent. A button and a tab in the same state then use the same two
        roles, and not two selected colours that become different with time.
        """
    return blend(roles[container], roles[content], opacity)


def disabled(roles, colour, on="surface"):
    """A colour at the weight a disabled control's *content* keeps."""
    return blend(roles[on], colour, DISABLED_CONTENT)


def disabled_container(roles, on="on_surface", over="surface"):
    """The fill a disabled container keeps: barely there, but still a shape."""
    return blend(roles[over], roles[on], DISABLED_CONTAINER)


def contrast(first, second):
    """Returns the WCAG contrast ratio between two "#rrggbb", from 1 to 21.

        This function selects no colour. The tone pairs of the specification do
        that. The tests use this function to prove that the pairs keep their
        contrast in OKLab. The change from HCT to OKLab is the one step that can
        break them without a visible sign.
        """
    def relative(colour):
        red, green, blue = (_to_linear(channel) for channel in _unpack(colour))
        return 0.2126 * red + 0.7152 * green + 0.0722 * blue

    first, second = relative(first), relative(second)
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)
