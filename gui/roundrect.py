# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""Rounded rectangles as pixels, for widgets that ttk draws with sharp corners.

ttk has no corner radius, but a widget part can be an image, stretched with
nine-slice scaling - so the corners are drawn here and handed over as pictures.
Pure arithmetic, no tkinter: the shapes are worth testing, and a build machine
has no display. Antialiasing blends against a known background, PhotoImage
having no alpha to speak of.
"""

from __future__ import annotations

import functools
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

    Four is what a notebook tab needs: round on top, square against the page.
    """
    if isinstance(radius, (int, float)):
        return (float(radius),) * 4
    values = tuple(float(value) for value in radius)
    if len(values) != 4:
        raise ValueError("a radius is one number or four")
    return values


def _shape(width, height, radii):
    """Everything about a rounded rectangle that no pixel changes.

    rows() measures a shape once and then asks it about tens of thousands of
    pixels, so the centre, the half-sides and the clamped radii are worked out
    here rather than again for every one of them.
    """
    cap = min(width, height) / 2.0
    return ((width - 1) / 2.0, (height - 1) / 2.0, width / 2.0, height / 2.0,
            tuple(max(0.0, min(value, cap)) for value in radii))


def _distance_in(shape, x, y):
    """Signed distance from one pixel to a measured shape's edge."""
    cx, cy, half_width, half_height, radii = shape
    if y <= cy:
        here = radii[0] if x <= cx else radii[1]
    else:
        here = radii[3] if x <= cx else radii[2]

    dx = abs(x - cx) - (half_width - here)
    dy = abs(y - cy) - (half_height - here)
    outside = math.hypot(max(dx, 0.0), max(dy, 0.0))
    inside = min(max(dx, dy), 0.0)
    return outside + inside - here


def distance(x, y, width, height, radius):
    """Signed distance from a rounded rectangle's edge; negative is inside.

    The usual rounded-box formula: shrink by the radius, measure, subtract it
    back off. Sharp corners fall out as the radius-zero case, tabs as the
    radius-per-corner one, so neither needs a path of its own.
    """
    return _distance_in(_shape(width, height, corner_radii(radius)), x, y)


def coverage(x, y, width, height, radius):
    """How much of this pixel the shape covers, 0..1."""
    return max(0.0, min(1.0, 0.5 - distance(x, y, width, height, radius)))


@functools.lru_cache(maxsize=64)
def _coverage_grid(width, height, radii, border_width):
    """Per pixel: how much of the shape covers it, and how much of the border.

    Keyed on the geometry alone, because the panel draws one shape in several
    colours - a button in its normal, hover and pressed shades are three
    pictures of the same rectangle - and measuring it is the expensive half.
    The result is read, never written, so one grid can serve all of them.
    """
    outer = _shape(width, height, radii)
    inner = (_shape(width - 2 * border_width, height - 2 * border_width,
                    tuple(max(0.0, value - border_width) for value in radii))
             if border_width > 0 else None)

    grid = []
    for y in range(height):
        row = []
        for x in range(width):
            edge = _distance_in(outer, x, y)
            covered = 0.0 if edge >= 0.5 else (1.0 if edge <= -0.5
                                               else 0.5 - edge)
            if inner is None:
                row.append((covered, 0.0))
                continue
            # The ring is what the outer shape covers and the inner does not,
            # so the border keeps its width around the corner.
            edge = _distance_in(inner, x - border_width, y - border_width)
            within = 0.0 if edge >= 0.5 else (1.0 if edge <= -0.5
                                              else 0.5 - edge)
            row.append((covered, max(0.0, covered - within)))
        grid.append(row)
    return grid


