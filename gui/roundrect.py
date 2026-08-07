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


def distance(x, y, width, height, radius):
    """Signed distance from a rounded rectangle's edge; negative is inside.

    The usual rounded-box formula: shrink the box by the radius, measure to
    that, then subtract the radius back off. Sharp corners fall out of it as
    the radius-zero case, so there is no separate path for them.
    """
    radius = max(0.0, min(radius, min(width, height) / 2.0))
    dx = abs(x - (width - 1) / 2.0) - (width / 2.0 - radius)
    dy = abs(y - (height - 1) / 2.0) - (height / 2.0 - radius)
    outside = math.hypot(max(dx, 0.0), max(dy, 0.0))
    inside = min(max(dx, dy), 0.0)
    return outside + inside - radius


def coverage(x, y, width, height, radius):
    """How much of this pixel the shape covers, 0..1."""
    return max(0.0, min(1.0, 0.5 - distance(x, y, width, height, radius)))


def rows(width, height, radius, fill, background, border=None, border_width=1):
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
                                 radius - border_width)
                color = blend(color, border, max(0.0, inside - inner))
            row.append(color)
        picture.append(row)
    return picture


def as_put_string(picture):
    """Rows in the form PhotoImage.put() wants: {row} {row} ..."""
    return " ".join("{%s}" % " ".join(row) for row in picture)


def pill(length, thickness, fill, background, border=None, border_width=1):
    """A fully rounded bar - the radius is simply half the short side."""
    return rows(length, thickness, thickness / 2.0, fill, background,
                border=border, border_width=border_width)
