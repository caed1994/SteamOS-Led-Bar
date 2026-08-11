"""Unit tests: python3 -m unittest discover -s tests

Everything here is stdlib-only, so it runs on a stock SteamOS image.
"""

import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "server"))

from steamos_led import config, link, render, service, shim  # noqa: E402


class TestCrc(unittest.TestCase):
    def test_known_vector(self):
        # CRC-16/CCITT-FALSE check value.
        self.assertEqual(link.crc16(b"123456789"), 0x29B1)

    def test_empty(self):
        self.assertEqual(link.crc16(b""), 0xFFFF)


class TestFraming(unittest.TestCase):
    def test_roundtrip(self):
        frame = link.build(link.MSG_FRAME, b"\x11\x00" + b"\xAA" * 51)
        parser = link.FrameParser()
        messages = parser.feed(frame)
        self.assertEqual(len(messages), 1)
        msg_type, payload = messages[0]
        self.assertEqual(msg_type, link.MSG_FRAME)
        self.assertEqual(payload, b"\x11\x00" + b"\xAA" * 51)

    def test_split_across_reads(self):
        frame = link.build(link.MSG_INFO, b"\x01\x2c\x01\x02esp")
        parser = link.FrameParser()
        self.assertEqual(parser.feed(frame[:3]), [])
        self.assertEqual(parser.feed(frame[3:7]), [])
        messages = parser.feed(frame[7:])
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0][0], link.MSG_INFO)

    def test_resync_after_garbage(self):
        frame = link.build(link.MSG_PONG)
        parser = link.FrameParser()
        messages = parser.feed(b"\x00\xA5\xFF garbage \xA5" + frame)
        self.assertEqual([msg for msg, _ in messages], [link.MSG_PONG])

    def test_corrupt_crc_is_dropped(self):
        frame = bytearray(link.build(link.MSG_FRAME, b"\x01\x00\x10\x20\x30"))
        frame[-1] ^= 0xFF
        self.assertEqual(link.FrameParser().feed(bytes(frame)), [])

    def test_back_to_back_frames(self):
        stream = link.build(link.MSG_PING) + link.build(link.MSG_BLANK)
        messages = link.FrameParser().feed(stream)
        self.assertEqual([msg for msg, _ in messages],
                         [link.MSG_PING, link.MSG_BLANK])

    def test_device_info(self):
        info = link.DeviceInfo.parse(b"\x01\x2c\x01\x02esp8266-led-client")
        self.assertEqual(info.protocol, 1)
        self.assertEqual(info.max_leds, 300)
        self.assertEqual(info.pin, 2)
        self.assertEqual(info.name, "esp8266-led-client")


class TestSnapshot(unittest.TestCase):
    def test_encode_parse_roundtrip(self):
        original = shim.make_snapshot(shim.EFFECT_PATROL, (10, 20, 30),
                                      brightness=128, delay=25, patrol_num=2)
        raw = shim.encode(original, seq=7)
        self.assertEqual(len(raw), shim.SNAPSHOT_SIZE)

        parsed = shim.parse(raw)
        self.assertEqual(parsed.seq, 7)
        self.assertEqual(parsed.effect, shim.EFFECT_PATROL)
        self.assertEqual(parsed.brightness_scale, 128)
        self.assertEqual(parsed.delay, 25)
        self.assertEqual(parsed.patrol_num, 2)
        self.assertEqual(parsed.pixels[0], (10, 20, 30, 255))
        self.assertEqual(len(parsed.pixels), shim.LOGICAL_LEDS)

    def test_rejects_bad_magic(self):
        raw = bytearray(shim.encode(shim.make_snapshot()))
        raw[0] ^= 0xFF
        with self.assertRaises(shim.SnapshotError):
            shim.parse(bytes(raw))

    def test_rejects_short_buffer(self):
        with self.assertRaises(shim.SnapshotError):
            shim.parse(b"\x00" * 12)

    def test_animated_flag(self):
        self.assertTrue(shim.make_snapshot(shim.EFFECT_RAINBOW).is_animated)
        self.assertFalse(shim.make_snapshot(shim.EFFECT_MANUAL).is_animated)
        self.assertFalse(
            shim.make_snapshot(shim.EFFECT_RAINBOW, enabled=0).is_animated)

    def test_base_color_skips_black(self):
        snapshot = shim.make_snapshot(shim.EFFECT_BREATH, (0, 0, 0))
        snapshot.pixels[3] = (0, 200, 100, 255)
        self.assertEqual(snapshot.base_color(), (0, 200, 100))


