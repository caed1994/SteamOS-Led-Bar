# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

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

    def chip(self, name, inputs, limits=None, alarms=None):
        """inputs: [(label or None, millidegrees or None)]

        limits and alarms are keyed by sensor number: {1: {"crit": 100000}}
        and {1: {"crit_alarm": 1}}, spelled in the same millidegrees and flags
        that hwmon uses.
        """
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
            for name, reading in (limits or {}).get(number, {}).items():
                with open(os.path.join(directory,
                                       "temp%d_%s" % (number, name)),
                          "w") as handle:
                    handle.write("%d\n" % reading)
            for name, flag in (alarms or {}).get(number, {}).items():
                with open(os.path.join(directory,
                                       "temp%d_%s" % (number, name)),
                          "w") as handle:
                    handle.write("%d\n" % flag)
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


class PublishedLimitsTest(unittest.TestCase):
    """What a sensor says about itself.

    "Hot" is not one number across a machine: an APU at 95 C is doing what it
    was designed to do, an NVMe drive at 95 C is long past its critical point.
    The parts publish their own limits, so a threshold does not have to be
    guessed - where they publish anything, which is not everywhere.
    """

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.hwmon = FakeHwmon(self.root)

    def _input(self, directory, number=1):
        return os.path.join(directory, "temp%d_input" % number)

    def test_limits_are_read_in_degrees(self):
        directory = self.hwmon.chip(
            "k10temp", [("Tctl", 52000)],
            limits={1: {"crit": 100000, "max": 95000}})
        self.assertEqual(temperature.read_limits(self._input(directory)),
                         {"crit": 100.0, "max": 95.0})

    def test_a_sensor_that_publishes_nothing_reports_nothing(self):
        directory = self.hwmon.chip("iwlwifi_1", [(None, 35000)])
        self.assertEqual(temperature.read_limits(self._input(directory)), {})

    def test_only_the_files_that_exist_come_back(self):
        # hwmon is optional throughout - a driver exposes what the hardware
        # tells it, so a missing file is the normal case, not a fault.
        directory = self.hwmon.chip("nvme", [("Composite", 41000)],
                                    limits={1: {"crit": 84850}})
        self.assertEqual(temperature.read_limits(self._input(directory)),
                         {"crit": 84.85})

    def test_each_sensor_of_a_chip_has_its_own(self):
        directory = self.hwmon.chip(
            "k10temp", [("Tctl", 52000), ("Tccd1", 49000)],
            limits={1: {"crit": 100000}, 2: {"crit": 90000}})
        self.assertEqual(temperature.read_limits(self._input(directory, 1)),
                         {"crit": 100.0})
        self.assertEqual(temperature.read_limits(self._input(directory, 2)),
                         {"crit": 90.0})

    def test_rubbish_is_skipped_rather_than_reported(self):
        directory = self.hwmon.chip("mystery", [(None, 44000)])
        with open(os.path.join(directory, "temp1_crit"), "w") as handle:
            handle.write("n/a\n")
        self.assertEqual(temperature.read_limits(self._input(directory)), {})

    def test_a_raised_alarm_is_reported(self):
        # The kernel's own opinion that a limit has been passed, which beats
        # any threshold we would pick.
        directory = self.hwmon.chip("amdgpu", [("edge", 97000)],
                                    alarms={1: {"crit_alarm": 1}})
        self.assertEqual(temperature.read_alarms(self._input(directory)),
                         ["crit_alarm"])

    def test_a_quiet_alarm_is_not(self):
        directory = self.hwmon.chip("amdgpu", [("edge", 47000)],
                                    alarms={1: {"crit_alarm": 0}})
        self.assertEqual(temperature.read_alarms(self._input(directory)), [])

    def test_no_alarm_files_at_all_is_quiet_too(self):
        directory = self.hwmon.chip("k10temp", [("Tctl", 52000)])
        self.assertEqual(temperature.read_alarms(self._input(directory)), [])

    def test_reading_limits_does_not_disturb_the_sensor_list(self):
        # find_sensors() feeds the gauge and the panel's drop-down; the limits
        # are a separate question and must not change what those two see.
        self.hwmon.chip("k10temp", [("Tctl", 52000)],
                        limits={1: {"crit": 100000}})
        sensors = temperature.find_sensors(self.root)
        self.assertEqual(len(sensors), 1)
        self.assertEqual(sorted(sensors[0]),
                         ["chip", "label", "path", "rank"])