def rows(width, height, radius, fill, background, border=None, border_width=1,
         open_bottom=False):
    """The pixels of one rounded rectangle, as rows of "#rrggbb".

    Anything outside the shape comes back as `background`, so the result drops
    onto that colour seamlessly - the price of having no alpha, and why every
    caller has to say what it is sitting on.
    """
    ring = bool(border) and border_width > 0
    grid = _coverage_grid(width, height, corner_radii(radius),
                          border_width if ring else 0)

    picture = []
    for line in grid:
        row = []
        for covered, ringed in line:
            color = blend(background, fill, covered)
            if ringed:
                color = blend(color, border, ringed)
            row.append(color)
        picture.append(row)

    if open_bottom and ring:
        # A tab has no line along its bottom: that edge joins the page, and a
        # border there is the stripe that makes a row of tabs look struck
        # through. Nine-slice repeats these rows, so it would show up doubly.
        for y in range(height - int(math.ceil(border_width)), height):
            for x in range(width):
                picture[y][x] = blend(background, fill, grid[y][x][0])
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


def draw_check(picture, color, thickness=2.2, box=None):
    """Put a tick into an existing picture, filling it or a part of it.

    Drawn rather than taken from a font: at this size the wrong fallback font
    is unreadable, and which one that is depends on the machine.

    `box` is (left, top, width, height) for a tick that belongs to something
    smaller than the whole picture - the thumb of a switch, which is a circle
    somewhere along a track and not the track itself.
    """
    if box is None:
        box = (0, 0, len(picture[0]), len(picture))
    left, top, width, height = box
    points = ((left + 0.26 * width, top + 0.53 * height),
              (left + 0.44 * width, top + 0.71 * height),
              (left + 0.76 * width, top + 0.31 * height))
    for y in range(max(0, int(top)), min(len(picture), int(top + height) + 1)):
        for x in range(max(0, int(left)),
                       min(len(picture[0]), int(left + width) + 1)):
            ink = max(segment_coverage(x, y, points[0], points[1], thickness),
                      segment_coverage(x, y, points[1], points[2], thickness))
            if ink > 0:
                picture[y][x] = blend(picture[y][x], color, ink)
    return picture


def disc_coverage(x, y, centre_x, centre_y, radius):
    """How much of this pixel a filled circle covers, 0..1."""
    return max(0.0, min(1.0, radius + 0.5
                        - math.hypot(x - centre_x, y - centre_y)))


def draw_disc(picture, centre_x, centre_y, radius, colour):
    """Put a filled circle into an existing picture.

    Onto the picture rather than beside it: a switch's thumb sits on its track
    and a radio's dot inside its ring, and with no alpha the only way to get a
    clean edge is to blend against what is really underneath.
    """
    height, width = len(picture), len(picture[0])
    for y in range(max(0, int(centre_y - radius - 1)),
                   min(height, int(centre_y + radius + 2))):
        for x in range(max(0, int(centre_x - radius - 1)),
                       min(width, int(centre_x + radius + 2))):
            ink = disc_coverage(x, y, centre_x, centre_y, radius)
            if ink > 0:
                picture[y][x] = blend(picture[y][x], colour, ink)
    return picture


def draw_ring(picture, centre_x, centre_y, radius, thickness, colour):
    """An unfilled circle: a disc with a smaller one taken back out.

    The hole is cut by remembering what was there and putting it back, so a
    ring can be drawn over anything - which is what a radio button on a card
    needs, the middle of it being the card and not a colour anyone knows here.
    """
    keep = [row[:] for row in picture]
    draw_disc(picture, centre_x, centre_y, radius, colour)
    height, width = len(picture), len(picture[0])
    inner = max(0.0, radius - thickness)
    for y in range(max(0, int(centre_y - inner - 1)),
                   min(height, int(centre_y + inner + 2))):
        for x in range(max(0, int(centre_x - inner - 1)),
                       min(width, int(centre_x + inner + 2))):
            ink = disc_coverage(x, y, centre_x, centre_y, inner)
            if ink > 0:
                picture[y][x] = blend(picture[y][x], keep[y][x], ink)
    return picture


def as_put_string(picture):
    """Rows in the form PhotoImage.put() wants: {row} {row} ..."""
    return " ".join("{%s}" % " ".join(row) for row in picture)


def pill(length, thickness, fill, background, border=None, border_width=1):
    """A fully rounded bar - the radius is simply half the short side."""
    return rows(length, thickness, thickness / 2.0, fill, background,
                border=border, border_width=border_width)
