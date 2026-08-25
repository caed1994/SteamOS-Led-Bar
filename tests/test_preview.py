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

import ledpanel  # noqa: E402
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


class PhysicalStripTest(unittest.TestCase):
    """It draws the strip you have, not the seventeen Steam works in.

    The seventeen logical LEDs are what the effects are composed on, but they
    are not what is on the desk - and the setting that turns one into the
    other is the mapping, the hardest thing on the Strip page to picture.
    """

    def _frame(self, **settings):
        preview_ = preview.Preview(settings)
        return preview_.slot_frame(render.SHOWS_AURORA, 2.0)

    def test_the_strip_is_as_long_as_the_setting_says(self):
        for count in (1, 17, 30, 60):
            self.assertEqual(len(self._frame(LED_COUNT=count)), count)
            self.assertEqual(
                len(preview.Preview({"LED_COUNT": count}).shape_frame(
                    "bloom", "#1a9fff", 0.4)), count)

    def test_the_mapping_is_the_one_in_the_window(self):
        # Sixty LEDs stretched is a gradient; cropped it is seventeen lit and
        # the rest dark. A preview drawn at seventeen could show neither.
        stretched = self._frame(LED_COUNT=60, MAPPING="stretch")
        cropped = self._frame(LED_COUNT=60, MAPPING="crop")
        self.assertNotEqual(stretched, cropped)
        self.assertEqual(cropped[40], (0, 0, 0))
        self.assertNotEqual(stretched[40], (0, 0, 0))

    def test_the_brightness_ceiling_reaches_both_halves(self):
        # Both, now that both go through the service's own path - the flashes
        # honoured it and the effects did not, which was the one setting the
        # preview could not be trusted on.
        for frame in (lambda cap: self._frame(MAX_BRIGHTNESS=cap),
                      lambda cap: preview.Preview(
                          {"MAX_BRIGHTNESS": cap}).shape_frame(
                              "pulse", "#ffffff", 0.9)):
            full = max(max(pixel) for pixel in frame(255))
            capped = max(max(pixel) for pixel in frame(60))
            self.assertLess(capped, full, "the ceiling did not reach it")

    def test_reversing_the_strip_reverses_the_picture(self):
        forward = self._frame(LED_COUNT=30, REVERSE=False)
        backward = self._frame(LED_COUNT=30, REVERSE=True)
        self.assertEqual(forward, list(reversed(backward)))

    def test_a_strip_of_one_led_still_draws(self):
        # The validator allows it, so the preview has to survive it.
        self.assertEqual(len(self._frame(LED_COUNT=1)), 1)


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


class RendererReuseTest(unittest.TestCase):
    """The preview asks for a frame 25 times a second, on a live window.

    A renderer builds a 256 entry gamma table and its own interpolation
    weights. Built per frame, at GAMMA=2.2 that was most of the frame it was
    built for - so it is kept until a setting it was built from moves.
    """

    def test_a_still_window_builds_one_renderer(self):
        page = preview.Preview({"LED_COUNT": 17, "GAMMA": 2.2})
        first = page._renderer(render.SHOWS_FIRE)
        for tick in range(50):
            page.slot_frame(render.SHOWS_FIRE, tick / 25.0)
        self.assertIs(page._renderer(render.SHOWS_FIRE), first,
                      "nothing changed, so nothing had to be rebuilt")

    def test_a_moved_slider_is_picked_up_at_once(self):
        # The window writes its live values in before every frame, so a kept
        # renderer must not outlive the settings it was built from.
        page = preview.Preview({"LED_COUNT": 17, "GAMMA": 1.0})
        self.assertEqual(len(page.slot_frame(render.SHOWS_FIRE, 1.0)), 17)
        page.settings = {"LED_COUNT": 60, "GAMMA": 1.0}
        self.assertEqual(len(page.slot_frame(render.SHOWS_FIRE, 1.0)), 60)

    def test_each_slot_keeps_its_own(self):
        # Switching what the tab shows is a different renderer, not the same
        # one asked a different question.
        page = preview.Preview({"LED_COUNT": 17})
        fire = page._renderer(render.SHOWS_FIRE)
        self.assertIsNot(page._renderer(render.SHOWS_AURORA), fire)

    def test_a_gauge_colour_is_a_setting_it_was_built_from(self):
        # Kept renderers are the trap: picking a colour and seeing the preview
        # go on drawing the old one is exactly what a stale cache looks like.
        page = preview.Preview({"LED_COUNT": 17,
                                "LOAD_CPU_COLOR": "#ff6e00"})
        first = page._renderer(render.SHOWS_LOAD)
        page.settings = {"LED_COUNT": 17, "LOAD_CPU_COLOR": "#00ff00"}
        self.assertIsNot(page._renderer(render.SHOWS_LOAD), first)


    def test_the_sensor_readings_still_move(self):
        # slot_frame writes into the sensor object the renderer holds, so a
        # kept renderer has to keep seeing the walk rather than one reading.
        page = preview.Preview({"LED_COUNT": 17})
        frames = {tuple(page.slot_frame(render.SHOWS_TEMPERATURE, t / 25.0))
                  for t in range(0, 400, 20)}
        self.assertGreater(len(frames), 1, "the gauge should be walked")