class OverheatWatchTest(unittest.TestCase):
    """Watching every sensor for one that stays close to its own limit.

    The thresholds are never written here: they come from what each part
    publishes about itself, which is the only way one rule can be right on an
    APU, an NVMe drive and a DDR5 module at the same time.
    """

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.hwmon = FakeHwmon(self.root)

    def _watch(self, **kwargs):
        return temperature.OverheatWatch(root=self.root, **kwargs)

    def _set(self, directory, millidegrees, number=1):
        with open(os.path.join(directory,
                               "temp%d_input" % number), "w") as handle:
            handle.write("%d\n" % millidegrees)

    # -- what gets watched, and at what temperature -----------------------

    def test_the_threshold_comes_from_the_part(self):
        self.hwmon.chip("amdgpu", [("edge", 40000)],
                        limits={1: {"crit": 110000}})
        watched = self._watch(margin=5.0).resolve()
        self.assertEqual(len(watched), 1)
        self.assertAlmostEqual(watched[0][1], 105.0)

    def test_two_parts_get_two_thresholds(self):
        # The point of the whole design: one number cannot serve both.
        self.hwmon.chip("amdgpu", [("edge", 40000)],
                        limits={1: {"crit": 110000}})
        self.hwmon.chip("nvme", [("Composite", 40000)],
                        limits={1: {"crit": 84850}})
        thresholds = sorted(round(threshold, 2)
                            for _sensor, threshold in self._watch().resolve())
        self.assertEqual(thresholds, [79.85, 105.0])

    def test_a_sensor_with_no_limit_is_not_watched(self):
        # k10temp publishes nothing on a real machine, and an APU at its
        # limit is working as designed - so guessing one would warn all day.
        self.hwmon.chip("k10temp", [("Tctl", 95000)])
        self.assertEqual(self._watch().resolve(), [])

    def test_max_is_not_a_threshold(self):
        # A DDR5 module reports max 55 with crit 85; thresholding on max would
        # warn about a warm room. An Ethernet chip reports max 120 and no crit.
        self.hwmon.chip("r8169", [(None, 44000)], limits={1: {"max": 120000}})
        self.assertEqual(self._watch().resolve(), [])

    def test_crit_is_preferred_to_emergency(self):
        self.hwmon.chip("amdgpu", [("edge", 40000)],
                        limits={1: {"crit": 110000, "emergency": 115000}})
        self.assertAlmostEqual(self._watch(margin=5.0).resolve()[0][1], 105.0)

    def test_emergency_serves_when_there_is_no_crit(self):
        self.hwmon.chip("amdgpu", [("edge", 40000)],
                        limits={1: {"emergency": 115000}})
        self.assertAlmostEqual(self._watch(margin=5.0).resolve()[0][1], 110.0)

    def test_a_disabled_threshold_is_not_a_limit(self):
        # 0xFFFF Kelvin is how NVMe spells "not implemented", and it reaches
        # sysfs as 65261.85 C. Watching for that would watch for nothing.
        self.hwmon.chip("nvme", [("Sensor 1", 63900)],
                        limits={1: {"max": 65261850, "crit": 65261850}})
        self.assertEqual(self._watch().resolve(), [])

    # -- when it warns ----------------------------------------------------

    def _hot_chip(self):
        return self.hwmon.chip("amdgpu", [("edge", 40000)],
                               limits={1: {"crit": 110000}})

    def test_below_the_threshold_is_silence(self):
        directory = self._hot_chip()
        watch = self._watch(dwell=60.0, interval=0)
        self._set(directory, 104000)
        for step in range(10):
            self.assertIsNone(watch.poll(step * 30.0))

    def test_a_spike_is_not_a_warning(self):
        # A chip touches its limit whenever it boosts. That is not a fault,
        # and warning about it is how the bar stops meaning anything.
        directory = self._hot_chip()
        watch = self._watch(dwell=60.0, interval=0)
        self._set(directory, 106000)
        self.assertIsNone(watch.poll(0.0))
        self._set(directory, 80000)
        self.assertIsNone(watch.poll(5.0))
        self._set(directory, 106000)
        self.assertIsNone(watch.poll(10.0), "the minute starts again")
        self.assertIsNone(watch.poll(65.0))

    def test_staying_there_is(self):
        directory = self._hot_chip()
        watch = self._watch(dwell=60.0, interval=0)
        self._set(directory, 106000)
        self.assertIsNone(watch.poll(0.0))
        self.assertIsNone(watch.poll(59.0))
        self.assertIsNotNone(watch.poll(61.0))

    def test_the_warning_says_what_and_how_hot(self):
        # The bar can only say "something is too hot"; the answer to "what"
        # has to be in the log or the warning is not actionable.
        directory = self._hot_chip()
        watch = self._watch(dwell=60.0, interval=0)
        self._set(directory, 106500)
        watch.poll(0.0)
        reason = watch.poll(61.0)
        self.assertIn("amdgpu", reason)
        self.assertIn("edge", reason)
        self.assertIn("106.5", reason)

    def test_it_does_not_warn_twice_for_the_same_heat(self):
        directory = self._hot_chip()
        watch = self._watch(dwell=60.0, interval=0, quiet=0.0)
        self._set(directory, 106000)
        watch.poll(0.0)
        self.assertIsNotNone(watch.poll(61.0))
        self.assertIsNone(watch.poll(200.0), "still hot is not news again")

    def test_it_warns_again_after_cooling_down_properly(self):
        directory = self._hot_chip()
        watch = self._watch(dwell=60.0, release=5.0, interval=0, quiet=0.0)
        self._set(directory, 106000)
        watch.poll(0.0)
        self.assertIsNotNone(watch.poll(61.0))
        self._set(directory, 99000)             # below 105 - 5
        watch.poll(100.0)
        self._set(directory, 106000)
        watch.poll(120.0)
        self.assertIsNotNone(watch.poll(181.0))

    def test_wobbling_on_the_line_does_not_re_arm_it(self):
        directory = self._hot_chip()
        watch = self._watch(dwell=60.0, release=5.0, interval=0, quiet=0.0)
        self._set(directory, 106000)
        watch.poll(0.0)
        self.assertIsNotNone(watch.poll(61.0))
        self._set(directory, 104000)            # under the line, but only just
        watch.poll(100.0)
        self._set(directory, 106000)
        watch.poll(120.0)
        self.assertIsNone(watch.poll(181.0))

    def test_a_second_sensor_still_has_to_wait_its_turn(self):
        # The bar cannot say more than "something is too hot", so two hot
        # parts are not two warnings a minute apart.
        first = self._hot_chip()
        second = self.hwmon.chip("nvme", [("Composite", 40000)],
                                 limits={1: {"crit": 90000}})
        watch = self._watch(dwell=60.0, interval=0, quiet=300.0)
        self._set(first, 106000)
        self._set(second, 86000)
        watch.poll(0.0)
        self.assertIsNotNone(watch.poll(61.0))
        self.assertIsNone(watch.poll(120.0), "one warning is the message")
        self.assertIsNotNone(watch.poll(400.0), "and later it may say so again")

    def test_a_sensor_that_stops_answering_starts_over(self):
        directory = self._hot_chip()
        watch = self._watch(dwell=60.0, interval=0)
        self._set(directory, 106000)
        watch.poll(0.0)
        os.unlink(os.path.join(directory, "temp1_input"))
        watch.poll(30.0)
        self._set(directory, 106000)
        watch.poll(40.0)
        self.assertIsNone(watch.poll(95.0), "the minute restarts on its return")
        self.assertIsNotNone(watch.poll(101.0))

    def test_nothing_to_watch_is_never_a_warning(self):
        self.hwmon.chip("k10temp", [("Tctl", 99000)])
        watch = self._watch(dwell=0.0, interval=0)
        self.assertIsNone(watch.poll(0.0))
        self.assertIsNone(watch.poll(1000.0))

    def test_it_reads_no_more_often_than_its_interval(self):
        # It sits in the render loop, which runs at up to 60 frames a second.
        directory = self._hot_chip()
        watch = self._watch(dwell=0.0, interval=5.0)
        self._set(directory, 106000)
        self.assertIsNotNone(watch.poll(0.0))
        os.unlink(os.path.join(directory, "temp1_input"))
        self.assertIsNone(watch.poll(1.0), "it must not have looked again")


