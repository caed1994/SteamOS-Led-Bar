# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""The rainbow slot, and the effects that can stand in it.

Steam's LED menu cannot be extended, so everything of ours shares one entry.
That makes the slot itself worth testing as hard as the effects in it: a
choice that silently falls back to the rainbow, or one that keeps drawing
after its sensor stopped answering, is a bug nobody can see and everybody
would blame on the strip.
"""

import colorsys
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "server"))

from steamos_led import config, load, render, sampling, shim  # noqa: E402


class FakeLoad:
    """Something with .fractions(), which is all the renderer wants."""

    def __init__(self, cpu=0.5, gpu=0.5, missing=False):
        self.cpu, self.gpu, self.missing = cpu, gpu, missing

    def fractions(self, now=None):
        return None if self.missing else (self.cpu, self.gpu)


class FakeTemperature:
    def __init__(self, celsius=50.0):
        self.value = celsius

    def celsius(self, now=None):
        return self.value


def _renderer(**kwargs):
    return render.Renderer(led_count=shim.LOGICAL_LEDS,
                           mapping=render.MAPPING_CROP, **kwargs)


def _rainbow_snapshot():
    return shim.make_snapshot(shim.EFFECT_RAINBOW)


class SlotTest(unittest.TestCase):
    """Which effect the rainbow entry actually draws."""

    def test_the_default_leaves_steams_own_rainbow_alone(self):
        renderer = _renderer()
        self.assertEqual(renderer.rainbow_shows, render.SHOWS_RAINBOW)
        snapshot = _rainbow_snapshot()
        self.assertEqual(renderer.render_logical(snapshot, 0.0),
                         render._rainbow(snapshot, 0.0, renderer))

    def test_every_choice_the_config_offers_can_be_rendered(self):
        # The validator accepts these names, so each one has to reach a
        # renderer - a name that validates and then does nothing would leave
        # the rainbow in place with no error anywhere.
        sources = {render.SHOWS_TEMPERATURE: {"temperature": FakeTemperature()},
                   render.SHOWS_LOAD: {"load": FakeLoad()}}
        for name in config.RAINBOW_CHOICES:
            renderer = _renderer(rainbow_shows=name, **sources.get(name, {}))
            frame = renderer.render_logical(_rainbow_snapshot(), 1.0)
            self.assertEqual(len(frame), shim.LOGICAL_LEDS, name)

    def test_only_the_rainbow_entry_is_taken_over(self):
        # Every other effect Steam offers has to keep working untouched -
        # taking the rainbow is the price, and it is the whole price.
        renderer = _renderer(rainbow_shows=render.SHOWS_FIRE)
        for effect in (shim.EFFECT_BREATH, shim.EFFECT_PATROL,
                       shim.EFFECT_MANUAL, shim.EFFECT_DEMO):
            snapshot = shim.make_snapshot(effect)
            expected = render._EFFECTS[effect](snapshot, 0.5, renderer)
            self.assertEqual(renderer.render_logical(snapshot, 0.5), expected,
                             effect)

    def test_a_choice_whose_hardware_is_missing_falls_back(self):
        # Asking for the load gauge on a machine that has no counters must
        # not draw a dark strip: that looks like a service that has died.
        renderer = _renderer(rainbow_shows=render.SHOWS_LOAD)
        snapshot = _rainbow_snapshot()
        self.assertEqual(renderer.render_logical(snapshot, 0.0),
                         render._rainbow(snapshot, 0.0, renderer))

    def test_a_gauge_with_nothing_to_read_falls_back(self):
        renderer = _renderer(rainbow_shows=render.SHOWS_LOAD,
                             load=FakeLoad(missing=True))
        snapshot = _rainbow_snapshot()
        self.assertEqual(renderer.render_logical(snapshot, 0.0),
                         render._rainbow(snapshot, 0.0, renderer))
        self.assertTrue(renderer.is_animated(snapshot),
                        "a rainbow underneath still has to animate")

    def test_an_unknown_choice_is_refused_at_construction(self):
        with self.assertRaises(ValueError):
            _renderer(rainbow_shows="kaleidoscope")

    def test_handing_over_a_source_alone_still_means_the_gauge(self):
        # How the temperature gauge was switched on before there was a
        # choice, and what every caller that predates one still does.
        self.assertEqual(_renderer(temperature=FakeTemperature()).rainbow_shows,
                         render.SHOWS_TEMPERATURE)
        self.assertEqual(_renderer(load=FakeLoad()).rainbow_shows,
                         render.SHOWS_LOAD)


class Stepping(load.LoadSource):
    """A real source whose counters jump from idle to a game and stay there.

    Real enough to carry the smoothing, which is the thing under test - a fake
    that just hands back a number would be testing the fake.
    """

    def __init__(self, **kwargs):
        super().__init__(stat_path="/nonexistent", gpu_pattern="/nonexistent/*",
                         **kwargs)
        self._resolved = True           # do not go looking at the real /sys

    def _sample_cpu(self):
        return 0.05 if (self._taken or 0.0) < 0.5 else 0.85


class FrameRateTest(unittest.TestCase):
    """Which scenes have something new to draw on the next frame."""

    def test_the_temperature_gauge_is_not_driven_at_the_frame_rate(self):
        # It redraws when the sensor moves, which is far slower than a frame.
        renderer = _renderer(temperature=FakeTemperature())
        self.assertFalse(renderer.is_animated(_rainbow_snapshot()))

    def test_the_load_gauge_is(self):
        # It glides towards each reading instead of stepping onto it, so at
        # the idle rate the glide would be four jumps a second.
        renderer = _renderer(load=Stepping())
        self.assertTrue(renderer.is_animated(_rainbow_snapshot()))

    def test_fire_and_aurora_are(self):
        for name in (render.SHOWS_FIRE, render.SHOWS_AURORA):
            renderer = _renderer(rainbow_shows=name)
            self.assertTrue(renderer.is_animated(_rainbow_snapshot()), name)


class LoadGlideTest(unittest.TestCase):
    """The bar walks to a new reading rather than jumping onto it."""

    HALF = shim.LOGICAL_LEDS // 2

    def _walk(self, fps):
        """How far the CPU bar sits along its half, frame by frame, in LEDs."""
        source = Stepping()
        return [(source.fractions(now=tick / float(fps))[0] or 0.0) * self.HALF
                for tick in range(int(2.0 * fps))]

    def _biggest_step(self, walk):
        return max(abs(b - a) for a, b in zip(walk, walk[1:]))

    def test_no_frame_moves_the_bar_more_than_a_fifth_of_an_LED(self):
        # The whole complaint: at the idle rate one reading moved it nearly
        # two and a half LEDs at once, which reads as a jump rather than a
        # meter.
        self.assertLess(self._biggest_step(self._walk(60)), 0.2)

    def test_drawing_it_faster_does_not_make_it_arrive_later(self):
        # Only the granularity changed, not the pace. Drawn at four frames a
        # second or at sixty, the bar is within a quarter of an LED of the
        # same place two seconds in.
        self.assertAlmostEqual(self._walk(4)[-1], self._walk(60)[-1], delta=0.25)

    def test_it_gets_most_of_the_way_there_in_two_seconds(self):
        # Softer, not slower: a meter that lags behind the thing you just
        # started is not showing you the thing you just started.
        self.assertGreater(self._walk(60)[-1], 0.85 * self.HALF * 0.7)

    def test_every_frame_of_the_glide_has_something_new_to_draw(self):
        # Which is what earns it the full frame rate. Sampled while it is
        # actually moving - once arrived it holds still, as it should.
        gliding = self._walk(60)[31:71]
        self.assertTrue(all(b > a for a, b in zip(gliding, gliding[1:])))


class LoadGaugeTest(unittest.TestCase):
    """Two bars growing out of the middle, CPU one way and GPU the other."""

    def _frame(self, cpu, gpu):
        renderer = _renderer(load=FakeLoad(cpu, gpu))
        return renderer.render_logical(_rainbow_snapshot(), 0.0)

    def _lit(self, frame):
        """Which LEDs are lit past the always-on floor, as a set."""
        return {index for index, pixel in enumerate(frame)
                if max(pixel) > 255 * render.LOAD_FLOOR + 1}

    def test_the_middle_led_separates_the_two_sides(self):
        # Seventeen LEDs, so there is an odd one out. It belongs to neither
        # reading, and a lit one would make the halves read as a single bar.
        frame = self._frame(1.0, 1.0)
        self.assertEqual(frame[shim.LOGICAL_LEDS // 2], (0.0, 0.0, 0.0))

    def test_full_load_lights_everything_but_that(self):
        lit = self._lit(self._frame(1.0, 1.0))
        self.assertEqual(lit, set(range(shim.LOGICAL_LEDS))
                         - {shim.LOGICAL_LEDS // 2})

    def test_each_side_grows_from_the_middle_outwards(self):
        # Not from the ends inwards: the reading has to start where the eye
        # already is, or a quiet machine puts its only lit LEDs in the corners.
        half = shim.LOGICAL_LEDS // 2
        lit = self._lit(self._frame(0.3, 0.3))
        self.assertIn(half - 1, lit, "the CPU side starts beside the middle")
        self.assertIn(half + 1, lit, "and so does the GPU side")
        self.assertNotIn(0, lit)
        self.assertNotIn(shim.LOGICAL_LEDS - 1, lit)

    def test_the_two_sides_are_told_apart_by_colour(self):
        frame = self._frame(1.0, 1.0)
        half = shim.LOGICAL_LEDS // 2
        self.assertEqual(frame[half - 1], render.LOAD_CPU_COLOUR)
        self.assertEqual(frame[half + 1], render.LOAD_GPU_COLOUR)

    def test_an_idle_machine_still_shows_something(self):
        # Nothing lit at all is indistinguishable from a strip that has been
        # switched off, which is the one thing a meter must never look like.
        frame = self._frame(0.0, 0.0)
        self.assertTrue(max(max(pixel) for pixel in frame) > 0)

    def test_the_bar_is_fractional_rather_than_whole_leds(self):
        # Eight LEDs a side would otherwise only ever show eighths.
        half = shim.LOGICAL_LEDS // 2
        frame = self._frame(1.5 / half, 0.0)
        self.assertAlmostEqual(frame[half - 2][0] / render.LOAD_CPU_COLOUR[0],
                               0.5, places=6)

    def test_more_load_never_means_less_light(self):
        previous = -1.0
        for percent in range(0, 101, 5):
            total = sum(sum(pixel) for pixel in self._frame(percent / 100.0, 0.0))
            self.assertGreaterEqual(total, previous, percent)
            previous = total

    def test_without_a_gpu_counter_the_cpu_is_drawn_on_both_halves(self):
        # Rather than leaving one side dark, which reads as a broken strip
        # rather than as a machine whose driver does not publish the number.
        frame = self._frame(0.5, None)
        half = shim.LOGICAL_LEDS // 2
        for offset in range(half):
            left = frame[half - 1 - offset]
            right = frame[half + 1 + offset]
            self.assertAlmostEqual(left[0] / render.LOAD_CPU_COLOUR[0],
                                   right[2] / render.LOAD_GPU_COLOUR[2],
                                   places=6, msg=offset)


class FireTest(unittest.TestCase):
    def _renderer(self):
        return _renderer(rainbow_shows=render.SHOWS_FIRE)

    def test_it_moves(self):
        renderer = self._renderer()
        snapshot = _rainbow_snapshot()
        frames = {tuple(renderer.render_logical(snapshot, tick * 0.2))
                  for tick in range(20)}
        self.assertGreater(len(frames), 15, "a fire that repeats is a pattern")

    def test_it_stays_in_the_warm_half_of_the_spectrum(self):
        # What makes it read as flame rather than as a rainbow with a filter:
        # red leads, and blue only ever arrives with the white-hot tips.
        renderer = self._renderer()
        snapshot = _rainbow_snapshot()
        for tick in range(60):
            for red, green, blue in renderer.render_logical(snapshot, tick * 0.1):
                self.assertGreaterEqual(red, green, tick)
                self.assertGreaterEqual(green, blue, tick)

    def test_the_same_moment_draws_the_same_frame(self):
        # A function of time, not a random draw - so a dropped frame resumes
        # where it would have been instead of jumping.
        first, second = self._renderer(), self._renderer()
        snapshot = _rainbow_snapshot()
        self.assertEqual(first.render_logical(snapshot, 3.7),
                         second.render_logical(snapshot, 3.7))

    def test_the_strip_is_never_dark(self):
        renderer = self._renderer()
        snapshot = _rainbow_snapshot()
        for tick in range(40):
            for pixel in renderer.render_logical(snapshot, tick * 0.15):
                self.assertGreater(max(pixel), 0.0)


class AuroraTest(unittest.TestCase):
    def _renderer(self):
        return _renderer(rainbow_shows=render.SHOWS_AURORA)

    def _hues(self, snapshot, ticks=200):
        renderer = self._renderer()
        hues = []
        for tick in range(ticks):
            for red, green, blue in renderer.render_logical(snapshot, tick * 0.1):
                hues.append(colorsys.rgb_to_hsv(red / 255.0, green / 255.0,
                                                blue / 255.0)[0])
        return hues

    def test_it_keeps_to_green_through_violet(self):
        # The restraint is the whole effect. Let it reach the warm half of the
        # circle and it stops being an aurora and becomes a slow rainbow -
        # which already exists, and is the one people turn off.
        hues = self._hues(_rainbow_snapshot())
        self.assertGreaterEqual(min(hues), 0.30, "wandered towards yellow")
        self.assertLessEqual(max(hues), 0.80, "wandered towards red")

    def test_steams_colour_slider_still_moves_it(self):
        # It takes the rainbow's place, so the control that shifted the
        # rainbow has to keep doing something.
        plain = _rainbow_snapshot()
        shifted = shim.make_snapshot(shim.EFFECT_RAINBOW, color_shift=128)
        self.assertNotEqual(self._renderer().render_logical(plain, 1.0),
                            self._renderer().render_logical(shifted, 1.0))

    def test_it_is_slower_than_the_rainbow_it_replaces(self):
        self.assertGreater(render.AURORA_CYCLE, render.RAINBOW_CYCLE)

    def test_the_curtain_thins_but_never_goes_out(self):
        renderer = self._renderer()
        snapshot = _rainbow_snapshot()
        for tick in range(60):
            for pixel in renderer.render_logical(snapshot, tick * 0.2):
                self.assertGreater(max(pixel), 0.0)


class ColourScaleTest(unittest.TestCase):
    """The shared stop-mixing both the temperature scale and fire use."""

    STOPS = ((0.0, (0.0, 0.0, 0.0)), (1.0, (100.0, 200.0, 50.0)))

    def test_the_ends_are_held_rather_than_extrapolated(self):
        self.assertEqual(render.blend_stops(-5.0, self.STOPS), self.STOPS[0][1])
        self.assertEqual(render.blend_stops(99.0, self.STOPS), self.STOPS[1][1])

    def test_halfway_is_halfway(self):
        self.assertEqual(render.blend_stops(0.5, self.STOPS),
                         (50.0, 100.0, 25.0))

    def test_marks_on_top_of_each_other_do_not_divide_by_zero(self):
        stops = ((1.0, (10.0, 0.0, 0.0)), (1.0, (0.0, 10.0, 0.0)))
        self.assertEqual(render.blend_stops(1.0, stops), stops[0][1])
        self.assertEqual(render.blend_stops(2.0, stops), stops[-1][1])

    def test_the_fire_palette_is_in_order(self):
        marks = [mark for mark, _colour in render.FIRE_STOPS]
        self.assertEqual(marks, sorted(marks))
        self.assertEqual(len(set(marks)), len(marks))


class CpuCountersTest(unittest.TestCase):
    """Reading /proc/stat, which reports totals and expects the arithmetic."""

    def _stat(self, *fields):
        handle = tempfile.NamedTemporaryFile("w", suffix=".stat", delete=False)
        self.addCleanup(os.unlink, handle.name)
        handle.write("cpu  %s\n" % " ".join(str(field) for field in fields))
        handle.write("cpu0 1 2 3 4 5 6 7\n")
        handle.close()
        return handle.name

    def test_idle_and_iowait_both_count_as_not_working(self):
        # user nice system idle iowait: 30 busy, 70 not.
        path = self._stat(20, 0, 10, 60, 10)
        self.assertEqual(load.read_cpu_totals(path), (30, 100))

    def test_a_missing_file_is_not_a_crash(self):
        self.assertIsNone(load.read_cpu_totals("/nonexistent/proc/stat"))

    def test_the_first_reading_is_only_a_baseline(self):
        # Since-boot totals would show a machine that has been up for a week
        # at its week-long average, which is never what it is doing now.
        source = load.LoadSource(interval=0.0, smoothing=0.0,
                                 stat_path=self._stat(20, 0, 10, 60, 10),
                                 gpu_pattern="/nonexistent/*")
        self.assertIsNone(source.fractions(now=0.0))

    def test_the_second_reading_is_the_load_between_the_two(self):
        first = self._stat(20, 0, 10, 60, 10)
        source = load.LoadSource(interval=0.0, smoothing=0.0, stat_path=first,
                                 gpu_pattern="/nonexistent/*")
        source.fractions(now=0.0)
        source.stat_path = self._stat(60, 0, 30, 100, 10)   # 60 of 100 busy
        cpu, gpu = source.fractions(now=1.0)
        self.assertAlmostEqual(cpu, 0.6)
        self.assertIsNone(gpu)

    def test_a_counter_that_did_not_move_reports_nothing(self):
        path = self._stat(20, 0, 10, 60, 10)
        source = load.LoadSource(interval=0.0, smoothing=0.0, stat_path=path,
                                 gpu_pattern="/nonexistent/*")
        source.fractions(now=0.0)
        self.assertIsNone(source.fractions(now=1.0))


class GpuCounterTest(unittest.TestCase):
    def _busy(self, text):
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory)
        path = os.path.join(directory, "gpu_busy_percent")
        with open(path, "w") as handle:
            handle.write(text)
        return directory, path

    def test_a_percentage_becomes_a_fraction(self):
        _directory, path = self._busy("42\n")
        self.assertAlmostEqual(load.read_gpu_percent(path), 0.42)

    def test_a_driver_without_one_is_not_an_error(self):
        self.assertIsNone(load.find_gpu_busy("/nonexistent/card*/busy"))
        self.assertIsNone(load.read_gpu_percent("/nonexistent/busy"))

    def test_nonsense_is_refused_rather_than_shown(self):
        _directory, path = self._busy("unknown\n")
        self.assertIsNone(load.read_gpu_percent(path))

    def test_it_is_found_by_pattern(self):
        directory, path = self._busy("7\n")
        found = load.find_gpu_busy(os.path.join(directory, "gpu_*"))
        self.assertEqual(found, path)


class SmoothingTest(unittest.TestCase):
    """Shared by both gauges, so it is tested once and used twice."""

    def test_a_first_reading_is_taken_as_it_is(self):
        self.assertEqual(sampling.smooth(None, 0.5, 1.0, 2.0), 0.5)

    def test_a_sensor_that_stopped_answering_is_not_remembered(self):
        self.assertIsNone(sampling.smooth(0.5, None, 1.0, 2.0))

    def test_it_moves_part_of_the_way(self):
        moved = sampling.smooth(0.0, 1.0, 1.0, 1.0)
        self.assertAlmostEqual(moved, 0.5)

    def test_a_longer_gap_moves_further(self):
        # Sized by elapsed time, so a skipped frame does not change how
        # quickly a gauge follows.
        self.assertGreater(sampling.smooth(0.0, 1.0, 4.0, 1.0),
                           sampling.smooth(0.0, 1.0, 1.0, 1.0))

    def test_switching_it_off_reports_the_sample(self):
        self.assertEqual(sampling.smooth(0.0, 1.0, 1.0, 0.0), 1.0)


if __name__ == "__main__":
    unittest.main()
