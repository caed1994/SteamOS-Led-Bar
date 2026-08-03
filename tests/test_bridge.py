"""Unit tests: python3 -m unittest discover -s tests

Everything here is stdlib-only, so it runs on a stock SteamOS image.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "server"))

from steamos_led import config, link, render, shim  # noqa: E402


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
        quick.delay, slow.delay = 20, 40
        self.assertLess(render._cycle(quick, render.PATROL_CYCLE, 1.0),
                        render._cycle(slow, render.PATROL_CYCLE, 1.0))

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

    def test_invalid_values_rejected(self):
        for override in ({"LED_COUNT": 0}, {"FPS": 999}, {"MAPPING": "spiral"},
                         {"GAMMA": 0}, {"MAX_BRIGHTNESS": 300}):
            with self.assertRaises(config.ConfigError):
                config.load(path=None, overrides=override)


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


if __name__ == "__main__":
    unittest.main()
