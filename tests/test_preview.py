# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""The panel's preview tab, minus the canvas.

The point of the tab is that what you see is what the strip will do, so the
thing worth testing is that the frames really come from render.py and
notify.py and really carry the settings in the window - not that a rectangle
ended up the right shade of orange.
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "server"))
sys.path.insert(0, os.path.join(HERE, "..", "gui"))

import preview  # noqa: E402
from steamos_led import config as config_module, notify, render, shim  # noqa: E402


class CatalogueTest(unittest.TestCase):
    """What the tab offers, and what it deliberately does not."""

    def setUp(self):
        self.entries = preview.entries()

    def test_every_effect_this_project_added_is_there(self):
        offered = {name for _label, _kind, name, _blurb in self.entries}
        # Everything the rainbow slot can hold except Steam's own rainbow.
        for name in config_module.RAINBOW_CHOICES:
            if name == render.SHOWS_RAINBOW:
                continue
            self.assertIn(name, offered, name)
        for style in notify.STYLES:
            self.assertIn(style, offered, style)

    def test_steams_own_effects_are_left_out(self):
        # They can be seen by picking them in Steam, and a preview of "static"
        # is a coloured rectangle.
        offered = {name for _label, _kind, name, _blurb in self.entries}
        self.assertNotIn(render.SHOWS_RAINBOW, offered)
        for name in shim.EFFECT_NAMES.values():
            if name in ("rainbow",):
                continue
            self.assertNotIn(name, offered, name)

    def test_the_two_breaths_are_left_out(self):
        # Standby and startup are states the machine puts itself into, not
        # settings anybody picks, so there is nothing to preview before.
        offered = {name for _label, _kind, name, _blurb in self.entries}
        self.assertNotIn("standby", offered)
        self.assertNotIn("startup", offered)

    def test_each_one_says_what_it_is(self):
        for label, kind, name, blurb in self.entries:
            self.assertTrue(label, name)
            self.assertIn(kind, ("slot", "shape"), name)
            self.assertTrue(blurb, "%s has no description" % name)

    def test_a_new_shape_turns_up_without_being_listed_here(self):
        # shape_effects() reads the service's own table, so the tab cannot
        # quietly fall behind it.
        self.assertEqual([style for _label, style, _blurb
                          in preview.shape_effects()], list(notify.STYLES))


class FrameTest(unittest.TestCase):
    def setUp(self):
        self.preview = preview.Preview()

    def _frames(self, kind, name, times):
        draw = (self.preview.slot_frame if kind == "slot"
                else lambda n, t: self.preview.shape_frame(n, "#1a9fff", t))
        return [draw(name, when) for when in times]

    def test_every_entry_draws_a_full_strip(self):
        for _label, kind, name, _blurb in preview.entries():
            for frame in self._frames(kind, name, (0.0, 1.3, 4.9)):
                self.assertEqual(len(frame), shim.LOGICAL_LEDS, name)
                for pixel in frame:
                    self.assertEqual(len(pixel), 3, name)

    def test_nothing_stands_still(self):
        # Only that it moves. double_flash is the floor and always will be:
        # whole bar, hard edges, so it has exactly two frames to its name and
        # that is the shape working, not a preview that failed to animate.
        for _label, kind, name, _blurb in preview.entries():
            frames = self._frames(kind, name,
                                  [tick * 0.05 for tick in range(60)])
            self.assertGreater(len({tuple(map(tuple, f)) for f in frames}), 1,
                               "%s never changes" % name)

    def test_the_gauges_are_walked_rather_than_left_at_one_reading(self):
        # A gauge showing one number would be a still picture of the least
        # interesting moment.
        # Half a sweep, so the walk out is counted without the mirrored walk
        # back landing on the same readings again. Not one distinct frame per
        # sample: the temperature scale is deliberately flat below its first
        # mark and above its second, so both ends of the walk repeat.
        for name in (render.SHOWS_TEMPERATURE, render.SHOWS_LOAD):
            step = preview.SWEEP_SECONDS / 2.0 / 12
            seen = {tuple(map(tuple, self.preview.slot_frame(name, tick * step)))
                    for tick in range(12)}
            self.assertGreater(len(seen), 4, name)

    def test_a_flash_ends_and_starts_again(self):
        # Looped with a pause, so a shape is seen to finish rather than
        # running into its own beginning.
        duration = config_module.DEFAULTS["NOTIFY_DURATION"]
        lit = [max(max(pixel) for pixel in
                   self.preview.shape_frame("bloom", "#1a9fff", tick / 10.0))
               for tick in range(int((duration + preview.FLASH_PAUSE) * 10) * 2)]

        # Lit, then dark, then lit again. Not "dark at the end" - the sampling
        # window ends inside the second pause, which is the effect working.
        first_dark = next(index for index, level in enumerate(lit)
                          if level == 0 and index > 5)
        self.assertLess(first_dark, len(lit) - 1, "the flash never ends")
        self.assertGreater(max(lit[first_dark:]), 200, "it never starts again")

    def test_the_repeat_gap_does_not_silence_the_loop(self):
        # The service's gap would show the shape once and then leave the strip
        # dark for ten seconds, which is not a preview.
        late = [max(max(pixel) for pixel in
                    self.preview.shape_frame("pulse", "#1a9fff", 12.0 + tick / 5.0))
                for tick in range(30)]
        self.assertGreater(max(late), 200)


