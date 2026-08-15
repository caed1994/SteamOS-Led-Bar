#!/usr/bin/env python3
"""Draw the README's effect previews, straight from the renderer.

GitHub runs no JavaScript in a README, so the previews have to be images.
They are animated PNGs rather than GIFs: a GIF has 256 colours to spend and
these are all gradients, which is exactly what a palette cannot do. APNG is
plain PNG chunks plus three more, so zlib out of the standard library is the
whole dependency.

Nothing here reimplements an effect. The two gauges read a scripted sensor -
a machine does not warm from 25 to 95 degrees on cue - but every frame is
drawn by render.py and notify.py as the service uses them.

    python3 tools/make-previews.py [--out docs/previews]
"""

from __future__ import annotations

import argparse
import os
import struct
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "server"))

from steamos_led import notify, render, service, shim  # noqa: E402

FPS = 14                    # smooth enough for these, and every frame is bytes
LEDS = shim.LOGICAL_LEDS

# A colour somebody picked in Steam's own menu, for the effects that take one.
STEAM_COLOUR = (26, 159, 255)

CELL = 22                   # one LED, in pixels
GAP = 4
BAR_HEIGHT = 20
SPILL_HEIGHT = 12           # the light falling on whatever it is mounted above
MARGIN = 6
BACKDROP = (13, 14, 17)

WIDTH = LEDS * CELL + (LEDS - 1) * GAP + MARGIN * 2
HEIGHT = BAR_HEIGHT + SPILL_HEIGHT + MARGIN * 2


# -- drawing ---------------------------------------------------------------


def raster(pixels):
    """One frame as rows of (r, g, b), the bar over its own reflection."""
    rows = [[BACKDROP] * WIDTH for _ in range(HEIGHT)]

    spread = []             # the reflection, one blurred colour per column
    for x in range(WIDTH):
        spread.append(BACKDROP)

    for index, colour in enumerate(pixels):
        red, green, blue = (min(255, int(channel)) for channel in colour)
        left = MARGIN + index * (CELL + GAP)
        for y in range(MARGIN, MARGIN + BAR_HEIGHT):
            row = rows[y]
            for x in range(left, left + CELL):
                row[x] = (red, green, blue)
        # The reflection is wider than the LED that casts it.
        for x in range(max(0, left - GAP), min(WIDTH, left + CELL + GAP)):
            spread[x] = (red, green, blue)

    top = MARGIN + BAR_HEIGHT
    for offset in range(SPILL_HEIGHT):
        # Fades out with distance, the way light on a surface does.
        fade = (1.0 - offset / float(SPILL_HEIGHT)) ** 2 * 0.42
        row = rows[top + offset]
        for x in range(WIDTH):
            red, green, blue = spread[x]
            # Rounded to steps of four: invisible on a band this faint, and
            # it turns a gradient full of near-misses into runs a compressor
            # can actually use.
            row[x] = tuple(
                int(BACKDROP[axis] + (channel - BACKDROP[axis]) * fade) & 0xFC
                for axis, channel in enumerate((red, green, blue)))
    return rows


def scanlines(rows):
    """Rows as PNG's filter-byte-then-pixels bytes, ready for zlib.

    Every row but the first is stored as its difference from the row above.
    The bar is twenty identical rows and the backdrop is flat, so almost the
    whole image becomes runs of zero - which is the difference between a
    preview that belongs in a repository and one that does not.
    """
    out = bytearray()
    previous = None
    for row in rows:
        flat = bytearray()
        for red, green, blue in row:
            flat.append(red)
            flat.append(green)
            flat.append(blue)
        if previous is None:
            out.append(0)               # filter: none
            out += flat
        else:
            out.append(2)               # filter: up
            out += bytes((flat[index] - previous[index]) & 0xFF
                         for index in range(len(flat)))
        previous = flat
    return bytes(out)


# -- animated PNG ----------------------------------------------------------


def chunk(kind, payload):
    body = kind + payload
    return (struct.pack(">I", len(payload)) + body
            + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))


def write_apng(path, frames, fps=FPS):
    """frames: a list of rasters. Loops forever."""
    delay_num, delay_den = 1, fps
    out = bytearray(b"\x89PNG\r\n\x1a\n")
    out += chunk(b"IHDR", struct.pack(">IIBBBBB", WIDTH, HEIGHT, 8, 2, 0, 0, 0))
    out += chunk(b"acTL", struct.pack(">II", len(frames), 0))

    sequence = 0
    for index, rows in enumerate(frames):
        out += chunk(b"fcTL", struct.pack(">IIIIIHHBB", sequence, WIDTH, HEIGHT,
                                          0, 0, delay_num, delay_den, 0, 0))
        sequence += 1
        data = zlib.compress(scanlines(rows), 9)
        if index == 0:
            out += chunk(b"IDAT", data)
        else:
            out += chunk(b"fdAT", struct.pack(">I", sequence) + data)
            sequence += 1
    out += chunk(b"IEND", b"")

    with open(path, "wb") as handle:
        handle.write(bytes(out))
    return len(out)


