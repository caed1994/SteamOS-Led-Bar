"""Finding and reading a temperature sensor, against a made-up /sys tree.

Picking the right sensor is the whole job: a machine reports a dozen, and most
of them measure something nobody means by "how hot is it".
"""

import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "server"))

from steamos_led import render, shim, temperature  # noqa: E402


class FakeHwmon:
    """Builds the corner of /sys that hwmon lives in."""

    def __init__(self, root):
        self.root = root
        self.count = 0

    def chip(self, name, inputs):
        """inputs: [(label or None, millidegrees or None)]"""
        while os.path.exists(os.path.join(self.root, "hwmon%d" % self.count)):
            self.count += 1        # a second builder on the same tree
        directory = os.path.join(self.root, "hwmon%d" % self.count)
        self.count += 1
        os.makedirs(directory)
        with open(os.path.join(directory, "name"), "w") as handle:
            handle.write(name + "\n")
        for number, (label, value) in enumerate(inputs, 1):
            if label is not None:
                with open(os.path.join(directory, "temp%d_label" % number),
                          "w") as handle:
                    handle.write(label + "\n")
            if value is not None:
                with open(os.path.join(directory, "temp%d_input" % number),
                          "w") as handle:
                    handle.write("%d\n" % value)
        return directory


class SensorPickingTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.hwmon = FakeHwmon(self.root)

    def _pick(self):
        return temperature.pick_sensor(temperature.find_sensors(self.root))

    def test_the_cpu_is_preferred_over_the_ssd(self):
        # An NVMe drive is a real temperature and the wrong answer.
        self.hwmon.chip("nvme", [("Composite", 41000)])
        self.hwmon.chip("k10temp", [("Tctl", 52000)])
        self.assertEqual(self._pick()["chip"], "k10temp")

    def test_a_well_labelled_wrong_chip_does_not_win(self):
        # "Composite" is a preferred label, but on the wrong chip. The chip has
        # to decide first, or a tidy SSD beats an untidy CPU.
        self.hwmon.chip("nvme", [("Composite", 41000)])
        self.hwmon.chip("k10temp", [(None, 52000)])
        self.assertEqual(self._pick()["chip"], "k10temp")

    def test_the_package_sensor_wins_within_a_chip(self):
        self.hwmon.chip("k10temp", [("Tccd1", 49000), ("Tctl", 52000)])
        self.assertEqual(self._pick()["label"], "Tctl")

    def test_the_gpu_is_used_when_there_is_no_cpu_sensor(self):
        self.hwmon.chip("iwlwifi_1", [(None, 35000)])
        self.hwmon.chip("amdgpu", [("edge", 47000)])
        self.assertEqual(self._pick()["chip"], "amdgpu")

    def test_something_unknown_is_better_than_nothing(self):
        self.hwmon.chip("mystery", [(None, 44000)])
        self.assertEqual(self._pick()["chip"], "mystery")

    def test_a_machine_with_no_sensors_reports_none(self):
        self.assertIsNone(self._pick())

    def test_a_chip_with_no_temperature_inputs_is_skipped(self):
        self.hwmon.chip("fan_only", [])
        self.assertEqual(temperature.find_sensors(self.root), [])


class ReadingTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.hwmon = FakeHwmon(self.root)

    def test_thousandths_become_degrees(self):
        # Reporting 52000 unconverted would peg any gauge at maximum forever.
        directory = self.hwmon.chip("k10temp", [("Tctl", 52000)])
        self.assertEqual(
            temperature.read_celsius(os.path.join(directory, "temp1_input")),
            52.0)

    def test_a_missing_file_reads_as_nothing(self):
        self.assertIsNone(temperature.read_celsius(
            os.path.join(self.root, "not-there")))

    def test_rubbish_in_the_file_reads_as_nothing(self):
        path = os.path.join(self.root, "temp1_input")
        with open(path, "w") as handle:
            handle.write("n/a\n")
        self.assertIsNone(temperature.read_celsius(path))

    def test_a_sensor_that_disappears_stops_reporting(self):
        # Sensors do come and go - an eGPU unplugged, a driver unloaded.
        directory = self.hwmon.chip("k10temp", [("Tctl", 52000)])
        path = os.path.join(directory, "temp1_input")
        source = temperature.TemperatureSource(interval=0, root=self.root)
        self.assertEqual(source.celsius(), 52.0)
        os.unlink(path)
        self.assertIsNone(source.celsius())


