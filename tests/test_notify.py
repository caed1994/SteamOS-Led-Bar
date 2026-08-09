"""Tests for the notification overlay and its trigger pipe."""

import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "server"))

from steamos_led import notify  # noqa: E402


class ColourParsingTest(unittest.TestCase):
    def test_named_kinds(self):
        self.assertEqual(notify.parse_color("achievement"), (255, 215, 0))
        self.assertEqual(notify.parse_color("MESSAGE"), (128, 0, 255))

    def test_hex(self):
        self.assertEqual(notify.parse_color("#00ff88"), (0, 255, 136))
        self.assertEqual(notify.parse_color("00FF88"), (0, 255, 136))

    def test_triplet(self):
        self.assertEqual(notify.parse_color("10, 20,30"), (10, 20, 30))

    def test_rejects_nonsense(self):
        for bad in ("", "#12345", "nope", "1,2", "300,0,0", "#gggggg", "1,2,3,4"):
            with self.assertRaises(ValueError, msg=bad):
                notify.parse_color(bad)


class OverlayTest(unittest.TestCase):
    def setUp(self):
        self.overlay = notify.NotificationOverlay(duration=2.0, led_count=4)
        self.base = bytes(range(12))          # something recognisable

    def test_passes_frames_through_when_idle(self):
        self.assertEqual(self.overlay.apply(self.base, 0.0), self.base)
        self.assertFalse(self.overlay.active)

    def test_flash_takes_over_the_whole_bar(self):
        self.overlay.trigger("achievement", 100.0)
        self.assertTrue(self.overlay.active)
        frame = self.overlay.apply(self.base, 100.5)
        self.assertNotEqual(frame, self.base)
        self.assertEqual(len(frame), 4 * 3)
        # Gold: red high, some green, no blue - on whichever LED is lit.
        lit = max(range(4), key=lambda led: frame[led * 3])
        red, green, blue = frame[lit * 3:lit * 3 + 3]
        self.assertGreater(red, green)
        self.assertGreater(green, blue)
        self.assertEqual(blue, 0)

    def test_brightness_varies_over_the_flash(self):
        self.overlay.trigger("achievement", 0.0)
        levels = {self.overlay.apply(self.base, step / 40.0)[0]
                  for step in range(80)}
        self.assertGreater(len(levels), 3, "the flash should move, not sit still")

    def test_frame_is_none_while_nothing_is_running(self):
        # The service renders the frame underneath only when this says None,
        # so an idle overlay must not claim the bar.
        self.assertIsNone(self.overlay.frame(0.0))

    def test_frame_gives_the_flash_while_one_runs(self):
        self.overlay.trigger("achievement", 100.0)
        frame = self.overlay.frame(100.5)
        self.assertIsNotNone(frame)
        self.assertEqual(len(frame), 4 * 3)
        self.assertEqual(frame, self.overlay.apply(self.base, 100.5))

    def test_frame_lets_go_the_moment_the_flash_expires(self):
        self.overlay.trigger("achievement", 100.0)
        self.assertIsNotNone(self.overlay.frame(100.5))
        self.assertIsNone(self.overlay.frame(102.1),
                          "the bar has to go back to Steam, not stay gold")
        self.assertFalse(self.overlay.active)

    def test_hands_the_bar_back_when_it_expires(self):
        self.overlay.trigger("achievement", 100.0)
        self.assertNotEqual(self.overlay.apply(self.base, 100.5), self.base)
        self.assertEqual(self.overlay.apply(self.base, 102.1), self.base)
        self.assertFalse(self.overlay.active)

    def test_ends_dark_so_there_is_no_hard_edge(self):
        self.overlay.trigger("achievement", 0.0)
        tail = self.overlay.apply(self.base, 1.98)
        self.assertLess(max(tail), 60)

    def test_a_second_flash_waits_its_turn(self):
        self.overlay.trigger("achievement", 0.0)
        self.overlay.trigger("message", 1.0)
        # Look at the middle: the bloom starts there, so the ends are still
        # dark this early into a flash.
        red, green, blue = self.overlay.apply(self.base, 1.5)[3:6]
        self.assertGreater(red, blue, "the achievement is still being shown")
        self.assertGreater(green, 0, "gold has green in it, purple does not")

    def test_disabled_overlay_never_fires(self):
        overlay = notify.NotificationOverlay(enabled=False, led_count=4)
        self.assertFalse(overlay.trigger("achievement", 0.0))
        self.assertEqual(overlay.apply(self.base, 0.5), self.base)

    def test_unknown_trigger_is_ignored(self):
        self.assertFalse(self.overlay.trigger("banana", 0.0))
        self.assertEqual(self.overlay.apply(self.base, 0.5), self.base)

    def test_arbitrary_colour_can_be_triggered(self):
        self.overlay.trigger("#0000ff", 0.0)
        frame = self.overlay.apply(self.base, 0.5)
        red, green, blue = frame[3:6]           # the middle, where it starts
        self.assertEqual(red, 0)
        self.assertEqual(green, 0)
        self.assertGreater(blue, 0)


