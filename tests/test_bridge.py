# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

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

from steamos_utility_center import config, link, render, service, shim  # noqa: E402


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
        # The data end of the strip is not always the start of the bar.
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
        # patrol_num is most probably a live position and not a count. A read
        # of it as a count painted three dots on a bar with one dot.
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
                           "WARNING_STYLE=default\n"
                           "TEMPERATURE_MIN=40.0\nTEMPERATURE_MAX=85.0\n")
        loaded = config.load(path)
        self.assertEqual(loaded["LED_COUNT"], 60)
        for gone in config.RETIRED:
            self.assertNotIn(gone, loaded)

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
        # A name in both tables reads as retired and never reaches the code
        # that needs it. The setting then stops with no message.
        for key in config.RETIRED:
            self.assertNotIn(key, config.DEFAULTS, key)

    def test_a_withdrawn_setting_that_still_means_something_is_carried_over(self):
        # An option with no meaning is correct to ignore. But
        # TEMPERATURE_GAUGE=1 means one of the entries that replaced it. A
        # drop of that value turns off a gauge that a user reads, and it gives
        # no message and no text to search for.
        path = self._write("LED_COUNT=17\nTEMPERATURE_GAUGE=1\n")
        self.assertEqual(config.load(path)["RAINBOW_SHOWS"], "temperature")

        path = self._write("LED_COUNT=17\nTEMPERATURE_GAUGE=0\n")
        self.assertEqual(config.load(path)["RAINBOW_SHOWS"], "rainbow")

    def test_the_new_setting_outranks_the_one_it_replaced(self):
        # Whichever order the two appear in: a line naming the new option is
        # the reader's actual intention, and a leftover line is not.
        for text in ("RAINBOW_SHOWS=fire\nTEMPERATURE_GAUGE=1\n",
                     "TEMPERATURE_GAUGE=1\nRAINBOW_SHOWS=fire\n"):
            loaded = config.load(self._write(text))
            self.assertEqual(loaded["RAINBOW_SHOWS"], "fire", text)

    def test_a_withdrawn_setting_spelled_wrongly_is_not_fatal(self):
        # We are only reading the line out of politeness. Refusing to start
        # over one we no longer accept would be worse than ignoring it.
        loaded = config.load(self._write("TEMPERATURE_GAUGE=maybe\n"))
        self.assertEqual(loaded["RAINBOW_SHOWS"], config.DEFAULTS["RAINBOW_SHOWS"])

    def test_everything_carried_over_lands_somewhere_real(self):
        # A migration pointing at a key that no longer exists, or producing a
        # value the validator refuses, would be a config that cannot start -
        # and it would only show up on the machines that still had the line.
        for old, (new, table) in config.MIGRATED.items():
            self.assertIn(old, config.RETIRED, old)
            self.assertIn(new, config.DEFAULTS, new)
            for value in table.values():
                settings = dict(config.DEFAULTS)
                settings[new] = value
                config.validate(settings)

    def test_invalid_values_rejected(self):
        for override in ({"LED_COUNT": 0}, {"FPS": 999}, {"MAPPING": "spiral"},
                         {"GAMMA": 0}, {"MAX_BRIGHTNESS": 300},
                         # A colour the service cannot parse would be found
                         # out at flash time, which is no time to find out.
                         {"ACHIEVEMENT_COLOR": "goldish"},
                         {"MESSAGE_COLOR": "#12345"},
                         # Same for a shape: a name nothing implements would
                         # silently become the default one at flash time.
                         {"ACHIEVEMENT_STYLE": "kaleidoscope"},
                         {"FRIEND_STYLE": ""},
                         # And for the rainbow slot, where an unknown name
                         # would quietly leave Steam's own effect in place.
                         {"RAINBOW_SHOWS": "temperatures"}):
            with self.assertRaises(config.ConfigError):
                config.load(path=None, overrides=override)

    def test_the_default_notification_colours_are_the_built_in_ones(self):
        # The config file states them so they can be changed; if the two ever
        # disagree, a fresh install changes colour for no stated reason.
        from steamos_utility_center import notify
        for kind, prefix in config.CONFIGURABLE_KINDS:
            key = prefix + "_COLOR"
            self.assertEqual(notify.parse_color(config.DEFAULTS[key]),
                             notify.KINDS[kind], key)

    def test_every_notification_starts_out_following_the_general_shape(self):
        # Otherwise NOTIFY_STYLE would stop being the one setting for "all of
        # them look like this", which is what most people want it to be.
        from steamos_utility_center import notify
        for _kind, prefix in config.CONFIGURABLE_KINDS:
            key = prefix + "_STYLE"
            self.assertEqual(config.DEFAULTS[key], notify.STYLE_INHERIT, key)

    def test_following_the_general_shape_is_not_itself_a_shape(self):
        # The field holds "default" and also a shape name. A shape with that
        # name is therefore unreachable, and no message gives the reason.
        from steamos_utility_center import notify
        self.assertNotIn(notify.STYLE_INHERIT, notify.STYLES)

    def test_a_shape_of_its_own_is_accepted(self):
        from steamos_utility_center import notify
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
        from steamos_utility_center.serialport import BAUD_CONSTANTS
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

    Each incorrect device is still readable, so poll() returns at once each
    time and the main loop waits for nothing. A measurement before the backoff
    gave 49k passes each second against a device with unusable content, and 288k
    passes against a device with no content. The second case gave no message,
    because an empty read raises no exception.
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

    # Above FPS, and much below a loop with no wait. FPS applies after the
    # first snapshot, and neither device here gives one.
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