class TestRenderer(unittest.TestCase):
    def test_payload_length_matches_strip(self):
        renderer = render.Renderer(led_count=60)
        payload = renderer.render(shim.make_snapshot(), 0.0)
        self.assertEqual(len(payload), 60 * 3)

    def test_disabled_is_black(self):
        renderer = render.Renderer(led_count=17)
        snapshot = shim.make_snapshot(shim.EFFECT_MANUAL, (255, 0, 0), enabled=0)
        self.assertEqual(renderer.render(snapshot, 0.0), bytes(17 * 3))

    def test_effect_off_is_black(self):
        renderer = render.Renderer(led_count=17)
        snapshot = shim.make_snapshot(shim.EFFECT_OFF, (255, 255, 255))
        self.assertEqual(renderer.render(snapshot, 0.0), bytes(17 * 3))

    def test_manual_colour_passes_through(self):
        renderer = render.Renderer(led_count=17)
        snapshot = shim.make_snapshot(shim.EFFECT_MANUAL, (10, 20, 30))
        payload = renderer.render(snapshot, 0.0)
        self.assertEqual(payload[:3], bytes((10, 20, 30)))
        self.assertEqual(payload[-3:], bytes((10, 20, 30)))

    def test_brightness_scale_applies(self):
        renderer = render.Renderer(led_count=1, mapping=render.MAPPING_CROP)
        snapshot = shim.make_snapshot(shim.EFFECT_MANUAL, (200, 100, 0),
                                      brightness=128)
        payload = renderer.render(snapshot, 0.0)
        self.assertEqual(payload[0], round(200 * 128 / 255))

    def test_max_brightness_clamps(self):
        renderer = render.Renderer(led_count=1, mapping=render.MAPPING_CROP,
                                   max_brightness=64)
        snapshot = shim.make_snapshot(shim.EFFECT_MANUAL, (255, 255, 255))
        self.assertEqual(renderer.render(snapshot, 0.0)[0], 64)

    def test_min_brightness_floor(self):
        renderer = render.Renderer(led_count=1, mapping=render.MAPPING_CROP,
                                   min_brightness=255)
        snapshot = shim.make_snapshot(shim.EFFECT_MANUAL, (80, 0, 0),
                                      brightness=0)
        self.assertEqual(renderer.render(snapshot, 0.0)[0], 80)

    def test_reverse_flips_strip(self):
        snapshot = shim.make_snapshot(shim.EFFECT_MANUAL)
        snapshot.pixels[0] = (255, 0, 0, 255)
        forward = render.Renderer(led_count=17, mapping=render.MAPPING_CROP)
        backward = render.Renderer(led_count=17, mapping=render.MAPPING_CROP,
                                   reverse=True)
        self.assertEqual(forward.render(snapshot, 0.0)[:3], bytes((255, 0, 0)))
        self.assertEqual(backward.render(snapshot, 0.0)[-3:], bytes((255, 0, 0)))

    @staticmethod
    def _progress_snapshot(filled):
        """What Steam writes while a download runs: the first LEDs lit."""
        snapshot = shim.make_snapshot(shim.EFFECT_MANUAL, (0, 0, 0))
        snapshot.pixels = [(0, 150, 255, 255) if led < filled else (0, 0, 0, 0)
                           for led in range(shim.LOGICAL_LEDS)]
        return snapshot

    def test_progress_bar_grows_from_the_first_led(self):
        renderer = render.Renderer(led_count=30)
        payload = renderer.render(self._progress_snapshot(6), 0.0)
        self.assertGreater(payload[2], 40, "bar should start at the first LED")
        self.assertEqual(payload[-3:], bytes(3), "far end should stay dark")

    def test_reverse_moves_the_progress_bar_to_the_other_end(self):
        # The strip's data end is not always where the bar should start.
        snapshot = self._progress_snapshot(6)
        payload = render.Renderer(led_count=30, reverse=True).render(snapshot, 0.0)
        self.assertEqual(payload[:3], bytes(3), "near end should now be dark")
        self.assertGreater(payload[-1], 40, "bar should start at the far LED")

    def test_repeat_mapping_tiles(self):
        snapshot = shim.make_snapshot(shim.EFFECT_MANUAL, (0, 0, 0))
        snapshot.pixels[0] = (255, 0, 0, 255)
        renderer = render.Renderer(led_count=34, mapping=render.MAPPING_REPEAT)
        payload = renderer.render(snapshot, 0.0)
        self.assertEqual(payload[0:3], bytes((255, 0, 0)))
        self.assertEqual(payload[17 * 3:17 * 3 + 3], bytes((255, 0, 0)))

    def test_crop_mapping_leaves_extra_dark(self):
        snapshot = shim.make_snapshot(shim.EFFECT_MANUAL, (255, 255, 255))
        renderer = render.Renderer(led_count=20, mapping=render.MAPPING_CROP)
        payload = renderer.render(snapshot, 0.0)
        self.assertEqual(payload[17 * 3:], bytes(3 * 3))

    def test_animated_effects_change_over_time(self):
        renderer = render.Renderer(led_count=17)
        for effect in (shim.EFFECT_RAINBOW, shim.EFFECT_BREATH,
                       shim.EFFECT_PATROL, shim.EFFECT_DEMO):
            snapshot = shim.make_snapshot(effect, (255, 40, 0))
            first = renderer.render(snapshot, 0.0)
            later = renderer.render(snapshot, 1.7)
            self.assertNotEqual(first, later, "effect %d is static" % effect)

    def test_animation_cycles_are_watchable(self):
        # A patrol sweep once used 32 steps where rainbow used 256, which made
        # it eight times faster than everything else and frantic to look at.
        for nominal in (render.RAINBOW_CYCLE, render.BREATH_CYCLE,
                        render.PATROL_CYCLE, render.DEMO_CYCLE):
            seconds = render._cycle(shim.make_snapshot(), nominal, 1.0)
            self.assertGreaterEqual(seconds, 1.5,
                                    "a %.1fs cycle is too frantic" % seconds)

    def test_short_delay_cannot_strobe(self):
        snapshot = shim.make_snapshot(shim.EFFECT_PATROL)
        snapshot.delay = 1
        self.assertGreaterEqual(
            render._cycle(snapshot, render.PATROL_CYCLE, 1.0),
            render.MIN_CYCLE_SECONDS)

    def test_speed_scales_the_cycle(self):
        snapshot = shim.make_snapshot(shim.EFFECT_RAINBOW)
        slow = render._cycle(snapshot, render.RAINBOW_CYCLE, 0.5)
        fast = render._cycle(snapshot, render.RAINBOW_CYCLE, 2.0)
        self.assertAlmostEqual(slow, render.RAINBOW_CYCLE * 2)
        self.assertAlmostEqual(fast, render.RAINBOW_CYCLE / 2)

    def test_longer_delay_slows_the_animation(self):
        quick, slow = shim.make_snapshot(), shim.make_snapshot()
        quick.delay, slow.delay = 8, 16      # the module's range is 0..20
        self.assertLess(render._cycle(quick, render.PATROL_CYCLE, 1.0),
                        render._cycle(slow, render.PATROL_CYCLE, 1.0))

    def test_default_delay_gives_the_nominal_cycle(self):
        # The constants are stated for the module's default delay.
        snapshot = shim.make_snapshot()
        self.assertEqual(snapshot.delay, render.DELAY_DEFAULT)
        self.assertAlmostEqual(
            render._cycle(snapshot, render.RAINBOW_CYCLE, 1.0),
            render.RAINBOW_CYCLE)

    def test_delay_above_the_range_is_clamped(self):
        snapshot = shim.make_snapshot()
        snapshot.delay = 255
        at_max = shim.make_snapshot()
        at_max.delay = render.DELAY_MAX
        self.assertEqual(render._cycle(snapshot, render.RAINBOW_CYCLE, 1.0),
                         render._cycle(at_max, render.RAINBOW_CYCLE, 1.0))

    def test_patrol_repeats_exactly_once_per_cycle(self):
        renderer = render.Renderer(led_count=17)
        snapshot = shim.make_snapshot(shim.EFFECT_PATROL, (255, 40, 0))
        period = render._cycle(snapshot, render.PATROL_CYCLE, 1.0)
        self.assertEqual(renderer.render(snapshot, 0.0),
                         renderer.render(snapshot, period))
        self.assertNotEqual(renderer.render(snapshot, 0.0),
                            renderer.render(snapshot, period / 4.0))

    def _patrol_positions(self, snapshot, samples=400):
        renderer = render.Renderer(led_count=shim.LOGICAL_LEDS,
                                   mapping=render.MAPPING_CROP)
        period = render._cycle(snapshot, render.PATROL_CYCLE, 1.0)
        positions = []
        for step in range(samples):
            frame = renderer.render(snapshot, period * step / (samples / 2.0))
            positions.append(max(range(shim.LOGICAL_LEDS),
                                 key=lambda led: frame[led * 3]))
        return positions

    def test_patrol_sweep_is_continuous(self):
        # Offsetting a scanner's position and wrapping it used to teleport it
        # from the far end back to LED 0 at every turning point.
        positions = self._patrol_positions(
            shim.make_snapshot(shim.EFFECT_PATROL, (255, 40, 0)))
        for previous, current in zip(positions, positions[1:]):
            self.assertLessEqual(abs(current - previous), 2,
                                 "scanner jumped from LED %d to %d"
                                 % (previous, current))

    def test_patrol_reaches_both_ends(self):
        positions = self._patrol_positions(
            shim.make_snapshot(shim.EFFECT_PATROL, (255, 40, 0)))
        self.assertEqual(min(positions), 0)
        self.assertEqual(max(positions), shim.LOGICAL_LEDS - 1)

    @staticmethod
    def _lit(frame):
        return sum(1 for led in range(shim.LOGICAL_LEDS) if frame[led * 3] > 40)

    def test_patrol_num_does_not_change_the_dot_count(self):
        # patrol_num is most likely live position state, not a count. Reading
        # it as one painted three dots on a bar that should show one.
        renderer = render.Renderer(led_count=shim.LOGICAL_LEDS,
                                   mapping=render.MAPPING_CROP)
        plain = shim.make_snapshot(shim.EFFECT_PATROL, (255, 40, 0))
        odd = shim.make_snapshot(shim.EFFECT_PATROL, (255, 40, 0))
        odd.patrol_num = 3
        self.assertEqual(renderer.render(plain, 0.0), renderer.render(odd, 0.0))

    def test_patrol_dots_option_adds_scanners(self):
        snapshot = shim.make_snapshot(shim.EFFECT_PATROL, (255, 40, 0))
        one = render.Renderer(led_count=shim.LOGICAL_LEDS,
                              mapping=render.MAPPING_CROP, patrol_dots=1)
        three = render.Renderer(led_count=shim.LOGICAL_LEDS,
                                mapping=render.MAPPING_CROP, patrol_dots=3)
        self.assertGreater(self._lit(three.render(snapshot, 0.0)),
                           self._lit(one.render(snapshot, 0.0)))

    def test_patrol_defaults_to_a_single_dot(self):
        renderer = render.Renderer(led_count=shim.LOGICAL_LEDS,
                                   mapping=render.MAPPING_CROP)
        self.assertEqual(renderer.patrol_dots, 1)
        # One dot with a soft tail must not light up half the bar.
        snapshot = shim.make_snapshot(shim.EFFECT_PATROL, (255, 40, 0))
        peak = max(self._lit(renderer.render(snapshot, offset / 20.0))
                   for offset in range(20))
        self.assertLessEqual(peak, 5)

    def test_gamma_darkens_midtones(self):
        snapshot = shim.make_snapshot(shim.EFFECT_MANUAL, (128, 128, 128))
        linear = render.Renderer(led_count=1, mapping=render.MAPPING_CROP)
        corrected = render.Renderer(led_count=1, mapping=render.MAPPING_CROP,
                                    gamma=2.2)
        self.assertLess(corrected.render(snapshot, 0.0)[0],
                        linear.render(snapshot, 0.0)[0])

    def test_hsv_primaries(self):
        self.assertEqual(tuple(round(v) for v in render.hsv_to_rgb(0.0, 1, 1)),
                         (255, 0, 0))
        self.assertEqual(tuple(round(v) for v in render.hsv_to_rgb(1 / 3.0, 1, 1)),
                         (0, 255, 0))
        self.assertEqual(tuple(round(v) for v in render.hsv_to_rgb(2 / 3.0, 1, 1)),
                         (0, 0, 255))