class SettingsTest(unittest.TestCase):
    """The window's values reach the picture, which is why it is worth having."""

    def test_the_temperature_marks_move_the_colours(self):
        cool = preview.Preview({"TEMPERATURE_MIN": 20.0,
                                "TEMPERATURE_MAX": 90.0})
        tight = preview.Preview({"TEMPERATURE_MIN": 20.0,
                                 "TEMPERATURE_MAX": 30.0})
        when = preview.SWEEP_SECONDS / 4.0        # part way up the sweep
        self.assertNotEqual(cool.slot_frame(render.SHOWS_TEMPERATURE, when),
                            tight.slot_frame(render.SHOWS_TEMPERATURE, when))

    def test_the_flash_duration_changes_the_pacing(self):
        short = preview.Preview({"NOTIFY_DURATION": 1.0})
        long = preview.Preview({"NOTIFY_DURATION": 10.0})
        # Two seconds in, the short one has finished and looped; the long one
        # is still in the middle of its first flash.
        self.assertNotEqual(short.shape_frame("bloom", "#1a9fff", 2.0),
                            long.shape_frame("bloom", "#1a9fff", 2.0))

    def test_the_speed_setting_reaches_the_animations(self):
        slow = preview.Preview({"SPEED": 0.2})
        fast = preview.Preview({"SPEED": 4.0})
        self.assertNotEqual(slow.slot_frame(render.SHOWS_FIRE, 3.0),
                            fast.slot_frame(render.SHOWS_FIRE, 3.0))

    def test_a_missing_setting_falls_back_to_the_shipped_default(self):
        self.assertEqual(preview.Preview().setting("NOTIFY_DURATION"),
                         config_module.DEFAULTS["NOTIFY_DURATION"])

    def test_an_unreadable_window_still_draws(self):
        # The panel hands over whatever it could read; the rest is defaults.
        drawn = preview.Preview({}).slot_frame(render.SHOWS_AURORA, 1.0)
        self.assertEqual(len(drawn), shim.LOGICAL_LEDS)


class CanvasColourTest(unittest.TestCase):
    """A canvas has no alpha, so the glow is mixed here instead."""

    def test_a_pixel_becomes_a_colour_a_canvas_understands(self):
        self.assertEqual(preview.hex_colour((255, 110.4, 0)), "#ff6e00")

    def test_out_of_range_values_are_clamped_rather_than_wrapped(self):
        # render.py works in floats and does not promise 0..255.
        self.assertEqual(preview.hex_colour((300, -5, 0)), "#ff0000")

    def test_none_of_the_glow_is_the_bare_backdrop(self):
        self.assertEqual(preview.toward((0, 0, 0), 1.0), "#000000")
        self.assertEqual(preview.toward((255, 255, 255), 0.0),
                         preview.BACKDROP)

    def test_the_glow_sits_between_the_backdrop_and_the_led(self):
        # Towards the pixel, not upwards: the backdrop is a blue-ish grey, so
        # an orange LED's blue channel travels down to meet it.
        pixel = (255, 110, 0)
        steps = [preview.toward(pixel, amount)
                 for amount in (0.0, 0.25, 0.75, 1.0)]
        for index, channel in zip((1, 3, 5), pixel):
            walk = [int(step[index:index + 2], 16) for step in steps]
            rising = channel >= walk[0]
            self.assertEqual(walk, sorted(walk, reverse=not rising),
                             "channel %d does not move towards the LED" % index)


if __name__ == "__main__":
    unittest.main()