class LoadPreviewTest(unittest.TestCase):
    """What the Preview tab draws for the load gauge, in the colours set.

    The tab is where the two menus are judged - a colour picked on the Strip
    page and previewed in the shipped one would be a preview of somebody
    else's setting.
    """

    HALF = 17 // 2

    def _sides(self, **settings):
        page = preview.Preview(dict({"LED_COUNT": 17, "MAPPING": "repeat",
                                     "MAX_BRIGHTNESS": 255}, **settings))
        # A moment into the sweep, where LOAD_WALK has both halves well lit.
        pixels = page.slot_frame(render.SHOWS_LOAD,
                                 preview.SWEEP_SECONDS * 0.5)
        return pixels[self.HALF - 1], pixels[self.HALF + 1]

    def _hue(self, pixel):
        """Which channel leads, which is all these assertions need."""
        return max(range(3), key=lambda index: pixel[index])

    def test_the_two_halves_come_out_in_the_colours_that_were_set(self):
        cpu, gpu = self._sides(LOAD_CPU_COLOR="#00ff00",
                               LOAD_GPU_COLOR="#0000ff")
        self.assertEqual(self._hue(cpu), 1, cpu)        # green leads
        self.assertEqual(self._hue(gpu), 2, gpu)        # blue leads

    def test_the_shipped_pair_still_draws_amber_and_blue(self):
        cpu, gpu = self._sides()
        self.assertEqual(self._hue(cpu), 0, cpu)        # amber leads on red
        self.assertEqual(self._hue(gpu), 2, gpu)

    def test_the_blurb_does_not_name_a_colour_that_is_a_setting(self):
        """Caught on a screenshot: the line under the stage still said

        "CPU left in amber, GPU right in blue" while the bar it sat under was
        green and violet. Which side is which does not move; which colour is
        which does, so the blurb says the half that is fixed.
        """
        blurb = next(text for _label, name, text in preview.SLOT_EFFECTS
                     if name == render.SHOWS_LOAD)
        for colour, _value in ledpanel.load_colours():
            self.assertNotIn(colour.lower(), blurb.lower(), blurb)
        self.assertIn("left", blurb.lower())
        self.assertIn("right", blurb.lower())

    def test_a_half_typed_colour_does_not_stop_the_preview(self):
        """It redraws twenty-five times a second while somebody types.

        Read leniently, the way every other setting on this page is: a colour
        mid-edit is not the moment to raise, and the shipped one standing in
        is what the strip would do anyway.
        """
        cpu, _gpu = self._sides(LOAD_CPU_COLOR="#00ff")
        self.assertEqual(self._hue(cpu), 0, "it did not fall back")

if __name__ == "__main__":
    unittest.main()
