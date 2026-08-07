"""Read the colours and font KDE Plasma is using.

tkinter has no idea a desktop theme exists, which is why an unstyled panel
looks like a visitor from another decade. Plasma, however, writes its active
colour scheme into ~/.config/kdeglobals as plain INI - so the colours can
simply be read and handed to ttk.

No tkinter in here: parsing a file is worth testing, painting widgets is not.
"""

from __future__ import annotations

import configparser
import os

KDEGLOBALS = "~/.config/kdeglobals"

# Breeze, the Plasma default, for when there is no kdeglobals to read - a
# machine without KDE, or a fresh account that has never opened the settings.
BREEZE_LIGHT = {
    "window": "#eff0f1", "window_text": "#232627",
    "view": "#fcfcfc", "view_text": "#232627",
    "button": "#fcfcfc", "button_text": "#232627",
    "selection": "#3daee9", "selection_text": "#ffffff",
    "positive": "#27ae60", "negative": "#da4453", "neutral": "#f67400",
}

# Which kdeglobals section and key each of ours comes from.
_SOURCES = {
    "window": ("Colors:Window", "BackgroundNormal"),
    "window_text": ("Colors:Window", "ForegroundNormal"),
    "view": ("Colors:View", "BackgroundNormal"),
    "view_text": ("Colors:View", "ForegroundNormal"),
    "button": ("Colors:Button", "BackgroundNormal"),
    "button_text": ("Colors:Button", "ForegroundNormal"),
    "selection": ("Colors:Selection", "BackgroundNormal"),
    "selection_text": ("Colors:Selection", "ForegroundNormal"),
    "positive": ("Colors:View", "ForegroundPositive"),
    "negative": ("Colors:View", "ForegroundNegative"),
    "neutral": ("Colors:View", "ForegroundNeutral"),
}


def parse_color(text):
    """"r,g,b" or "r,g,b,a" as KDE writes it, into "#rrggbb"."""
    parts = [part.strip() for part in str(text).split(",")]
    if len(parts) < 3:
        return None
    try:
        channels = [int(part) for part in parts[:3]]
    except ValueError:
        return None
    if any(channel < 0 or channel > 255 for channel in channels):
        return None
    return "#%02x%02x%02x" % tuple(channels)


def parse_font(text):
    """Plasma's font line, e.g. "Noto Sans,10,-1,5,50,0,0,0,0,0".

    Only the family and size are of interest; the rest describes weight and
    style in Qt's own numbering, which Tk does not share.
    """
    parts = [part.strip() for part in str(text).split(",")]
    if not parts or not parts[0]:
        return None
    family = parts[0]
    size = 10
    if len(parts) > 1:
        try:
            size = int(float(parts[1]))
        except ValueError:
            pass
    return (family, max(6, min(size, 32)))


def luminance(color):
    """Perceived brightness of "#rrggbb", 0..1."""
    red, green, blue = (int(color[index:index + 2], 16)
                        for index in (1, 3, 5))
    return (0.299 * red + 0.587 * green + 0.114 * blue) / 255.0


def is_dark(palette):
    """Whether this is a dark scheme, judged by the window background."""
    return luminance(palette["window"]) < 0.5


def mix(first, second, weight=0.5):
    """Blend two "#rrggbb" colours; weight is how much of `second`."""
    channels = []
    for index in (1, 3, 5):
        left = int(first[index:index + 2], 16)
        right = int(second[index:index + 2], 16)
        channels.append(int(round(left * (1 - weight) + right * weight)))
    return "#%02x%02x%02x" % tuple(channels)


def read(path=None):
    """The desktop's palette, falling back to Breeze where anything is missing.

    Every value is filled in: a partial or hand-edited kdeglobals must not
    produce a window with three colours and two holes.
    """
    palette = dict(BREEZE_LIGHT)
    palette["font"] = None

    parser = configparser.ConfigParser(strict=False, interpolation=None)
    try:
        with open(os.path.expanduser(path or KDEGLOBALS),
                  "r", errors="replace") as handle:
            parser.read_file(handle)
    except (OSError, configparser.Error):
        return palette

    for name, (section, key) in _SOURCES.items():
        if parser.has_option(section, key):
            color = parse_color(parser.get(section, key))
            if color:
                palette[name] = color

    if parser.has_option("General", "font"):
        palette["font"] = parse_font(parser.get("General", "font"))
    return palette


def derived(palette):
    """The few shades a widget set needs that Plasma does not name.

    Borders and hover states are drawn by Qt itself, so the scheme has no entry
    for them - they get mixed from the colours it does name, which keeps them
    right in a dark scheme as well as a light one.
    """
    text = palette["window_text"]
    window = palette["window"]
    return {
        "border": mix(window, text, 0.25),
        "hover": mix(window, palette["selection"], 0.18),
        "muted": mix(window, text, 0.55),
        "raised": mix(window, "#ffffff" if not is_dark(palette) else "#000000",
                      0.35),
    }
