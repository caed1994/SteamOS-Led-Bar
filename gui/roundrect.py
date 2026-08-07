"""Rounded rectangles as pixels, for widgets that ttk draws with sharp corners.

ttk has no corner radius. What it does have is image elements: a widget part
can be a picture, stretched with nine-slice scaling. So the corners are drawn
here and handed over as images.

Pure arithmetic, no tkinter - the shapes are worth checking, and a build
machine has no display. Antialiasing is done by blending against a known
background, because tkinter's PhotoImage has no alpha channel to speak of.
"""

from __future__ import annotations

import math


def _unpack(color):
    return tuple(int(color[index:index + 2], 16) for index in (1, 3, 5))


def _pack(channels):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(value))))
                                   for value in channels)


def blend(background, foreground, amount):
    """`amount` of foreground over background, both "#rrggbb"."""
    if amount <= 0:
        return background
    if amount >= 1:
        return foreground
    back, front = _unpack(background), _unpack(foreground)
    return _pack(back[index] * (1 - amount) + front[index] * amount
                 for index in range(3))


def corner_radii(radius):
    """One radius or four, as (top left, top right, bottom right, bottom left).

    Four of them is what a notebook tab needs: round on top, square where it
    meets the page.
    """
    if isinstance(radius, (int, float)):
        return (float(radius),) * 4
    values = tuple(float(value) for value in radius)
    if len(values) != 4:
        raise ValueError("a radius is one number or four")
    return values


def distance(x, y, width, height, radius):
    """Signed distance from a rounded rectangle's edge; negative is inside.

    The usual rounded-box formula: shrink the box by the radius, measure to
    that, then subtract the radius back off. Sharp corners fall out of it as
    the radius-zero case, so there is no separate path for them - and with a
    radius per corner, neither do tabs.
    """
    radii = corner_radii(radius)
    cx, cy = (width - 1) / 2.0, (height - 1) / 2.0
    if y <= cy:
        here = radii[0] if x <= cx else radii[1]
    else:
        here = radii[3] if x <= cx else radii[2]

    here = max(0.0, min(here, min(width, height) / 2.0))
    dx = abs(x - cx) - (width / 2.0 - here)
    dy = abs(y - cy) - (height / 2.0 - here)
    outside = math.hypot(max(dx, 0.0), max(dy, 0.0))
    inside = min(max(dx, dy), 0.0)
    return outside + inside - here


def coverage(x, y, width, height, radius):
    """How much of this pixel the shape covers, 0..1."""
    return max(0.0, min(1.0, 0.5 - distance(x, y, width, height, radius)))


def rows(width, height, radius, fill, background, border=None, border_width=1,
         open_bottom=False):
    """The pixels of one rounded rectangle, as rows of "#rrggbb".

    Anything outside the shape comes back as `background`, so the result drops
    onto that colour without a visible seam - which is the price of having no
    alpha, and the reason every caller has to say what it is sitting on.
    """
    picture = []
    for y in range(height):
        row = []
        for x in range(width):
            inside = coverage(x, y, width, height, radius)
            color = blend(background, fill, inside)
            if border and border_width > 0:
                # The ring is what the outer shape covers and the inner one
                # does not, so a rounded border stays the same width all the
                # way around the corner.
                inner = coverage(x - border_width, y - border_width,
                                 width - 2 * border_width,
                                 height - 2 * border_width,
                                 tuple(max(0.0, value - border_width)
                                       for value in corner_radii(radius)))
                color = blend(color, border, max(0.0, inside - inner))
            row.append(color)
        picture.append(row)

    if open_bottom and border and border_width > 0:
        # A tab has no line along its bottom: that edge is where it joins the
        # page, and a border there is exactly the stripe that makes a row of
        # tabs look like it has been struck through. It matters twice over,
        # because nine-slice scaling repeats the bottom rows to fill height.
        for y in range(height - int(math.ceil(border_width)), height):
            for x in range(width):
                inside = coverage(x, y, width, height, radius)
                picture[y][x] = blend(background, fill, inside)
    return picture


def segment_coverage(x, y, start, end, thickness):
    """How much of this pixel a line segment covers, 0..1."""
    (x0, y0), (x1, y1) = start, end
    dx, dy = x1 - x0, y1 - y0
    length = dx * dx + dy * dy
    if length == 0:
        along = 0.0
    else:
        along = max(0.0, min(1.0, ((x - x0) * dx + (y - y0) * dy) / length))
    near = math.hypot(x - (x0 + along * dx), y - (y0 + along * dy))
    return max(0.0, min(1.0, thickness / 2.0 + 0.5 - near))


def draw_check(picture, color, thickness=2.2):
    """Put a tick in the middle of an existing picture.

    Drawn rather than taken from a font: a glyph would depend on what is
    installed, and at sixteen pixels the wrong fallback font is unreadable.
    """
    height, width = len(picture), len(picture[0])
    points = ((0.26 * width, 0.53 * height),
              (0.44 * width, 0.71 * height),
              (0.76 * width, 0.31 * height))
    for y in range(height):
        for x in range(width):
            ink = max(segment_coverage(x, y, points[0], points[1], thickness),
                      segment_coverage(x, y, points[1], points[2], thickness))
            if ink > 0:
                picture[y][x] = blend(picture[y][x], color, ink)
    return picture


def as_put_string(picture):
    """Rows in the form PhotoImage.put() wants: {row} {row} ..."""
    return " ".join("{%s}" % " ".join(row) for row in picture)


def pill(length, thickness, fill, background, border=None, border_width=1):
    """A fully rounded bar - the radius is simply half the short side."""
    return rows(length, thickness, thickness / 2.0, fill, background,
                border=border, border_width=border_width)