class RefusedConfigurationTest(unittest.TestCase):
    """A file the service will not accept must stop it, not spin it.

    A machine gave this result: one manual line with an option that does not
    exist put the unit in a restart loop. The line was WARNING_COLOR, from the
    names of the two colours that are settings. Each three seconds the unit
    started, refused the file and stopped. Forty lines of systemd messages then
    covered the one line with the incorrect option. The installer read
    is-active one time and reported the service as running.
    """

    def _run_with(self, text):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".conf",
                                         delete=False) as handle:
            handle.write(text)
            path = handle.name
        self.addCleanup(os.unlink, path)
        # --check-config loads the file and reports, which is the shortest
        # path through main() that the configuration has to survive.
        return service.main(["--config", path, "--check-config"])

    def test_an_unknown_option_is_refused_with_the_documented_code(self):
        # A spelling error, and not WARNING_COLOR. That name is in
        # config.RETIRED, which is the second half of the same fault. The
        # reader ignores a withdrawn option on purpose. Both ends must prevent
        # the loop, and only one end is an error of the reader.
        code = self._run_with("LED_COUNT=17\nLED_COUTN=60\n")
        self.assertEqual(code, service.CONFIG_REFUSED_EXIT)

    def test_an_option_we_withdrew_is_not_treated_as_a_typo(self):
        self.assertEqual(self._run_with("LED_COUNT=17\nWARNING_COLOR=#ff3c00\n"),
                         0)

    def test_a_line_that_is_not_an_option_at_all_is_refused_too(self):
        self.assertEqual(self._run_with("this is not a setting\n"),
                         service.CONFIG_REFUSED_EXIT)

    def test_systemd_is_told_not_to_retry_that(self):
        here = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(here, "..", "server", "steamos-utility-center.service")
        with open(path) as handle:
            unit = handle.read()
        self.assertIn("RestartPreventExitStatus=%d"
                      % service.CONFIG_REFUSED_EXIT, unit)
        self.assertIn("Restart=always", unit,
                      "everything else still has to come back")

    def test_the_code_is_not_the_one_a_healthy_exit_uses(self):
        # 0 is a clean shutdown, which must keep being restarted.
        self.assertNotEqual(service.CONFIG_REFUSED_EXIT, 0)

    def test_a_good_file_is_not_refused(self):
        self.assertNotEqual(self._run_with("LED_COUNT=17\n"),
                            service.CONFIG_REFUSED_EXIT)