class TestConfig(unittest.TestCase):
    def _write(self, text):
        import tempfile
        handle = tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False)
        handle.write(text)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return handle.name

    def test_defaults_are_valid(self):
        self.assertEqual(config.load(path=None)["LED_COUNT"], 17)

    def test_file_overrides_defaults(self):
        path = self._write("# comment\nLED_COUNT=60\nREVERSE=yes\nGAMMA=2.2\n")
        loaded = config.load(path)
        self.assertEqual(loaded["LED_COUNT"], 60)
        self.assertIs(loaded["REVERSE"], True)
        self.assertAlmostEqual(loaded["GAMMA"], 2.2)

    def test_cli_overrides_file(self):
        path = self._write("LED_COUNT=60\n")
        self.assertEqual(config.load(path, {"LED_COUNT": 30})["LED_COUNT"], 30)

    def test_quotes_are_stripped(self):
        path = self._write('SERIAL_PORT="/dev/steamos-led-esp"\n')
        self.assertEqual(config.load(path)["SERIAL_PORT"], "/dev/steamos-led-esp")

    def test_unknown_key_rejected(self):
        path = self._write("NOPE=1\n")
        with self.assertRaises(config.ConfigError):
            config.load(path)

    def test_a_setting_we_withdrew_does_not_stop_the_service(self):
        # This one was learned the hard way: WARNING_COLOR existed for two
        # commits, the panel wrote it into a real config, and removing the
        # option turned that line into a service that would not start at all.
        # A key the reader mistyped is their problem; a key we withdrew is not.
        path = self._write("LED_COUNT=60\nWARNING_COLOR=#ff3c00\n"
                           "WARNING_STYLE=default\n")
        loaded = config.load(path)
        self.assertEqual(loaded["LED_COUNT"], 60)
        self.assertNotIn("WARNING_COLOR", loaded)

    def test_a_typo_is_still_a_typo(self):
        # The reason unknown keys are fatal in the first place: LED_COUTN=60
        # that quietly does nothing is worse than a service that says so.
        path = self._write("LED_COUTN=60\n")
        with self.assertRaises(config.ConfigError):
            config.load(path)

    def test_a_withdrawn_setting_is_removed_when_the_panel_saves(self):
        # Otherwise it sits there forever, and every start logs about it.
        text = "LED_COUNT=17\nWARNING_COLOR=#ff3c00\n# a comment\n"
        updated = config.update_text(text, {"LED_COUNT": 60})
        self.assertNotIn("WARNING_COLOR", updated)
        self.assertIn("LED_COUNT=60", updated)
        self.assertIn("# a comment", updated)

    def test_every_withdrawn_setting_really_is_gone(self):
        # A name in both tables would be read as retired and never reach the
        # code that wants it - a setting that silently stopped working.
        for key in config.RETIRED:
            self.assertNotIn(key, config.DEFAULTS, key)

    def test_invalid_values_rejected(self):
        for override in ({"LED_COUNT": 0}, {"FPS": 999}, {"MAPPING": "spiral"},
                         {"GAMMA": 0}, {"MAX_BRIGHTNESS": 300},
                         # A gauge needs a span to fill over, and a mark
                         # outside the plausible range means a unit mix-up.
                         {"TEMPERATURE_MAX": 30}, {"TEMPERATURE_MIN": 200},
                         {"TEMPERATURE_MIN": 84, "TEMPERATURE_MAX": 85},
                         # A colour the service cannot parse would be found
                         # out at flash time, which is no time to find out.
                         {"ACHIEVEMENT_COLOR": "goldish"},
                         {"MESSAGE_COLOR": "#12345"},
                         # Same for a shape: a name nothing implements would
                         # silently become the default one at flash time.
                         {"ACHIEVEMENT_STYLE": "sparkle"},
                         {"FRIEND_STYLE": ""}):
            with self.assertRaises(config.ConfigError):
                config.load(path=None, overrides=override)

    def test_the_default_notification_colours_are_the_built_in_ones(self):
        # The config file states them so they can be changed; if the two ever
        # disagree, a fresh install changes colour for no stated reason.
        from steamos_led import notify
        for kind, prefix in config.CONFIGURABLE_KINDS:
            key = prefix + "_COLOR"
            self.assertEqual(notify.parse_color(config.DEFAULTS[key]),
                             notify.KINDS[kind], key)

    def test_every_notification_starts_out_following_the_general_shape(self):
        # Otherwise NOTIFY_STYLE would stop being the one setting for "all of
        # them look like this", which is what most people want it to be.
        from steamos_led import notify
        for _kind, prefix in config.CONFIGURABLE_KINDS:
            key = prefix + "_STYLE"
            self.assertEqual(config.DEFAULTS[key], notify.STYLE_INHERIT, key)

    def test_following_the_general_shape_is_not_itself_a_shape(self):
        # "default" is stored in the same field as a shape name, so a shape
        # called that would be unreachable - and nobody would know why.
        from steamos_led import notify
        self.assertNotIn(notify.STYLE_INHERIT, notify.STYLES)

    def test_a_shape_of_its_own_is_accepted(self):
        from steamos_led import notify
        loaded = config.load(path=None,
                             overrides={"ACHIEVEMENT_STYLE": notify.STYLE_PULSE})
        self.assertEqual(loaded["ACHIEVEMENT_STYLE"], notify.STYLE_PULSE)

    def test_a_configured_colour_survives_the_round_trip(self):
        path = self._write("ACHIEVEMENT_COLOR=#CD7F32\n")
        self.assertEqual(config.load(path)["ACHIEVEMENT_COLOR"], "#CD7F32")