class QueueTest(unittest.TestCase):
    """Two things happening at once are two things to say.

    Before this, a flash replaced whatever was running. An achievement and a
    message land in the same tick often enough - the watcher checks both on
    the same poll - and the second one silently ate the first.
    """

    def setUp(self):
        # No quiet time here: that has its own tests below, and switching it
        # off keeps these about the order things are shown in.
        self.overlay = notify.NotificationOverlay(duration=2.0, led_count=4,
                                                  repeat_gap=0)

    def _middle(self, now):
        frame = self.overlay.frame(now)
        return None if frame is None else tuple(frame[3:6])

    def _is_gold(self, colour):
        red, green, blue = colour
        return red > green > blue

    def _is_purple(self, colour):
        red, green, blue = colour
        return blue > red and green == 0

    def test_both_are_shown_in_the_order_they_arrived(self):
        self.overlay.trigger("achievement", 0.0)
        self.overlay.trigger("message", 0.0)         # the same tick
        self.assertTrue(self._is_gold(self._middle(0.5)))
        self.overlay.frame(2.1)      # the gold one ends and the queue moves on
        self.assertTrue(self._is_purple(self._middle(2.6)),
                        "the queued message should follow, not be lost")

    def test_the_queued_one_starts_when_the_first_ends(self):
        self.overlay.trigger("achievement", 0.0)
        self.overlay.trigger("message", 0.0)
        self.assertIsNotNone(self.overlay.frame(1.9))
        self.assertIsNotNone(self.overlay.frame(2.1), "not a gap in between")
        self.assertIsNotNone(self.overlay.frame(3.9), "the message's own turn")
        self.assertIsNone(self.overlay.frame(4.5), "and then the bar is free")

    def test_the_bar_is_claimed_while_anything_is_waiting(self):
        # The service drops to the idle frame rate when nothing is animating,
        # which would show the queue as a stutter.
        self.overlay.trigger("achievement", 0.0)
        self.overlay.trigger("message", 0.0)
        self.overlay.frame(2.1)
        self.assertTrue(self.overlay.active)

    def test_a_repeat_is_not_queued_behind_itself(self):
        # Three achievements in one poll is three trigger words, and gold
        # three times in a row says nothing gold twice does not.
        self.overlay.trigger("achievement", 0.0)
        self.overlay.trigger("achievement", 0.0)
        self.overlay.trigger("achievement", 0.0)
        self.assertEqual(self.overlay.pending, [])
        self.assertIsNone(self.overlay.frame(2.1))

    def test_the_queue_has_an_end(self):
        self.overlay.trigger("achievement", 0.0)
        for shade in range(notify.MAX_PENDING + 3):
            self.overlay.trigger("#0000%02x" % (0x10 + shade), 0.0)
        self.assertEqual(len(self.overlay.pending), notify.MAX_PENDING)

    def test_nothing_queues_while_the_overlay_is_off(self):
        overlay = notify.NotificationOverlay(enabled=False, led_count=4)
        overlay.trigger("achievement", 0.0)
        overlay.trigger("message", 0.0)
        self.assertFalse(overlay.active)


class RepeatGapTest(unittest.TestCase):
    """Someone typing at you once a second must not hold the bar lit.

    The watcher already collapses a burst into one trigger per poll, but the
    polls keep coming - and each one used to restart the flash, so the bar
    blinked out and regrew every second for as long as the chat lasted.
    """

    def setUp(self):
        self.overlay = notify.NotificationOverlay(duration=2.0, led_count=4,
                                                  repeat_gap=5.0)

    def test_a_repeat_during_the_flash_is_dropped(self):
        self.assertTrue(self.overlay.trigger("message", 0.0))
        self.assertFalse(self.overlay.trigger("message", 1.0))

    def test_a_repeat_in_the_quiet_time_after_it_is_dropped(self):
        self.overlay.trigger("message", 0.0)
        self.assertFalse(self.overlay.trigger("message", 3.0),
                         "the flash is over, but the bar only just said it")

    def test_it_may_be_said_again_afterwards(self):
        self.overlay.trigger("message", 0.0)
        self.assertTrue(self.overlay.trigger("message", 7.1))

    def test_a_message_storm_leaves_the_bar_dark_between_flashes(self):
        # One trigger a second for half a minute: what matters is that the bar
        # spends real time dark, not that it flashes a particular number of
        # times.
        for second in range(30):
            self.overlay.trigger("message", float(second))
            self.overlay.frame(float(second))
        dark = sum(1 for tick in range(300)
                   if self.overlay.frame(tick / 10.0) is None)
        self.assertGreater(dark, 100, "the bar should get a rest")

    def test_something_else_is_not_held_back_by_it(self):
        # The quiet time is per trigger: an achievement during a chat storm is
        # news, and has to get through.
        self.overlay.trigger("message", 0.0)
        self.assertTrue(self.overlay.trigger("achievement", 1.0))

    def test_it_can_be_switched_off(self):
        overlay = notify.NotificationOverlay(duration=2.0, led_count=4,
                                             repeat_gap=0)
        overlay.trigger("message", 0.0)
        self.assertTrue(overlay.trigger("message", 2.0),
                        "with no gap, a repeat may follow immediately")