class CachingTest(unittest.TestCase):
    """The render loop runs at 60 fps; a CPU warms up over seconds."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        hwmon = FakeHwmon(self.root)
        self.directory = hwmon.chip("k10temp", [("Tctl", 50000)])
        self.path = os.path.join(self.directory, "temp1_input")
        # Smoothing off here: this is about when the file is read, and it has
        # its own tests below.
        self.source = temperature.TemperatureSource(interval=1.0, smoothing=0,
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


class SmoothingTest(unittest.TestCase):
    """A CPU sensor is noisy enough to make the leading LED flicker.

    Tctl moves a degree or two between one second and the next on an idle
    machine, which over the gauge's span is most of an LED - so what the
    gauge is handed is an average, not the latest sample.
    """

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        hwmon = FakeHwmon(self.root)
        directory = hwmon.chip("k10temp", [("Tctl", 50000)])
        self.path = os.path.join(directory, "temp1_input")
        self.source = temperature.TemperatureSource(interval=1.0,
                                                    smoothing=6.0,
                                                    root=self.root)

    def _set(self, celsius):
        with open(self.path, "w") as handle:
            handle.write("%d\n" % int(celsius * 1000))

    def test_the_first_reading_is_not_smoothed_against_nothing(self):
        # Starting from zero would send the bar climbing from empty on every
        # service restart.
        self.assertEqual(self.source.celsius(now=0.0), 50.0)

    def test_a_jump_is_followed_gradually(self):
        self.source.celsius(now=0.0)
        self._set(80.0)
        moved = self.source.celsius(now=1.0)
        self.assertGreater(moved, 50.0)
        self.assertLess(moved, 60.0)

    def test_it_gets_there_in_the_end(self):
        self.source.celsius(now=0.0)
        self._set(80.0)
        for second in range(1, 60):
            value = self.source.celsius(now=float(second))
        self.assertAlmostEqual(value, 80.0, delta=0.5)

    def test_sensor_noise_barely_moves_the_reading(self):
        # A degree of jitter each way, which is what an idle Tctl does.
        self.source.celsius(now=0.0)
        seen = []
        for second in range(1, 40):
            self._set(50.0 + (1.0 if second % 2 else -1.0))
            seen.append(self.source.celsius(now=float(second)))
        self.assertLess(max(seen) - min(seen), 0.5,
                        "the gauge should not chase the noise")

    def test_a_longer_gap_moves_it_further(self):
        # Sized by elapsed time, so a slower loop follows at the same speed
        # rather than lagging further behind.
        self.source.celsius(now=0.0)
        self._set(80.0)
        slow = self.source.celsius(now=6.0)

        other = temperature.TemperatureSource(interval=1.0, smoothing=6.0,
                                              root=self.root)
        self._set(50.0)
        other.celsius(now=0.0)
        self._set(80.0)
        quick = other.celsius(now=1.0)
        self.assertGreater(slow, quick)

    def test_smoothing_can_be_switched_off(self):
        source = temperature.TemperatureSource(interval=0, smoothing=0,
                                               root=self.root)
        self.assertEqual(source.celsius(now=0.0), 50.0)
        self._set(80.0)
        self.assertEqual(source.celsius(now=1.0), 80.0)

    def test_a_sensor_that_stops_answering_is_not_remembered(self):
        # Holding the last average would leave a plausible-looking bar lit
        # for a sensor that is no longer there.
        self.source.celsius(now=0.0)
        os.unlink(self.path)
        self.assertIsNone(self.source.celsius(now=1.0))


class FakeSource:
    """A thermometer that reads whatever the test says."""

    def __init__(self, value=None):
        self.value = value

    def celsius(self, now=None):
        return self.value


LAST = shim.LOGICAL_LEDS - 1        # the end the gauge fills from


class GaugeTest(unittest.TestCase):
    """What the bar shows: the whole strip in one colour, green to red.

    Not a filling gauge any more. A bar that is part dark says two things at
    once - its colour and its length - and only one of them was ever the
    answer to "how hot is it".
    """

    def setUp(self):
        self.source = FakeSource()
        # One physical LED per logical one, so the frame is the gauge itself
        # and not an interpolation of it.
        self.renderer = render.Renderer(led_count=shim.LOGICAL_LEDS,
                                        mapping=render.MAPPING_CROP,
                                        temperature=self.source)
        self.snapshot = shim.make_snapshot(shim.EFFECT_RAINBOW)

    def _frame(self, celsius):
        self.source.value = celsius
        return self.renderer.render_logical(self.snapshot, 0.0)

    # -- the whole bar, whatever the reading ------------------------------

    def test_every_led_is_lit_when_cool(self):
        frame = self._frame(20.0)
        self.assertEqual(len(frame), shim.LOGICAL_LEDS)
        self.assertTrue(all(max(pixel) > 127 for pixel in frame))

    def test_every_led_is_lit_when_hot(self):
        self.assertTrue(all(max(pixel) > 127 for pixel in self._frame(95.0)))

    def test_the_whole_bar_is_one_colour(self):
        for value in (20.0, 45.0, 60.0, 72.0, 95.0):
            frame = self._frame(value)
            self.assertEqual(len(set(frame)), 1, "%.0f C is not even" % value)

    # -- and the colour is the message ------------------------------------

    def test_cool_is_green(self):
        red, green, blue = self._frame(20.0)[0]
        self.assertEqual((red, green, blue), (0.0, 255.0, 0.0))

    def test_it_is_still_green_at_the_first_mark(self):
        self.assertEqual(self._frame(40.0)[0], (0.0, 255.0, 0.0))

    def test_the_middle_mark_is_yellow(self):
        self.assertEqual(self._frame(60.0)[0], (255.0, 255.0, 0.0))

    def test_the_top_mark_is_red(self):
        self.assertEqual(self._frame(80.0)[0], (255.0, 0.0, 0.0))

    def test_hotter_than_the_top_mark_stays_red(self):
        # Not wrapped round to green, which a hue that keeps turning would do.
        self.assertEqual(self._frame(120.0)[0], (255.0, 0.0, 0.0))

    def test_colder_than_the_first_mark_stays_green(self):
        self.assertEqual(self._frame(-10.0)[0], (0.0, 255.0, 0.0))

    def test_red_only_ever_rises_and_green_only_ever_falls(self):
        # Which is what makes it readable without a legend: one direction,
        # the whole way, through yellow and orange.
        reds, greens = [], []
        for value in range(20, 100, 2):
            red, green, _blue = self._frame(float(value))[0]
            reds.append(round(red))
            greens.append(round(green))
        self.assertEqual(reds, sorted(reds))
        self.assertEqual(greens, sorted(greens, reverse=True))

    def test_blue_stays_out_of_it(self):
        for value in range(20, 100, 5):
            self.assertEqual(self._frame(float(value))[0][2], 0.0)

    def test_the_colour_moves_between_the_marks(self):
        # Half a degree is below what anyone sees, but the point is that the
        # scale is continuous rather than three steps.
        distinct = {self._frame(float(value) / 2)[0]
                    for value in range(82, 160)}
        self.assertGreater(len(distinct), 30)

    def test_halfway_up_a_leg_is_halfway_between_its_ends(self):
        self.assertEqual(self._frame(50.0)[0], (127.5, 255.0, 0.0))
        self.assertEqual(self._frame(70.0)[0], (255.0, 127.5, 0.0))

    # -- and everything around it -----------------------------------------

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

    def test_the_marks_are_in_order_and_span_something(self):
        # The colour lookup walks them in pairs, so an unsorted table would
        # divide by a negative and read backwards.
        marks = [mark for mark, _colour in render.temperature_stops(40.0, 80.0)]
        self.assertEqual(marks, sorted(marks))
        self.assertEqual(len(set(marks)), len(marks))

    def test_yellow_lands_halfway_between_the_marks(self):
        stops = render.temperature_stops(30.0, 90.0)
        self.assertEqual(stops[1][0], 60.0)
        self.assertEqual(stops[1][1], render.TEMPERATURE_WARM)

    def test_the_marks_move_the_whole_scale(self):
        # Set narrower and the same reading is further along: at 50 C a
        # 40..80 scale is barely warm and a 35..65 one is already yellow.
        wide = render.temperature_colour(50.0, 40.0, 80.0)
        narrow = render.temperature_colour(50.0, 35.0, 65.0)
        self.assertGreater(narrow[0], wide[0], "red should have risen further")

    def test_marks_with_no_span_do_not_divide_by_zero(self):
        # validate() keeps them apart, but the renderer is also used directly
        # - and a crash here would be in the render loop, not at startup.
        # Collapsed marks leave no room to fade, so the bar simply steps from
        # one end to the other at the mark.
        self.assertEqual(render.temperature_colour(50.0, 60.0, 60.0),
                         render.TEMPERATURE_COOL)
        self.assertEqual(render.temperature_colour(60.0, 60.0, 60.0),
                         render.TEMPERATURE_COOL)
        self.assertEqual(render.temperature_colour(70.0, 60.0, 60.0),
                         render.TEMPERATURE_HOT)


class GaugeFrameRateTest(unittest.TestCase):
    """The gauge redraws when the sensor moves, not sixty times a second.

    Steam calls the rainbow animated, and the gauge sits in its slot - so the
    main loop drove it at the full frame rate while the sensor answered once a
    second. Every frame in between was the same bytes, rendered and sent again.
    """

    def setUp(self):
        self.source = FakeSource(62.5)
        self.renderer = render.Renderer(led_count=shim.LOGICAL_LEDS,
                                        mapping=render.MAPPING_CROP,
                                        temperature=self.source)
        self.snapshot = shim.make_snapshot(shim.EFFECT_RAINBOW)

    def test_a_second_of_frames_is_one_picture(self):
        frames = {bytes(self.renderer.render(self.snapshot, tick / 60.0))
                  for tick in range(60)}
        self.assertEqual(len(frames), 1, "the gauge does not animate")

    def test_the_gauge_is_not_treated_as_animated(self):
        self.assertTrue(self.snapshot.is_animated, "Steam says it is")
        self.assertFalse(self.renderer.is_animated(self.snapshot),
                         "but the gauge in its place is not")

    def test_without_a_sensor_the_rainbow_underneath_still_animates(self):
        # The fallback really is a rainbow, and running that at the idle rate
        # would make it stutter.
        self.source.value = None
        self.assertTrue(self.renderer.is_animated(self.snapshot))

    def test_the_other_effects_are_unaffected(self):
        for effect in (shim.EFFECT_BREATH, shim.EFFECT_PATROL,
                       shim.EFFECT_DEMO):
            snapshot = shim.make_snapshot(effect)
            self.assertTrue(self.renderer.is_animated(snapshot), effect)
        self.assertFalse(
            self.renderer.is_animated(shim.make_snapshot(shim.EFFECT_MANUAL)))

    def test_a_renderer_without_a_gauge_just_asks_the_snapshot(self):
        plain = render.Renderer(led_count=shim.LOGICAL_LEDS)
        for effect in (shim.EFFECT_RAINBOW, shim.EFFECT_MANUAL,
                       shim.EFFECT_PATROL, shim.EFFECT_OFF):
            snapshot = shim.make_snapshot(effect)
            self.assertEqual(plain.is_animated(snapshot),
                             snapshot.is_animated, effect)


if __name__ == "__main__":
    unittest.main()