class TestFirmwareConsistency(unittest.TestCase):
    """Guards the two places that have to agree about the serial link."""

    def _firmware_rates(self):
        import re
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "..", "firmware", "led-client", "platformio.ini")
        with open(path) as handle:
            rates = {int(rate) for rate
                     in re.findall(r"-D SERIAL_BAUD=(\d+)", handle.read())}
        self.assertTrue(rates, "no SERIAL_BAUD flags found in platformio.ini")
        return rates

    def test_firmware_rates_can_be_set_on_linux(self):
        from steamos_led.serialport import BAUD_CONSTANTS
        for rate in self._firmware_rates():
            self.assertIn(rate, BAUD_CONSTANTS,
                          "firmware builds %d baud, which termios cannot set"
                          % rate)

    def test_default_config_matches_the_firmware(self):
        self.assertIn(config.DEFAULTS["BAUD"], self._firmware_rates(),
                      "the default BAUD talks to none of the shipped firmware")

    def test_firmware_rates_are_covered_by_autodetect(self):
        for rate in self._firmware_rates():
            self.assertIn(rate, (config.DEFAULTS["BAUD"],) + link.FALLBACK_BAUD_RATES,
                          "a board flashed at %d baud would never be found" % rate)


class MisconfiguredDeviceTest(unittest.TestCase):
    """DEVICE pointing at something that is not the shim must not spin.

    Every way to get this wrong leaves the device readable, so poll() returns
    at once every time and the main loop has nothing to wait on. Measured
    before the backoff: 49k turns a second against a device that reads as
    garbage, and 288k against one that reads as empty - the second in complete
    silence, because an empty read raised nothing to log.
    """

    def _turns_per_second(self, device, window=0.4):
        """How often the loop comes round against this device."""
        conf = dict(config.DEFAULTS)
        conf.update(DEVICE=device, SERIAL_PORT="/dev/does-not-exist",
                    NOTIFY=False)
        runner = service.Runner(conf)
        runner.source = runner._open_source()
        self.addCleanup(runner.source.close)

        turns = []
        waited = runner._wait
        runner._wait = lambda interval: (turns.append(1), waited(interval))[1]

        def drive():
            try:
                runner._loop()
            except service._Stopped:
                pass

        thread = threading.Thread(target=drive, daemon=True)
        started = time.monotonic()
        thread.start()
        time.sleep(window)
        runner.running = False
        thread.join(timeout=10)
        return len(turns) / (time.monotonic() - started)

    # Comfortably above FPS, far below a spin. FPS only applies once a
    # snapshot has been read; neither device here ever yields one.
    CEILING = 500

    def test_a_device_that_reads_as_garbage_does_not_spin(self):
        rate = self._turns_per_second("/dev/zero")
        self.assertLess(rate, self.CEILING,
                        "%.0f turns/s - the loop is not backing off" % rate)

    def test_a_device_that_reads_as_empty_does_not_spin(self):
        # The quiet one: an empty read is not an error, so this span used to
        # burn a core without a single line in the journal.
        rate = self._turns_per_second("/dev/null")
        self.assertLess(rate, self.CEILING,
                        "%.0f turns/s - the loop is not backing off" % rate)