class ConfiguredColourTest(unittest.TestCase):
    """The trigger word stays the interface; which colour it means is local.

    Callers ask for "achievement" - the watcher, a launcher hook, the panel's
    test button - so the colour has to be settled here rather than by every
    one of them.
    """

    def _middle(self, overlay):
        """The colour at the centre LED, where a bloom starts."""
        frame = overlay.frame(0.5)
        return tuple(frame[3:6])

    def test_a_configured_colour_replaces_the_built_in_one(self):
        overlay = notify.NotificationOverlay(
            led_count=4, colors={"achievement": (205, 127, 50)})
        overlay.trigger("achievement", 0.0)
        red, green, blue = self._middle(overlay)
        self.assertGreater(red, green)
        self.assertGreater(green, blue)
        self.assertEqual(overlay.colors["achievement"], (205, 127, 50))

    def test_the_kinds_nobody_configured_keep_their_colour(self):
        overlay = notify.NotificationOverlay(
            led_count=4, colors={"achievement": (205, 127, 50)})
        self.assertEqual(overlay.colors["warning"], notify.KINDS["warning"])

    def test_the_built_in_table_is_not_edited(self):
        # dict(KINDS, **colors) rather than KINDS.update(): one overlay must
        # not change what every other one flashes.
        gold = notify.KINDS["achievement"]
        notify.NotificationOverlay(led_count=4,
                                   colors={"achievement": (1, 2, 3)})
        self.assertEqual(notify.KINDS["achievement"], gold)

    def test_an_arbitrary_colour_still_works_alongside(self):
        overlay = notify.NotificationOverlay(
            led_count=4, colors={"message": (0, 200, 80)})
        overlay.trigger("#0000ff", 0.0)
        self.assertEqual(self._middle(overlay)[:2], (0, 0))


class FifoTriggerTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.path = os.path.join(self.tmpdir, "notify")
        self.trigger = notify.FifoTrigger(self.path)
        self.trigger.open()
        self.addCleanup(self._teardown)

    def _teardown(self):
        self.trigger.unlink()
        try:
            os.rmdir(self.tmpdir)
        except OSError:
            pass

    def test_creates_a_writable_fifo(self):
        import stat
        info = os.stat(self.path)
        self.assertTrue(stat.S_ISFIFO(info.st_mode))
        self.assertTrue(info.st_mode & stat.S_IWOTH,
                        "a normal user must be able to trigger it")

    def test_reads_nothing_when_idle(self):
        self.assertEqual(self.trigger.read(), [])

    def test_receives_a_word(self):
        notify.send(self.path, "achievement")
        self.assertEqual(self.trigger.read(), ["achievement"])

    def test_receives_several_words_at_once(self):
        notify.send(self.path, "achievement")
        notify.send(self.path, "message")
        self.assertEqual(self.trigger.read(), ["achievement", "message"])

    def test_partial_line_waits_for_its_newline(self):
        fd = os.open(self.path, os.O_WRONLY | os.O_NONBLOCK)
        try:
            os.write(fd, b"achiev")
            self.assertEqual(self.trigger.read(), [])
            os.write(fd, b"ement\n")
            self.assertEqual(self.trigger.read(), ["achievement"])
        finally:
            os.close(fd)

    def test_survives_a_writer_closing(self):
        # With O_RDONLY the pipe would report EOF forever after this.
        notify.send(self.path, "achievement")
        self.assertEqual(self.trigger.read(), ["achievement"])
        notify.send(self.path, "message")
        self.assertEqual(self.trigger.read(), ["message"])

    def test_send_without_a_listener_is_a_clear_error(self):
        missing = os.path.join(self.tmpdir, "not-there")
        with self.assertRaises(OSError) as caught:
            notify.send(missing, "achievement")
        self.assertIn("is the service running", str(caught.exception))

    def test_refuses_a_path_that_is_not_a_fifo(self):
        plain = os.path.join(self.tmpdir, "plain")
        with open(plain, "w") as handle:
            handle.write("x")
        with self.assertRaises(OSError):
            notify.FifoTrigger(plain).open()