class ConfigRewritingTest(unittest.TestCase):
    """Changing settings must not shred the file explaining them.

    The configuration file of the release is mostly comments. They give the
    function of each option, and the reason for the baud rate. A control panel
    that writes it again from the parsed values leaves a list of assignments
    only.
    """

    SAMPLE = (
        "# Configuration for steamos-utility-center.\n"
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

        A user edits this file manually, so a key can occur twice.
        parse_file() takes the last one. A write to the first one only lets
        the old second line win. The panel then reports a success, and the
        service keeps the old value with no message.
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
        shipped = os.path.join(here, "..", "server", "steamos-utility-center.conf")
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

        def __init__(self, shapes=True):
            self.standby = []
            self.frames = 0
            self.sent = []
            self.answer = True
            # Whether the board reads the shape byte. A board flashed before
            # it reads five bytes and breathes whatever the host asks for.
            self.standby_shapes = shapes

        connected = True

        def connect(self):
            return True

        def poll(self):
            pass

        def shutdown(self):
            pass

        def send_standby(self, colour, period_ms, shape=0):
            self.standby.append((tuple(colour), period_ms, shape))
            return self.answer

        def send_frame(self, payload, led_count):
            self.frames += 1
            self.sent.append(bytes(payload))
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
        colour, period, shape = runner.link.standby[0]
        self.assertEqual(colour, service.STANDBY_COLOR)
        self.assertEqual(period, service.STANDBY_PERIOD_MS)
        self.assertEqual(shape, link.STANDBY_BREATH)
        self.assertIsNotNone(runner.standby_since)

    def test_the_brightness_ceiling_applies_to_it_too(self):
        # Someone who capped the strip because it runs off the USB rail must
        # not get a full-brightness white breath all night.
        runner = self._runner(MAX_BRIGHTNESS=51)       # a fifth
        runner._enter_standby()
        colour = runner.link.standby[0][0]
        self.assertEqual(colour, tuple(channel // 5
                                       for channel in service.STANDBY_COLOR))

    def test_the_colour_comes_from_the_settings(self):
        """It was a constant in this module, and white was the only answer."""
        runner = self._runner(STANDBY_COLOR="#ff8000", STANDBY_BRIGHTNESS=60)
        runner._enter_standby()
        self.assertEqual(runner.link.standby[0][0], (60, 30, 0))

    def test_the_default_settings_are_the_colour_this_module_had(self):
        """A machine that upgrades must see no change at all.

        White at 30 of 255 is the (30, 30, 30) that was written here, so the
        two settings reproduce it and do not approximately reproduce it.
        """
        self.assertEqual(service.standby_colour(dict(config.DEFAULTS)),
                         service.STANDBY_COLOR)

    def test_the_shape_goes_with_it(self):
        runner = self._runner(STANDBY_SHOWS="dot")
        runner._enter_standby()
        self.assertEqual(runner.link.standby[0][2], link.STANDBY_DOT)

    def test_a_board_that_cannot_draw_it_is_reported(self):
        """It reads five bytes and breathes, which is the old behaviour and
        not a failure. But a person who set the dot must not be left to work
        out why the bar breathes.
        """
        runner = self._runner(STANDBY_SHOWS="dot")
        runner.link.standby_shapes = False
        with self.assertLogs("steamos-utility-center", "WARNING") as caught:
            runner._enter_standby()
        self.assertIn("firmware", "\n".join(caught.output))
        # And it still hands the strip over: a breath is better than a dark
        # bar for the whole of a suspend.
        self.assertEqual(len(runner.link.standby), 1)

    def test_a_breath_on_such_a_board_says_nothing(self):
        runner = self._runner(STANDBY_SHOWS="breath")
        runner.link.standby_shapes = False
        with self.assertNoLogs("steamos-utility-center", "WARNING"):
            runner._enter_standby()

    def test_the_sleep_hook_is_told_that_the_strip_is_ready(self):
        """It waited half a second and hoped. It waits for this now.

        The file goes beside the pipe, because a machine with a NOTIFY_FIFO
        of its own has both of them there.
        """
        room = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, room, True)
        pipe = os.path.join(room, "notify")
        runner = self._runner(NOTIFY_FIFO=pipe)
        runner._enter_standby()
        self.assertTrue(os.path.exists(os.path.join(room,
                                                    service.STANDBY_DONE)))

    def test_it_is_told_that_also_when_there_is_nothing_to_send(self):
        """A strip that is switched off, and a link that would not take the
        message, are answers too. A hook that waited for a message nobody
        will send is half a second of nothing at each suspend.
        """
        room = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, room, True)
        pipe = os.path.join(room, "notify")
        mark = os.path.join(room, service.STANDBY_DONE)

        runner = self._runner(NOTIFY_FIFO=pipe, STANDBY_PULSE=False)
        runner._enter_standby()
        self.assertTrue(os.path.exists(mark), "switched off")

        os.unlink(mark)
        runner = self._runner(NOTIFY_FIFO=pipe)
        runner.link.answer = False
        runner._enter_standby()
        self.assertTrue(os.path.exists(mark), "the link would not take it")

    def test_a_directory_it_cannot_write_does_not_stop_the_suspend(self):
        runner = self._runner(NOTIFY_FIFO="/proc/nowhere/notify")
        runner._enter_standby()             # raises nothing
        self.assertEqual(len(runner.link.standby), 1)

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
        # They come through the notification pipe, and that pipe otherwise
        # carries a colour or a type. So this code must remove them before the
        # overlay reads them. Without that, the log reports "standby" as a
        # colour that it cannot parse.
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

    def _drive(self, runner, seconds=0.3, seq=None, writes=None, every=0.02,
               effect=None):
        """Run the real loop against a FIFO fed with snapshots.

        `effect` is the request of Steam, and only a test about Game Mode needs
        it. The default is one still colour. The rainbow is the one effect where
        another setting changes the meaning.

        `every` is the wait of the feed between two snapshots. A test about the
        frame rate changes this value. The rate of the loop is not visible while
        the feed writes the device faster than that rate.

        `seq` fixes the sequence number, and the module uses that number to report
        a write. At UNTOUCHED_SEQ no program wrote to it, and that is a boot before
        Steam starts. Without this argument the number counts up from a value above
        it, and that is a normal session.

        `writes` stops the feed after that number of snapshots and keeps the device
        open. A device with a write each 20 ms wakes the loop each 20 ms, so the
        rate of the loop is not visible. Some tests here examine only that rate.
        One write and then no write is also the behaviour of a real machine: Steam
        writes at a change of a setting, and not sixty times each second.
        """
        from steamos_utility_center import shim as shim_module

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
            snapshot = shim_module.make_snapshot(
                shim_module.EFFECT_MANUAL if effect is None else effect,
                (10, 200, 30))
            counter = service.UNTOUCHED_SEQ
            written = 0
            try:
                while not stop.is_set():
                    if writes is None or written < writes:
                        counter += 1
                        written += 1
                        try:
                            os.write(fd, shim_module.encode(
                                snapshot, counter if seq is None else seq))
                        except OSError:
                            return
                    stop.wait(every)
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
        # The resume hook can fail to run: a user removed it, or an earlier
        # hook failed. The bar must then not breathe at a machine in use.
        # monotonic() does not count during a suspend, so this cannot start
        # during a real suspend of each length.
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
        colour, period, shape = runner.link.standby[0]
        self.assertEqual(colour, service.STARTUP_COLOR)
        self.assertEqual(period, service.STARTUP_PERIOD_MS)
        # The breath, whatever STANDBY_SHOWS says. This is the wait for the
        # first write of Steam and not the sleep of the machine.
        self.assertEqual(shape, link.STANDBY_BREATH)

    def test_the_first_thing_steam_writes_takes_the_bar_back(self):
        # The sequence number is what says so: the module steps it on every
        # write, so anything above the initial value means Steam is there.
        runner = self._runner()
        self._drive(runner, seq=service.UNTOUCHED_SEQ + 1)
        self.assertGreater(runner.link.frames, 0)
        self.assertEqual(runner.link.standby, [])

    def test_a_flash_still_gets_through(self):
        # A notification before the start of Steam must reach the bar. A frame
        # also ends the breath of the ESP, so this code must request that
        # breath again at the end.
        runner = self._runner()
        runner.overlay.trigger("achievement", time.monotonic())
        self._drive(runner, seconds=0.6, seq=service.UNTOUCHED_SEQ)
        self.assertGreater(runner.link.frames, 0, "the flash was not shown")
        self.assertGreaterEqual(len(runner.link.standby), 1,
                                "and the breath was not asked for again")