if __name__ == "__main__":
    unittest.main()


class ConfigRewritingTest(unittest.TestCase):
    """Changing settings must not shred the file explaining them.

    The shipped config is mostly comments - what each option does, why the
    baud rate is what it is. A control panel that rewrites it from parsed
    values would leave a bare list of assignments behind.
    """

    SAMPLE = (
        "# Configuration for steamos-led-serial.\n"
        "\n"
        "# Number of LEDs on the physical strip.\n"
        "LED_COUNT=17\n"
        "\n"
        "# Brightness limits, 0-255.\n"
        "MAX_BRIGHTNESS=255\n"
        "REVERSE=0\n"
    )

    def test_a_value_is_replaced_in_place(self):
        result = config.update_text(self.SAMPLE, {"LED_COUNT": 60})
        self.assertIn("LED_COUNT=60\n", result)
        self.assertNotIn("LED_COUNT=17", result)

    def test_every_comment_survives(self):
        result = config.update_text(self.SAMPLE, {"LED_COUNT": 60,
                                                  "MAX_BRIGHTNESS": 80})
        for line in self.SAMPLE.splitlines():
            if line.startswith("#"):
                self.assertIn(line, result, line)

    def test_the_order_of_the_file_is_kept(self):
        result = config.update_text(self.SAMPLE, {"MAX_BRIGHTNESS": 80})
        self.assertLess(result.index("LED_COUNT"), result.index("MAX_BRIGHTNESS"))

    def test_untouched_options_keep_their_value(self):
        result = config.update_text(self.SAMPLE, {"LED_COUNT": 60})
        self.assertIn("MAX_BRIGHTNESS=255\n", result)
        self.assertIn("REVERSE=0\n", result)

    def test_booleans_are_written_as_the_file_spells_them(self):
        result = config.update_text(self.SAMPLE, {"REVERSE": True})
        self.assertIn("REVERSE=1\n", result)
        # And not as Python spells them.
        self.assertNotIn("True", result)

    def test_an_option_not_in_the_file_is_appended(self):
        # Options added by a later version are not in an older user's file.
        result = config.update_text(self.SAMPLE, {"NOTIFY_MESSAGES": False})
        self.assertIn("NOTIFY_MESSAGES=0\n", result)
        self.assertIn("LED_COUNT=17\n", result, "and nothing else moved")

    def test_the_result_still_parses_to_what_was_asked_for(self):
        import tempfile
        result = config.update_text(self.SAMPLE, {"LED_COUNT": 60,
                                                  "REVERSE": True,
                                                  "GAMMA": 2.2})
        with tempfile.NamedTemporaryFile("w", suffix=".conf",
                                         delete=False) as handle:
            handle.write(result)
            path = handle.name
        self.addCleanup(os.unlink, path)
        parsed = config.parse_file(path)
        self.assertEqual(parsed["LED_COUNT"], 60)
        self.assertEqual(parsed["REVERSE"], True)
        self.assertEqual(parsed["GAMMA"], 2.2)

    def test_a_file_without_a_trailing_newline_is_handled(self):
        result = config.update_text("LED_COUNT=17", {"GAMMA": 2.2})
        self.assertIn("LED_COUNT=17\n", result)
        self.assertIn("GAMMA=2.2\n", result)

    def test_an_option_named_twice_is_rewritten_everywhere(self):
        """A duplicate must not outrank the change.

        Hand-editing is how this file is meant to be used, so a key can end up
        in it twice - and parse_file() takes the last one. Rewriting only the
        first left the stale duplicate to win, with the panel reporting success
        and the service quietly keeping the old value.
        """
        doubled = ("LED_COUNT=17\n"
                   "MAX_BRIGHTNESS=255\n"
                   "\n"
                   "# tried this out later\n"
                   "LED_COUNT=60\n")
        result = config.update_text(doubled, {"LED_COUNT": 30})
        self.assertEqual(result.count("LED_COUNT=30"), 2)
        self.assertNotIn("LED_COUNT=17", result)
        self.assertNotIn("LED_COUNT=60", result)

        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".conf",
                                         delete=False) as handle:
            handle.write(result)
            path = handle.name
        self.addCleanup(os.unlink, path)
        self.assertEqual(config.parse_file(path)["LED_COUNT"], 30,
                         "what the service reads must be what was asked for")

    def test_the_real_shipped_config_survives_a_round_trip(self):
        here = os.path.dirname(os.path.abspath(__file__))
        shipped = os.path.join(here, "..", "server", "steamos-led-serial.conf")
        with open(shipped) as handle:
            text = handle.read()
        result = config.update_text(text, {"LED_COUNT": 60, "GAMMA": 2.2})
        self.assertEqual(text.count("#"), result.count("#"),
                         "comments were lost")
        self.assertIn("LED_COUNT=60\n", result)