class BloomShapeTest(unittest.TestCase):
    """The achievement flash: out of the middle, one blink, back to the middle."""

    LEDS = 17
    DURATION = 2.0

    def setUp(self):
        self.overlay = notify.NotificationOverlay(
            duration=self.DURATION, led_count=self.LEDS)
        self.overlay.trigger("achievement", 0.0)
        self.base = bytes(self.LEDS * 3)

    def _levels(self, fraction):
        frame = self.overlay.apply(self.base, self.DURATION * fraction)
        return [frame[led * 3] for led in range(self.LEDS)]

    def test_starts_in_the_middle(self):
        levels = self._levels(0.10)
        middle = self.LEDS // 2
        self.assertGreater(levels[middle], 0)
        self.assertEqual(levels[0], 0, "the ends must not be lit yet")
        self.assertEqual(levels[-1], 0)

    def test_grows_outwards(self):
        early = self._levels(0.10)
        later = self._levels(0.25)
        lit = lambda levels: sum(1 for level in levels if level > 0)
        self.assertGreater(lit(later), lit(early))

    # Sampled relative to the phase boundaries, so tuning the timing does not
    # break the tests that describe the shape.
    JUST_OUT = notify.BLOOM_EXPANDED + 0.01
    BREATH_LOW = (notify.BLOOM_EXPANDED + notify.BLOOM_RETRACT) / 2
    BREATH_END = notify.BLOOM_RETRACT - 0.02

    def test_reaches_the_ends_at_full_brightness(self):
        # The travelling edge has to overshoot, or the outermost pair sits
        # exactly on the boundary and never lights.
        levels = self._levels(self.JUST_OUT)
        self.assertEqual(levels[0], levels[self.LEDS // 2])
        self.assertEqual(levels[-1], levels[self.LEDS // 2])
        self.assertGreater(levels[0], 240)

    def test_breathes_rather_than_blinking(self):
        # A hard off reads as a blink; the bar should look like it takes a
        # breath, so the dip stays visibly lit.
        low = self._levels(self.BREATH_LOW)
        self.assertGreater(min(low), 0, "the breath must not switch off")
        self.assertLess(max(low), 80, "and it has to dip noticeably")

    def test_the_breath_dims_the_whole_bar_together(self):
        low = self._levels(self.BREATH_LOW)
        self.assertEqual(len(set(low)), 1, "the dip should be even")

    def test_comes_back_up_after_the_breath(self):
        self.assertGreater(max(self._levels(self.BREATH_END)), 240)

    def test_never_goes_fully_dark_in_the_middle_of_the_flash(self):
        for step in range(5, 95):
            levels = self._levels(step / 100.0)
            self.assertGreater(max(levels), 0, "dark at %.2f" % (step / 100.0))

    def test_retracts_towards_the_middle(self):
        late = self._levels(0.85)
        middle = self.LEDS // 2
        self.assertGreater(late[middle], 0)
        self.assertEqual(late[0], 0, "the ends should be dark again")
        self.assertEqual(late[-1], 0)

    def test_symmetric_about_the_centre(self):
        for fraction in (0.1, 0.2, 0.35, 0.55, 0.8, 0.95):
            levels = self._levels(fraction)
            self.assertEqual(levels, levels[::-1], "asymmetric at %.2f" % fraction)

    def test_fades_out_rather_than_cutting_off(self):
        # The retract runs to zero exactly at the end, so the last frame is a
        # faint glow in the middle - not a hard switch-off.
        self.assertLess(max(self._levels(0.99)), 60)
        self.assertGreater(max(self._levels(0.90)), 0)

    def test_works_on_other_strip_lengths(self):
        for count in (1, 2, 5, 30, 144):
            overlay = notify.NotificationOverlay(duration=1.0, led_count=count)
            overlay.trigger("achievement", 0.0)
            frame = overlay.apply(bytes(count * 3), 0.35)
            self.assertEqual(len(frame), count * 3)
            self.assertGreater(max(frame), 0, "%d LEDs stayed dark" % count)

    def test_pulse_style_is_still_available(self):
        overlay = notify.NotificationOverlay(
            duration=2.0, led_count=self.LEDS, style=notify.STYLE_PULSE)
        overlay.trigger("achievement", 0.0)
        frame = overlay.apply(self.base, 0.3)
        levels = {frame[led * 3] for led in range(self.LEDS)}
        self.assertEqual(len(levels), 1, "the pulse lights the bar evenly")


if __name__ == "__main__":
    unittest.main()