class FrameRateTest(StandbyQuietTest):
    """FPS as a ceiling, which is what it always said it was.

    The loop wakes on every write to the device, so the frame rate was
    whatever Steam's write rate happened to be. Measured on a Steam Machine:
    during a download Steam writes the progress bar four hundred times a
    second, and every one of them was rendered and pushed down a link that
    carries about sixty.
    """

    def _runner(self, **overrides):
        conf = dict(config.DEFAULTS)
        conf.update(SERIAL_PORT="/dev/does-not-exist", NOTIFY=False)
        conf.update(overrides)
        runner = service.Runner(conf)
        runner.link = StandbyTest.FakeLink()
        return runner

    def _rate(self, runner, seconds=1.2):
        return runner.link.frames / seconds

    def test_a_flood_of_writes_comes_out_at_the_frame_rate(self):
        """Both ends of the one number, and they are easy to confuse.

        What is fed here is a static effect that changes on every write -
        which is exactly what a download's progress bar is. Held to the idle
        rate it would be a slideshow at four frames a second; held to nothing
        it goes out four hundred times a second. FPS is the ceiling, and
        IDLE_FPS is only how long to wait before resending something that has
        not changed.
        """
        runner = self._runner(FPS=60, IDLE_FPS=4)
        self._drive(runner, seconds=1.2, every=0.0)     # as fast as it can
        self.assertGreater(self._rate(runner), 25,
                           "the frame rate fell to the idle rate")
        self.assertLess(self._rate(runner), 90,
                        "the frame rate followed Steam's write rate")

    def test_writing_slower_than_that_still_sends_every_change(self):
        # The cap is a ceiling, not a schedule. A bar that only redrew on its
        # own clock would show a download's progress a frame late for no gain.
        runner = self._runner(FPS=60)
        self._drive(runner, seconds=1.2, every=0.05)     # about twenty a second
        self.assertGreater(self._rate(runner), 12)
        self.assertLess(self._rate(runner), 40)

    def test_a_quiet_device_still_gets_its_heartbeat_and_no_more(self):
        """The other end, and the one a first attempt at this broke.

        The firmware clears the strip when the host stops. So the loop must send
        a scene with no change again, at IDLE_FPS. That is the complete purpose
        of the setting. A shorter wait at each due frame made that sixty frames
        each second.
        """
        runner = self._runner(FPS=60, IDLE_FPS=4)
        self._drive(runner, seconds=1.2, writes=1)
        self.assertLess(self._rate(runner), 12, "the idle rate stopped idling")
        self.assertGreater(self._rate(runner), 1, "nothing was resent at all")