class StandbyTest(unittest.TestCase):
    """Handing the strip to the ESP while the machine sleeps.

    During a suspend the service is frozen, so nothing here can render: the
    ESP has to breathe on its own, and the only job on this side is to tell it
    so, and then to stay quiet.
    """

    class FakeLink:
        """Enough of EspLink for the real loop to run against."""

        def __init__(self):
            self.standby = []
            self.frames = 0
            self.answer = True

        connected = True

        def connect(self):
            return True

        def poll(self):
            pass

        def shutdown(self):
            pass

        def send_standby(self, colour, period_ms):
            self.standby.append((tuple(colour), period_ms))
            return self.answer

        def send_frame(self, payload, led_count):
            self.frames += 1
            return True

    def _runner(self, **overrides):
        conf = dict(config.DEFAULTS)
        conf.update(SERIAL_PORT="/dev/does-not-exist", NOTIFY=False)
        conf.update(overrides)
        runner = service.Runner(conf)
        runner.link = self.FakeLink()
        return runner

    def test_going_to_sleep_hands_the_strip_over(self):
        runner = self._runner()
        runner._enter_standby()
        self.assertEqual(len(runner.link.standby), 1)
        colour, period = runner.link.standby[0]
        self.assertEqual(colour, service.STANDBY_COLOR)
        self.assertEqual(period, service.STANDBY_PERIOD_MS)
        self.assertIsNotNone(runner.standby_since)

    def test_the_brightness_ceiling_applies_to_it_too(self):
        # Someone who capped the strip because it runs off the USB rail must
        # not get a full-brightness white breath all night.
        runner = self._runner(MAX_BRIGHTNESS=51)       # a fifth
        runner._enter_standby()
        colour, _period = runner.link.standby[0]
        self.assertEqual(colour, tuple(channel // 5
                                       for channel in service.STANDBY_COLOR))

    def test_it_can_be_switched_off(self):
        runner = self._runner(STANDBY_PULSE=False)
        runner._enter_standby()
        self.assertEqual(runner.link.standby, [])
        self.assertIsNone(runner.standby_since)

    def test_a_link_that_will_not_take_it_leaves_no_standby_behind(self):
        # Otherwise the loop would go quiet for a strip that never heard.
        runner = self._runner()
        runner.link.answer = False
        runner._enter_standby()
        self.assertIsNone(runner.standby_since)

    def test_resuming_ends_it(self):
        runner = self._runner()
        runner._enter_standby()
        runner._leave_standby("test")
        self.assertIsNone(runner.standby_since)

    def test_resuming_without_standby_is_harmless(self):
        runner = self._runner()
        runner._leave_standby("test")
        self.assertIsNone(runner.standby_since)

    def test_the_words_are_not_flashes(self):
        # They travel on the notification pipe, which is otherwise a colour or
        # a kind - so they have to be taken off it before the overlay sees
        # them, or "standby" would be logged as an unparseable colour.
        runner = self._runner()
        flashed = []
        runner.overlay.trigger = lambda word, now: flashed.append(word)
        runner.trigger = type("Pipe", (), {
            "read": lambda self: [service.STANDBY_WORD, "achievement",
                                  service.RESUME_WORD]})()
        runner._poll_trigger(0.0)
        self.assertEqual(flashed, ["achievement"])
        self.assertEqual(len(runner.link.standby), 1)
        self.assertIsNone(runner.standby_since, "resume followed")


