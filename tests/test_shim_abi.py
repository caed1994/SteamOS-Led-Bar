# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""Checks shim.py and render.py against the vendored kernel module source.

The Python side hardcodes the snapshot layout and the meaning of the tuning
fields. Now that the module lives in this repository, that agreement can be
verified instead of trusted - if a future update of leds-valve-shim.c changes
the ABI, this fails instead of quietly mis-decoding pixels.
"""

import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "server"))

from steamos_led import render, shim  # noqa: E402

SOURCE = os.path.join(HERE, "..", "leds-valve-shim", "leds-valve-shim.c")
SCALAR_SIZES = {"u8": 1, "u16": 2, "u32": 4, "u64": 8}


def _source():
    with open(SOURCE) as handle:
        return handle.read()


def _define(source, name, base=10):
    match = re.search(r"#define\s+%s\s+(0x[0-9a-fA-F]+|\d+)" % name, source)
    if match is None:
        raise AssertionError("#define %s not found in %s" % (name, SOURCE))
    return int(match.group(1), 0 if base == 16 else 10)


class ShimAbiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = _source()
        cls.leds = _define(cls.source, "VALVE_NUM_LEDS")

        body = re.search(r"struct valve_leds_snapshot \{(.*?)\} __packed;",
                         cls.source, re.S)
        assert body, "struct valve_leds_snapshot not found"
        fields = re.findall(
            r"^\s*(u\d+|struct valve_leds_pixel)\s+(\w+)(\[[^\]]+\])?;",
            body.group(1), re.M)
        assert fields, "could not parse the struct body"

        cls.offsets = {}
        offset = 0
        for ctype, name, array in fields:
            cls.offsets[name] = offset
            if ctype == "struct valve_leds_pixel":
                offset += 4 * cls.leds   # r, g, b, brightness
            else:
                offset += SCALAR_SIZES[ctype]
            del array
        cls.total = offset

    def test_snapshot_size(self):
        self.assertEqual(shim.SNAPSHOT_SIZE, self.total)

    def test_pixels_start_after_the_header(self):
        self.assertEqual(shim.HEADER_SIZE, self.offsets["pixels"])

    def test_led_count(self):
        self.assertEqual(shim.LOGICAL_LEDS, self.leds)

    def test_magic(self):
        self.assertEqual(shim.MAGIC,
                         _define(self.source, "VALVE_LEDS_UAPI_MAGIC", base=16))

    def test_field_order_matches_the_struct(self):
        # struct.Struct in shim.py encodes this order; a reshuffle upstream
        # would silently swap effect and brightness.
        expected = ["magic", "version", "size", "seq", "monotonic_ns",
                    "enabled", "effect", "brightness_scale", "delay",
                    "breath_offset", "breath_level", "patrol_num",
                    "color_shift", "pixels"]
        self.assertEqual(sorted(self.offsets, key=self.offsets.get), expected)

    def test_effect_numbers(self):
        enum = re.search(r"enum valve_leds_effect \{(.*?)\};", self.source, re.S)
        self.assertIsNotNone(enum)
        names = [entry.strip().split()[0].replace("VALVE_LEDS_EFFECT_", "").lower()
                 for entry in enum.group(1).split(",") if entry.strip()]
        self.assertEqual(names,
                         [shim.EFFECT_NAMES[value] for value in range(len(names))])

    def test_delay_reference_matches_the_module_default(self):
        # render.py scales the animation cycles relative to this value, so it
        # has to be the default the module actually reports.
        self.assertEqual(render.DELAY_DEFAULT,
                         _define(self.source, "VALVE_DELAY_DEFAULT"))

    def test_delay_range(self):
        self.assertEqual(render.DELAY_MAX,
                         _define(self.source, "VALVE_DELAY_RANGE_MAX"))
        self.assertEqual(_define(self.source, "VALVE_DELAY_RANGE_MIN"), 0)


if __name__ == "__main__":
    unittest.main()