# -- what to draw ----------------------------------------------------------


class Scripted:
    """A sensor whose reading is whatever the capture is up to."""

    def __init__(self):
        self.temperature = 0.0
        self.load = (0.0, 0.0)

    def celsius(self, now=None):
        return self.temperature

    def fractions(self, now=None):
        return self.load


def renderer(**kwargs):
    return render.Renderer(led_count=LEDS, mapping=render.MAPPING_CROP, **kwargs)


def steam_effect(effect, seconds, colour=STEAM_COLOUR, **kwargs):
    """Frames of one Steam effect, spanning exactly `seconds` so it loops."""
    snapshot = shim.make_snapshot(effect, colour)
    engine = renderer(**kwargs)
    count = max(1, round(seconds * FPS))
    return [raster(engine.render_logical(snapshot, index * seconds / count))
            for index in range(count)]


def flash(shape, colour, duration=3.5, tail=0.6):
    """One notification, start to finish, with a beat of dark after it."""
    overlay = notify.NotificationOverlay(duration=duration, led_count=LEDS,
                                         style=shape)
    overlay.trigger(colour, 0.0)
    frames = []
    for index in range(round((duration + tail) * FPS)):
        payload = overlay.frame(index / float(FPS))
        frames.append(raster(
            [(payload[led * 3], payload[led * 3 + 1], payload[led * 3 + 2])
             for led in range(LEDS)] if payload else [(0, 0, 0)] * LEDS))
    return frames


def sweep(seconds, sensor, engine, walk):
    """Frames while `walk(fraction)` moves the scripted sensor."""
    snapshot = shim.make_snapshot(shim.EFFECT_RAINBOW)
    count = round(seconds * FPS)
    frames = []
    for index in range(count):
        walk(index / float(count))
        frames.append(raster(engine.render_logical(snapshot, 0.0)))
    return frames


def firmware_breath(colour, period_ms):
    """What the ESP draws alone. Its breath is the host's, by design."""
    count = round(period_ms / 1000.0 * FPS)
    frames = []
    for index in range(count):
        level = render.breath_envelope(index / float(count), 0.05)
        frames.append(raster([tuple(int(channel * level + 0.5)
                                    for channel in colour)] * LEDS))
    return frames


def build(out):
    sensor = Scripted()

    def warm(fraction):
        walk = fraction * 2.0
        sensor.temperature = 25.0 + (walk if walk <= 1.0 else 2.0 - walk) * 70.0

    # Idle, a menu, a game, and back - the GPU spikier than the CPU.
    steps = ((0.05, 0.02), (0.22, 0.10), (0.35, 0.62), (0.78, 0.96),
             (0.55, 0.88), (0.18, 0.09), (0.05, 0.02))

    def busy(fraction):
        place = fraction * (len(steps) - 1)
        first = min(int(place), len(steps) - 2)
        blend = place - first
        sensor.load = tuple(steps[first][axis]
                            + (steps[first + 1][axis] - steps[first][axis]) * blend
                            for axis in (0, 1))

    previews = {
        "rainbow": lambda: steam_effect(shim.EFFECT_RAINBOW,
                                        render.RAINBOW_CYCLE),
        "breath": lambda: steam_effect(shim.EFFECT_BREATH, render.BREATH_CYCLE),
        "patrol": lambda: steam_effect(shim.EFFECT_PATROL, render.PATROL_CYCLE),
        "factory": lambda: steam_effect(shim.EFFECT_FACTORY,
                                        render.FACTORY_INTERVAL * 4),
        "fire": lambda: steam_effect(shim.EFFECT_RAINBOW, 6.0,
                                     rainbow_shows=render.SHOWS_FIRE),
        "aurora": lambda: steam_effect(shim.EFFECT_RAINBOW, 9.0,
                                       rainbow_shows=render.SHOWS_AURORA),
        "temperature": lambda: sweep(8.0, sensor,
                                     renderer(temperature=sensor), warm),
        "load": lambda: sweep(8.0, sensor, renderer(load=sensor), busy),
        "startup": lambda: firmware_breath(service.STARTUP_COLOR,
                                           service.STARTUP_PERIOD_MS),
        "standby": lambda: firmware_breath(service.STANDBY_COLOR,
                                           service.STANDBY_PERIOD_MS),
    }
    for shape in notify.STYLES:
        colour = "#ff0000" if shape == notify.STYLE_ALTERNATE else "#1a9fff"
        previews["shape-" + shape.replace("_", "-")] = (
            lambda shape=shape, colour=colour: flash(shape, colour))

    if not os.path.isdir(out):
        os.makedirs(out)
    total = 0
    for name in sorted(previews):
        frames = previews[name]()
        path = os.path.join(out, name + ".png")
        size = write_apng(path, frames)
        total += size
        print("  %-22s %3d frames  %5.1f KB" % (name, len(frames), size / 1024.0))
    print("%d previews, %.0f KB" % (len(previews), total / 1024.0))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out",
                        default=os.path.join(HERE, "..", "docs", "previews"),
                        help="where to write the animations")
    build(parser.parse_args(argv).out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