class StandbyQuietTest(unittest.TestCase):
    """The part that is easy to get wrong: the loop has to stop sending.

    The firmware ends standby on the next frame it is given, and the service
    keeps sending an idle heartbeat even for a static scene. So without this
    the standby would last exactly one frame interval and the strip would go
    dark a moment later, when the machine actually suspended.
    """

    def _runner(self):
        conf = dict(config.DEFAULTS)
        conf.update(SERIAL_PORT="/dev/does-not-exist", NOTIFY=False)
        runner = service.Runner(conf)
        runner.link = StandbyTest.FakeLink()
        return runner

    def _drive(self, runner, seconds=0.3, seq=None):
        """Run the real loop against a FIFO fed with snapshots.

        `seq` pins the sequence number, which is how the module says whether
        anything has written to it: pinned at UNTOUCHED_SEQ it never has, and
        that is a boot before Steam starts. Left out, the number climbs from
        just above it, which is an ordinary session.
        """
        from steamos_led import shim as shim_module

        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        path = os.path.join(directory, "shim")
        os.mkfifo(path)
        runner.config["DEVICE"] = path

        stop = threading.Event()

        def feed():
            try:
                fd = os.open(path, os.O_WRONLY)
            except OSError:
                return
            snapshot = shim_module.make_snapshot(shim_module.EFFECT_MANUAL,
                                                 (10, 200, 30))
            counter = service.UNTOUCHED_SEQ
            try:
                while not stop.is_set():
                    counter += 1
                    try:
                        os.write(fd, shim_module.encode(
                            snapshot, counter if seq is None else seq))
                    except OSError:
                        return
                    time.sleep(0.02)
            finally:
                os.close(fd)

        writer = threading.Thread(target=feed, daemon=True)
        writer.start()
        runner.source = runner._open_source()
        self.addCleanup(runner.source.close)

        def drive():
            try:
                runner._loop()
            except service._Stopped:
                pass

        thread = threading.Thread(target=drive, daemon=True)
        thread.start()
        time.sleep(seconds)
        runner.running = False
        stop.set()
        thread.join(timeout=10)

    def test_frames_flow_when_the_machine_is_awake(self):
        # The control: without this the next test would pass on a loop that
        # never sent anything for some other reason.
        runner = self._runner()
        self._drive(runner)
        self.assertGreater(runner.link.frames, 0)

    def test_no_frames_go_out_while_the_machine_is_asleep(self):
        runner = self._runner()
        runner._enter_standby()
        self._drive(runner)
        self.assertEqual(runner.link.frames, 0,
                         "one frame is all it takes to end the standby")

    def test_it_lets_go_after_half_a_minute_of_being_awake(self):
        # If the resume hook never runs - removed, or an earlier hook failed -
        # the bar must not breathe at a machine that is plainly in use.
        # monotonic() does not advance across a suspend, so this cannot fire
        # during a real one however long it lasts.
        runner = self._runner()
        runner._enter_standby()
        runner.standby_since -= service.STANDBY_MAX_AWAKE + 1
        self._drive(runner)
        self.assertIsNone(runner.standby_since)
        self.assertGreater(runner.link.frames, 0, "and it takes the bar back")