class CachingTest(unittest.TestCase):
    """The render loop runs at 60 fps; a CPU warms up over seconds."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        hwmon = FakeHwmon(self.root)
        self.directory = hwmon.chip("k10temp", [("Tctl", 50000)])
        self.path = os.path.join(self.directory, "temp1_input")
        self.source = temperature.TemperatureSource(interval=1.0,
                                                    root=self.root)

    def _set(self, millidegrees):
        with open(self.path, "w") as handle:
            handle.write("%d\n" % millidegrees)

    def test_a_reading_is_kept_for_the_interval(self):
        self.assertEqual(self.source.celsius(now=100.0), 50.0)
        self._set(80000)
        self.assertEqual(self.source.celsius(now=100.5), 50.0,
                         "it should not have looked again yet")

    def test_it_looks_again_once_the_reading_is_stale(self):
        self.assertEqual(self.source.celsius(now=100.0), 50.0)
        self._set(80000)
        self.assertEqual(self.source.celsius(now=101.5), 80.0)

    def test_the_sensor_is_only_resolved_once(self):
        self.source.celsius(now=100.0)
        first = self.source.path
        # Even if the tree changes underneath, the choice stands - swapping
        # sensor halfway through would make the bar jump for no visible reason.
        FakeHwmon(self.root).chip("amdgpu", [("edge", 90000)])
        self.source.celsius(now=200.0)
        self.assertEqual(self.source.path, first)

    def test_an_explicit_path_is_used_as_given(self):
        source = temperature.TemperatureSource(path=self.path, root=self.root)
        self.assertEqual(source.celsius(), 50.0)
        self.assertEqual(source.path, self.path)


class FakeSource:
    """A thermometer that reads whatever the test says."""

    def __init__(self, value=None):
        self.value = value

    def celsius(self, now=None):
        return self.value


class GaugeTest(unittest.TestCase):
    """What the bar shows: fills as it warms, green at the bottom, red at the top."""

    LOW, HIGH = 40.0, 85.0

    def setUp(self):
        self.source = FakeSource()
        # One physical LED per logical one, so the frame is the gauge itself
        # and not an interpolation of it.
        self.renderer = render.Renderer(led_count=shim.LOGICAL_LEDS,
                                        mapping=render.MAPPING_CROP,
                                        temperature=self.source,
                                        temperature_range=(self.LOW, self.HIGH))
        self.snapshot = shim.make_snapshot(shim.EFFECT_RAINBOW)

    def _frame(self, celsius):
        self.source.value = celsius
        return self.renderer.render_logical(self.snapshot, 0.0)

    def _lit(self, celsius):
        return sum(1 for pixel in self._frame(celsius) if max(pixel) > 127)

    def test_a_cool_machine_leaves_the_bar_dark(self):
        self.assertEqual(self._lit(25.0), 0)

    def test_nothing_is_lit_at_the_lower_mark_itself(self):
        self.assertEqual(self._lit(self.LOW), 0)

    def test_the_bar_is_full_at_the_upper_mark(self):
        self.assertEqual(self._lit(self.HIGH), shim.LOGICAL_LEDS)

    def test_running_hotter_than_the_upper_mark_stays_full(self):
        # Not wrapped round to empty, and not overflowing into the next effect.
        self.assertEqual(self._lit(120.0), shim.LOGICAL_LEDS)

    def test_halfway_fills_about_half(self):
        lit = self._lit((self.LOW + self.HIGH) / 2.0)
        self.assertAlmostEqual(lit, shim.LOGICAL_LEDS / 2.0, delta=1)

    def test_it_fills_monotonically(self):
        counts = [self._lit(value) for value in range(30, 100, 5)]
        self.assertEqual(counts, sorted(counts))

    def test_it_fills_from_the_first_led(self):
        frame = self._frame(50.0)
        lit = [index for index, pixel in enumerate(frame) if max(pixel) > 127]
        self.assertEqual(lit, list(range(len(lit))))

    def test_the_leading_led_fades_in(self):
        # A whole LED is nearly three degrees at this range; stepping a notch
        # at a time would make a warming machine look jumpy.
        # At these three readings LED 3 is the leading one, partly lit.
        levels = [max(self._frame(value)[3]) for value in (48.0, 48.5, 49.0)]
        self.assertEqual(levels, sorted(levels))
        self.assertNotEqual(levels[0], levels[-1])

    def test_it_starts_green_and_ends_red(self):
        cool = self._frame(self.LOW + 0.5)[0]
        hot = self._frame(self.HIGH)[0]
        self.assertGreater(cool[1], cool[0], "the cool end should be green")
        self.assertGreater(hot[0], hot[1], "the hot end should be red")

    def test_the_colour_walks_from_green_to_red(self):
        # Through yellow and orange, which is the sequence everyone reads as
        # "getting worse" - so red rises and green falls the whole way.
        reds, greens = [], []
        for value in range(45, 90, 5):
            red, green, _blue = self._frame(float(value))[0]
            reds.append(round(red))
            greens.append(round(green))
        self.assertEqual(reds, sorted(reds))
        self.assertEqual(greens, sorted(greens, reverse=True))

    def test_no_sensor_falls_back_to_the_rainbow(self):
        # A dark strip would look like the service had died.
        self.source.value = None
        frame = self.renderer.render_logical(self.snapshot, 0.0)
        self.assertEqual(frame,
                         render._rainbow(self.snapshot, 0.0, self.renderer))

    def test_only_the_rainbow_slot_is_taken_over(self):
        self.source.value = 90.0
        for effect in (shim.EFFECT_BREATH, shim.EFFECT_PATROL,
                       shim.EFFECT_MANUAL):
            snapshot = shim.make_snapshot(effect, (0, 0, 255))
            frame = self.renderer.render_logical(snapshot, 0.0)
            self.assertEqual(frame[0][0], 0.0,
                             "effect %d should be untouched" % effect)

    def test_the_rainbow_is_a_rainbow_when_the_gauge_is_off(self):
        renderer = render.Renderer(led_count=shim.LOGICAL_LEDS,
                                   mapping=render.MAPPING_CROP)
        frame = renderer.render_logical(self.snapshot, 0.0)
        self.assertEqual(frame, render._rainbow(self.snapshot, 0.0, renderer))

    def test_an_upside_down_range_does_not_divide_by_zero(self):
        # validate() rejects this, but the renderer is also used directly.
        renderer = render.Renderer(led_count=shim.LOGICAL_LEDS,
                                   mapping=render.MAPPING_CROP,
                                   temperature=FakeSource(60.0),
                                   temperature_range=(85.0, 85.0))
        renderer.render_logical(self.snapshot, 0.0)


if __name__ == "__main__":
    unittest.main()