class DesktopSceneTest(StandbyQuietTest):
    """The switch itself, in the loop that has to make it.

    The snapshot the feed writes is Steam's, and it is green. A scene of any
    other colour is therefore the whole assertion: which of the two came out
    of the renderer says which of them the loop chose, and nothing else in
    the loop can produce either by accident.
    """

    STEAM_GREEN = (10, 200, 30)
    SCENE_BLUE = "#0000ff"

    def _runner(self, scene="color", running="", uptime=10000.0, **overrides):
        conf = dict(config.DEFAULTS)
        conf.update(SERIAL_PORT="/dev/does-not-exist", NOTIFY=False,
                    DESKTOP_SCENE=scene, DESKTOP_COLOR=self.SCENE_BLUE,
                    DESKTOP_BRIGHTNESS=255, MAPPING="repeat")
        conf.update(overrides)
        runner = service.Runner(conf)
        runner.link = StandbyTest.FakeLink()
        if runner.ownership is not None:
            runner.ownership.look = lambda: running
            # A machine long since up unless a test says otherwise. Read from
            # /proc it would be whatever the build machine happens to be, and
            # one that had just come up would answer differently.
            runner.ownership.uptime = lambda: uptime
        return runner

    def _pixel(self, runner):
        """The first LED of the last frame that went out."""
        self.assertTrue(runner.link.sent, "nothing was sent at all")
        return tuple(runner.link.sent[-1][:3])

    def test_the_desktop_gets_the_scene_rather_than_steam_s_last_state(self):
        runner = self._runner()
        self._drive(runner)
        red, green, blue = self._pixel(runner)
        self.assertGreater(blue, green, "the bar is still showing Steam's")
        self.assertEqual((red, green), (0, 0))

    def test_game_mode_gets_steam_s_and_not_the_scene(self):
        # The failure that matters. A scene that held the bar through a game
        # would be the service ignoring the LED settings somebody had just
        # changed, with nothing on screen to say why.
        runner = self._runner(running="gamescope")
        self._drive(runner)
        red, green, blue = self._pixel(runner)
        self.assertGreater(green, blue, "the scene took a Game Mode session")
        self.assertEqual((red, green, blue), self.STEAM_GREEN)

    def test_leaving_it_to_steam_is_what_it_did_before(self):
        # The default, and the control for the two above: with no scene set
        # the loop has to behave exactly as it always did.
        runner = self._runner(scene="steam")
        self.assertIsNone(runner.scene)
        self._drive(runner)
        self.assertEqual(self._pixel(runner), self.STEAM_GREEN)

    def test_a_scene_comes_up_without_waiting_for_a_game_mode_session(self):
        # A machine that starts in Desktop Mode has a shim with no write, and
        # the loop leaves that condition to the startup breath of the ESP.
        # There is now a scene to show, so the loop shows it. Without this
        # code, the scene appears only after the first pass through Game Mode.
        runner = self._runner()
        self._drive(runner, seq=service.UNTOUCHED_SEQ)
        red, green, blue = self._pixel(runner)
        self.assertEqual((red, green, blue), (0, 0, 255))

    def test_but_not_in_the_middle_of_the_boot(self):
        """A user reported this sequence: boot effect, scene, boot effect, Steam.

        This service starts before the session that gives the mode of the
        machine. For some seconds a boot into Game Mode therefore looks the same
        as a desktop, and the scene started there. The startup breath belongs in
        that time. It must run until Steam starts, and the scene must not
        interrupt it.
        """
        runner = self._runner(uptime=8.0)
        self._drive(runner, seq=service.UNTOUCHED_SEQ)
        self.assertEqual(runner.link.frames, 0, "the scene came up mid-boot")
        self.assertEqual(len(runner.link.standby), 1,
                         "the startup breath was not left to the ESP")

    def test_that_breath_is_still_what_happens_with_no_scene_set(self):
        runner = self._runner(scene="steam")
        self._drive(runner, seq=service.UNTOUCHED_SEQ)
        self.assertEqual(runner.link.frames, 0)
        self.assertEqual(len(runner.link.standby), 1)

    def test_a_notification_still_covers_the_scene(self):
        # A scene is the behaviour of the bar with no event. A flash is an
        # event. This uses red over blue, so the two are clear.
        runner = self._runner(NOTIFY=True, NOTIFY_WARNING=False,
                              NOTIFY_DURATION=2.0)
        runner.overlay.trigger("warning", time.monotonic())
        self._drive(runner)
        self.assertTrue(any(frame[0] > frame[2] for frame in runner.link.sent),
                        "the flash never got over the scene")

    def test_the_scene_picks_its_own_effect_and_not_the_rainbow_slot_s(self):
        """A user asked for the four effects as separate scenes on the desktop.

        This test uses the loop and not the renderer, because the loop holds both
        answers. The slot of Game Mode is a setting of the renderer, and the
        desktop scene is a property of the scene. Only the frame at the output
        gives the answer that won.

        This uses fire against aurora, with the slot at a third value. A scene
        that still reads RAINBOW_SHOWS therefore draws the same bar for both.
        """
        frames = {}
        for scene in ("fire", "aurora", "rainbow"):
            runner = self._runner(scene=scene, RAINBOW_SHOWS="load",
                                  MAPPING="stretch")
            self._drive(runner)
            self.assertTrue(runner.link.sent, "nothing was sent at all")
            # The complete frame, and not its first LED. The three effects
            # move, so the clock decides the last frame. Two effects can also
            # have one equal pixel at one moment, with two different pictures.
            frames[scene] = runner.link.sent[-1]
        self.assertEqual(len(set(frames.values())), len(frames), frames)

    def test_a_game_mode_rainbow_still_shows_what_the_slot_holds(self):
        # The second half. No desktop setting must reach a snapshot from
        # Steam. Without that rule the slot stops, and the slot is the one
        # method to use these effects in a game.
        held = self._runner(scene="fire", RAINBOW_SHOWS="aurora",
                            running="gamescope")
        plain = self._runner(scene="fire", RAINBOW_SHOWS="rainbow",
                             running="gamescope")
        for runner in (held, plain):
            self._drive(runner, effect=shim.EFFECT_RAINBOW)
            self.assertTrue(runner.link.sent, "nothing was sent at all")
        self.assertNotEqual(held.link.sent[-1], plain.link.sent[-1])

    def test_the_frame_rate_follows_what_is_on_the_bar(self):
        """The rate follows the scene and not the last state of Steam.

        The idle rate is for a scene with no change. The last state of Steam here
        is a still colour. A read of that state, and not of the current scene,
        runs a desktop breath at four frames each second, and the breath then
        moves in steps.

        This test uses one write and then no write. That is the one method that
        shows the rate of the loop. A device with a write each 20 ms wakes the
        loop each 20 ms, for each rate.
        """
        steam = shim.make_snapshot(shim.EFFECT_MANUAL)
        self.assertFalse(service.Runner(dict(
            config.DEFAULTS, SERIAL_PORT="/dev/does-not-exist",
        )).renderer.is_animated(steam), "Steam's own state is the still one")

        still = self._runner(scene="color")
        moving = self._runner(scene="breath")
        self._drive(still, seconds=0.5, writes=1)
        self._drive(moving, seconds=0.5, writes=1)
        self.assertGreater(moving.link.frames, still.link.frames * 3,
                           "the breath is being drawn at the idle rate")