class StartupBreathTest(StandbyQuietTest):
    """What the bar does between the service starting and Steam saying anything.

    The kernel module comes up reporting "off" and only counts its sequence up
    when something writes, so for most of a boot the honest frame is black -
    and sending it kills the breath the ESP is already running. Nobody is
    served by that: it is not information, it is a gap.
    """

    def _runner(self):
        # Notifications on, unlike the standby tests above: one of these is
        # about a flash arriving before Steam has said anything.
        conf = dict(config.DEFAULTS)
        conf.update(SERIAL_PORT="/dev/does-not-exist", NOTIFY=True,
                    NOTIFY_WARNING=False, NOTIFY_DURATION=0.2)
        runner = service.Runner(conf)
        runner.link = StandbyTest.FakeLink()
        return runner

    def test_nothing_is_sent_while_steam_has_said_nothing(self):
        runner = self._runner()
        self._drive(runner, seq=service.UNTOUCHED_SEQ)
        self.assertEqual(runner.link.frames, 0)

    def test_the_esp_is_asked_to_keep_breathing(self):
        runner = self._runner()
        self._drive(runner, seq=service.UNTOUCHED_SEQ)
        self.assertEqual(len(runner.link.standby), 1, "asked once, not per frame")
        colour, period = runner.link.standby[0]
        self.assertEqual(colour, service.STARTUP_COLOR)
        self.assertEqual(period, service.STARTUP_PERIOD_MS)

    def test_the_first_thing_steam_writes_takes_the_bar_back(self):
        # The sequence number is what says so: the module steps it on every
        # write, so anything above the initial value means Steam is there.
        runner = self._runner()
        self._drive(runner, seq=service.UNTOUCHED_SEQ + 1)
        self.assertGreater(runner.link.frames, 0)
        self.assertEqual(runner.link.standby, [])

    def test_a_flash_still_gets_through(self):
        # A notification before Steam turns up has to be shown - and since
        # frames end the ESP's breath, it has to be asked for again after.
        runner = self._runner()
        runner.overlay.trigger("achievement", time.monotonic())
        self._drive(runner, seconds=0.6, seq=service.UNTOUCHED_SEQ)
        self.assertGreater(runner.link.frames, 0, "the flash was not shown")
        self.assertGreaterEqual(len(runner.link.standby), 1,
                                "and the breath was not asked for again")
