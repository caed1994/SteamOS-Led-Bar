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


class ConfiguredStyleTest(unittest.TestCase):
    """Each kind may flash in a shape of its own; the rest follow the general.

    Colour alone runs out when two notifications are the same colour, or when
    an achievement should feel like more of an event than a friend logging in.
    """

    def _overlay(self, **kwargs):
        return notify.NotificationOverlay(
            led_count=5, duration=1.0, style=notify.STYLE_BLOOM, **kwargs)

    def test_a_kind_with_its_own_shape_uses_it(self):
        overlay = self._overlay(styles={"achievement": notify.STYLE_PULSE})
        overlay.trigger("achievement", 0.0)
        self.assertEqual(overlay.current.style, notify.STYLE_PULSE)

    def test_the_kinds_nobody_configured_follow_the_general_one(self):
        overlay = self._overlay(styles={"achievement": notify.STYLE_PULSE})
        overlay.trigger("message", 0.0)
        self.assertEqual(overlay.current.style, notify.STYLE_BLOOM)

    def test_an_arbitrary_colour_follows_the_general_one(self):
        # A colour is nobody's kind, so there is nothing to look up - it must
        # not fall through to whatever the last configured shape was.
        overlay = self._overlay(styles={"achievement": notify.STYLE_PULSE})
        overlay.trigger("#00ff88", 0.0)
        self.assertEqual(overlay.current.style, notify.STYLE_BLOOM)

    def test_a_shape_nothing_implements_is_dropped_at_the_door(self):
        # Deliberately not a shape name anyone might add later: this test is
        # about a value the service has never heard of, and it stopped being
        # about that the day "sparkle" became real.
        overlay = self._overlay(styles={"achievement": "kaleidoscope"})
        overlay.trigger("achievement", 0.0)
        self.assertEqual(overlay.current.style, notify.STYLE_BLOOM)
        self.assertNotIn("achievement", overlay.styles)

    def test_a_queued_flash_keeps_its_own_shape(self):
        # The queue holds the kind, not the shape, so the lookup has to happen
        # when it finally starts - not when it was put in line.
        overlay = self._overlay(styles={"message": notify.STYLE_PULSE})
        overlay.trigger("achievement", 0.0)
        overlay.trigger("message", 0.0)
        overlay.frame(1.5)              # the first one is over by now
        self.assertEqual(overlay.current.kind, "message")
        self.assertEqual(overlay.current.style, notify.STYLE_PULSE)

    def test_the_shape_reaches_the_pixels(self):
        # Early in a bloom the ends are still dark while the middle is lit; a
        # pulse lights the whole bar at once. Same colour, same moment.
        blooming = self._overlay()
        pulsing = self._overlay(styles={"achievement": notify.STYLE_PULSE})
        for overlay in (blooming, pulsing):
            overlay.trigger("achievement", 0.0)
        bloom, pulse = blooming.frame(0.1), pulsing.frame(0.1)
        self.assertNotEqual(bloom, pulse)
        self.assertEqual(bloom[:3], b"\x00\x00\x00", "a bloom starts inside")
        self.assertNotEqual(pulse[:3], b"\x00\x00\x00")

    def test_every_shape_lasts_as_long_as_the_others(self):
        # The duration is deliberately shared: one setting, not three.
        overlay = self._overlay(styles={"achievement": notify.STYLE_PULSE})
        overlay.trigger("achievement", 0.0)
        self.assertIsNotNone(overlay.frame(0.9))
        self.assertIsNone(overlay.frame(1.1))

    def test_nothing_configured_leaves_every_kind_on_the_general_one(self):
        overlay = self._overlay()
        self.assertEqual(overlay.styles, {})
        now = 0.0
        for kind in ("achievement", "message", "friend"):
            overlay.trigger(kind, now)
            self.assertEqual(overlay.current.style, notify.STYLE_BLOOM, kind)
            now += 2.0
            overlay.frame(now)          # let it finish before the next


class ShapeInTheTriggerTest(unittest.TestCase):
    """"comet:#1a9fff" - one flash in that shape, without configuring it.

    Choosing between five shapes by writing each into the config and
    restarting the service is the wrong way round, so a trigger may name the
    shape it wants for itself.
    """

    def _overlay(self, **kwargs):
        return notify.NotificationOverlay(duration=2.0, led_count=17,
                                          style=notify.STYLE_BLOOM, **kwargs)

    def test_the_named_shape_is_used(self):
        overlay = self._overlay()
        overlay.trigger("comet:#1a9fff", 0.0)
        self.assertEqual(overlay.current.style, notify.STYLE_COMET)
        self.assertEqual(overlay.current.color, (26, 159, 255))

    def test_a_kind_may_be_named_instead_of_a_colour(self):
        overlay = self._overlay()
        overlay.trigger("pulse:achievement", 0.0)
        self.assertEqual(overlay.current.style, notify.STYLE_PULSE)
        self.assertEqual(overlay.current.color, notify.KINDS["achievement"])

    def test_without_a_prefix_nothing_changes(self):
        overlay = self._overlay()
        overlay.trigger("achievement", 0.0)
        self.assertEqual(overlay.current.style, notify.STYLE_BLOOM)

    def test_an_unknown_prefix_is_not_a_prefix(self):
        # It stays part of the colour, which then fails to parse - so nonsense
        # is refused rather than silently flashed in the default shape.
        overlay = self._overlay()
        self.assertFalse(overlay.trigger("kaleidoscope:#1a9fff", 0.0))
        self.assertIsNone(overlay.current)

    def test_a_shape_with_no_colour_is_refused(self):
        overlay = self._overlay()
        self.assertFalse(overlay.trigger("comet:", 0.0))

    def test_two_shapes_of_one_colour_are_two_triggers(self):
        # Same colour, so without this the second would be read as a repeat of
        # the first and dropped by the quiet time - which would make the
        # buttons on the Test tab look broken.
        overlay = self._overlay()
        self.assertTrue(overlay.trigger("comet:#1a9fff", 0.0))
        self.assertTrue(overlay.trigger("pulse:#1a9fff", 0.0))
        self.assertEqual(len(overlay.pending), 1)

    def test_a_queued_one_keeps_the_shape_it_asked_for(self):
        overlay = self._overlay()
        overlay.trigger("comet:#1a9fff", 0.0)
        overlay.trigger("alternate:#1a9fff", 0.0)
        overlay.frame(2.5)              # the first is over, the queue moves on
        self.assertEqual(overlay.current.style, notify.STYLE_ALTERNATE)

    def test_it_overrides_even_a_fixed_kind(self):
        # Only someone writing into the pipe by hand can ask for this, and
        # ignoring what they typed would be the more surprising answer.
        overlay = self._overlay()
        overlay.trigger("bloom:warning", 0.0)
        self.assertEqual(overlay.current.style, notify.STYLE_BLOOM)

    def test_a_warning_on_its_own_is_still_fixed(self):
        # Which is the property that matters: nothing that detects a warning
        # names a shape, so the automatic one looks the same everywhere.
        overlay = self._overlay()
        overlay.trigger("warning", 0.0)
        self.assertEqual(overlay.current.style, notify.STYLE_ALTERNATE)

    def test_splitting_knows_the_shapes_from_the_colours(self):
        self.assertEqual(notify.split_shape("comet:#1a9fff"),
                         ("comet", "#1a9fff"))
        self.assertEqual(notify.split_shape("#1a9fff"), (None, "#1a9fff"))
        self.assertEqual(notify.split_shape("10,20,30"), (None, "10,20,30"))
        self.assertEqual(notify.split_shape("nope:#1a9fff"),
                         (None, "nope:#1a9fff"))


class FixedWarningTest(unittest.TestCase):
    """A warning looks the same everywhere, and nothing can change that.

    The other three are yours to arrange - which is fine, you know what your
    own bar means. A warning is the one you must not have to recognise, so it
    is red and the alarm shape on every machine, whatever the settings say.
    """

    def _overlay(self, **kwargs):
        overlay = notify.NotificationOverlay(duration=2.0, led_count=17,
                                             **kwargs)
        overlay.trigger("warning", 0.0)
        return overlay

    def test_it_is_red(self):
        self.assertEqual(notify.KINDS["warning"], (255, 0, 0))

    def test_the_general_shape_does_not_apply_to_it(self):
        overlay = self._overlay(style=notify.STYLE_BLOOM)
        self.assertEqual(overlay.current.style, notify.STYLE_ALTERNATE)

    def test_a_shape_handed_in_for_it_is_ignored(self):
        overlay = self._overlay(styles={"warning": notify.STYLE_COMET})
        self.assertEqual(overlay.current.style, notify.STYLE_ALTERNATE)

    def test_a_colour_handed_in_for_it_is_ignored(self):
        overlay = self._overlay(colors={"warning": (0, 255, 0)})
        self.assertEqual(overlay.current.color, (255, 0, 0))
        self.assertEqual(overlay.colors["warning"], (255, 0, 0))

    def test_the_other_kinds_are_still_free(self):
        overlay = notify.NotificationOverlay(
            duration=2.0, led_count=17, style=notify.STYLE_PULSE,
            colors={"achievement": (1, 2, 3)})
        overlay.trigger("achievement", 0.0)
        self.assertEqual(overlay.current.style, notify.STYLE_PULSE)
        self.assertEqual(overlay.current.color, (1, 2, 3))


class BrightnessCeilingTest(unittest.TestCase):
    """A flash has to respect MAX_BRIGHTNESS, and for more than looks.

    People cap it because the strip runs off the ESP's USB rail, and a flash
    is the worst case there: the whole bar lit at once. Measured before this
    was fixed - a capped strip rendered at 80 and flashed at 254.
    """

    def _peak(self, cap, kind="achievement"):
        overlay = notify.NotificationOverlay(duration=2.0, led_count=17,
                                             max_brightness=cap)
        overlay.trigger(kind, 0.0)
        return max(overlay.frame(0.6))

    def test_a_capped_strip_flashes_within_its_cap(self):
        self.assertLessEqual(self._peak(80), 80)

    def test_the_cap_scales_rather_than_clips(self):
        # The same proportion the renderer applies, so a flash at a cap looks
        # like the rest of the bar rather than a differently shaped thing.
        full, capped = self._peak(255), self._peak(128)
        self.assertAlmostEqual(capped / full, 128 / 255.0, places=1)

    def test_no_cap_leaves_the_colour_alone(self):
        overlay = notify.NotificationOverlay(duration=2.0, led_count=17)
        overlay.trigger("warning", 0.0)
        self.assertEqual(max(overlay.frame(0.05)), 255)

    def test_it_applies_to_every_kind_and_to_a_bare_colour(self):
        for kind in ("achievement", "message", "friend", "warning",
                     "#00ff88"):
            self.assertLessEqual(self._peak(40, kind), 40, kind)

    def test_zero_is_dark_rather_than_full(self):
        # A cap of 0 is a strip someone wants silent; the flash is not an
        # exception to that.
        self.assertEqual(self._peak(0), 0)

    def test_no_floor_is_imposed_the_way_the_renderer_imposes_one(self):
        # MIN_BRIGHTNESS is a floor under what Steam asked for, and a flash
        # asks for nothing. It also has to fade to nothing at both ends, or
        # two in a row would run together - so the overlay takes no floor.
        overlay = notify.NotificationOverlay(duration=2.0, led_count=17)
        overlay.trigger("achievement", 0.0)
        self.assertEqual(max(overlay.frame(0.0)), 0, "it starts from nothing")
        self.assertLess(max(overlay.frame(1.999)), 5, "and returns to it")


class ConfiguredOverlayTest(unittest.TestCase):
    """What the service hands the overlay, straight from a configuration."""

    def _config(self, **overrides):
        from steamos_led import config as config_module
        settings = dict(config_module.DEFAULTS)
        settings.update(overrides)
        return settings

    def test_a_shape_left_at_the_default_is_not_passed_on(self):
        # "default" is the absence of a choice, so it must not arrive at the
        # overlay as a shape name - there is none by that name to draw.
        from steamos_led import service
        self.assertEqual(service.notification_styles(self._config()), {})

    def test_a_shape_of_its_own_is_passed_on(self):
        from steamos_led import service
        styles = service.notification_styles(
            self._config(MESSAGE_STYLE=notify.STYLE_PULSE))
        self.assertEqual(styles, {"message": notify.STYLE_PULSE})

    def test_the_colours_come_from_the_same_table(self):
        from steamos_led import config as config_module
        from steamos_led import service
        colors = service.notification_colors(self._config())
        self.assertEqual(sorted(colors),
                         sorted(kind for kind, _prefix
                                in config_module.CONFIGURABLE_KINDS))

    def test_every_configurable_kind_is_one_the_overlay_knows(self):
        # A prefix with no matching trigger word would be a setting that
        # changes nothing at all, and nothing would say so.
        from steamos_led import config as config_module
        for kind, _prefix in config_module.CONFIGURABLE_KINDS:
            self.assertIn(kind, notify.KINDS)

    def test_every_kind_is_either_configurable_or_deliberately_fixed(self):
        # And the other way round: a trigger word that is in neither table is
        # one nobody decided about, which is how warning started out - it had
        # no options because it had been forgotten, not because it was fixed.
        from steamos_led import config as config_module
        configurable = {kind for kind, _prefix
                        in config_module.CONFIGURABLE_KINDS}
        self.assertEqual(configurable | set(notify.FIXED_KINDS),
                         set(notify.KINDS))
        self.assertFalse(configurable & set(notify.FIXED_KINDS),
                         "a kind cannot be both")


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


class ShapeTestCase(unittest.TestCase):
    """Shared scaffolding: one overlay, sampled at a point in the flash."""

    LEDS = 17
    DURATION = 3.5
    STYLE = None

    def _overlay(self, duration=None, led_count=None, **kwargs):
        overlay = notify.NotificationOverlay(
            duration=self.DURATION if duration is None else duration,
            led_count=self.LEDS if led_count is None else led_count,
            style=self.STYLE, **kwargs)
        overlay.trigger("achievement", 0.0)
        return overlay

    def _levels(self, overlay, when):
        """Per-LED brightness, 0..255, `when` seconds into the flash."""
        frame = overlay.frame(when)
        self.assertIsNotNone(frame, "the flash was already over at %.3fs" % when)
        return [frame[led * 3] for led in range(len(frame) // 3)]


class DoubleFlashShapeTest(ShapeTestCase):
    """Blink, blink, wait - and again, at the same speed however long it runs."""

    STYLE = notify.STYLE_DOUBLE_FLASH

    def _lit_spans(self, overlay, duration, fps=60):
        """(start, end) in seconds of each stretch the bar is lit, as rendered.

        Sampled at the frame rate the service actually runs while a flash is
        on - anything shorter than a frame is not a blink, it is a rumour.
        """
        spans, start = [], None
        for step in range(int(duration * fps)):
            now = step / fps
            on = max(self._levels(overlay, now)) > 0
            if on and start is None:
                start = now
            elif not on and start is not None:
                spans.append((start, now))
                start = None
        if start is not None:
            spans.append((start, duration))
        return spans

    def test_it_blinks_in_pairs(self):
        overlay = self._overlay()
        spans = self._lit_spans(overlay, self.DURATION)
        self.assertEqual(len(spans), 2 * round(self.DURATION
                                               / notify.FLASH_PERIOD))

    def test_the_two_of_a_pair_are_closer_than_the_pairs_are(self):
        # That is the whole shape: what makes it read as *double* is that the
        # gap inside a pair is much shorter than the gap to the next one.
        overlay = self._overlay()
        spans = self._lit_spans(overlay, self.DURATION)
        within = spans[1][0] - spans[0][1]
        between = spans[2][0] - spans[1][1]
        self.assertLess(within * 3, between)

    def test_a_blink_is_short_but_lasts_more_than_one_frame(self):
        overlay = self._overlay()
        for start, end in self._lit_spans(overlay, self.DURATION):
            self.assertGreater(end - start, 2.0 / 60,
                               "a blink nobody can see is not a blink")
            self.assertLess(end - start, 0.2, "that is a pulse, not a blink")

    def test_a_longer_flash_means_more_pairs_not_slower_ones(self):
        # The point of measuring in seconds: a strobe that slows down when the
        # notification is made longer has stopped being a strobe.
        short = self._lit_spans(self._overlay(duration=3.5), 3.5)
        long = self._lit_spans(self._overlay(duration=10.0), 10.0)
        self.assertGreater(len(long), len(short))
        self.assertAlmostEqual(short[0][1] - short[0][0],
                               long[0][1] - long[0][0], places=2)

    def test_the_bar_lights_as_one(self):
        overlay = self._overlay()
        levels = self._levels(overlay, notify.FLASH_LIT / 2)
        self.assertEqual(len(set(levels)), 1)
        self.assertEqual(levels[0], 255)

    def test_it_ends_dark(self):
        # A pair that ran off the end would leave the bar lit until the next
        # flash blanked it, and two in a row would run together.
        overlay = self._overlay()
        self.assertEqual(max(self._levels(overlay, self.DURATION - 0.01)), 0)

    def test_a_whole_number_of_pairs_fits_in_any_duration(self):
        for duration in (1.0, 1.5, 2.0, 3.5, 7.0, 10.0, 60.0):
            overlay = self._overlay(duration=duration)
            spans = self._lit_spans(overlay, duration)
            self.assertEqual(len(spans) % 2, 0,
                             "half a pair at %.1fs" % duration)

    def test_a_duration_too_short_for_a_pair_gets_a_compressed_one(self):
        # Below what the panel offers, so only a hand-edited config gets here -
        # but a truncated pair would be a single blink, which is a different
        # shape saying a different thing.
        overlay = self._overlay(duration=0.2)
        spans = self._lit_spans(overlay, 0.2, fps=1000)
        self.assertEqual(len(spans), 2)

    def test_works_on_other_strip_lengths(self):
        for count in (1, 2, 5, 30, 144):
            overlay = self._overlay(led_count=count)
            levels = self._levels(overlay, notify.FLASH_LIT / 2)
            self.assertEqual(len(levels), count)
            self.assertEqual(min(levels), 255)


class CometShapeTest(ShapeTestCase):
    """One head with a tail, travelling the bar once."""

    STYLE = notify.STYLE_COMET

    def _head(self, levels):
        return levels.index(max(levels))

    def test_it_starts_and_ends_dark(self):
        overlay = self._overlay()
        self.assertEqual(max(self._levels(overlay, 0.0)), 0)
        self.assertEqual(max(self._levels(overlay, self.DURATION - 0.001)), 0)

    def test_the_head_moves_forward(self):
        overlay = self._overlay()
        seen = [self._head(self._levels(overlay, self.DURATION * fraction))
                for fraction in (0.2, 0.4, 0.6, 0.8)]
        self.assertEqual(seen, sorted(seen))
        self.assertLess(seen[0], seen[-1])

    def test_it_crosses_the_whole_bar(self):
        # Both ends have to be the brightest LED at some point, or the comet
        # appears out of nothing or dies before it arrives.
        overlay = self._overlay()
        heads = {self._head(self._levels(overlay, step / 200.0 * self.DURATION))
                 for step in range(200)}
        self.assertIn(0, heads)
        self.assertIn(self.LEDS - 1, heads)

    def test_the_tail_is_behind_the_head_and_fades(self):
        overlay = self._overlay()
        levels = self._levels(overlay, self.DURATION * 0.5)
        head = self._head(levels)
        self.assertGreater(head, 1)
        self.assertGreater(levels[head - 1], 0, "there should be a tail")
        self.assertLess(levels[head - 1], levels[head])
        self.assertGreater(levels[head - 1], levels[head - 2],
                           "and it has to fade away from the head")

    def test_nothing_is_lit_ahead_of_the_head(self):
        # Bar the front edge itself, which is deliberately soft: a hard step
        # looks blocky on a short strip, so the LED the head is arriving at is
        # already partly lit. Beyond that it must be dark.
        width = max(notify.COMET_MIN_HEAD, notify.COMET_HEAD * self.LEDS)
        overlay = self._overlay()
        levels = self._levels(overlay, self.DURATION * 0.5)
        ahead = self._head(levels) + 1 + int(width)
        self.assertEqual(levels[ahead:], [0] * (self.LEDS - ahead))

    def test_the_tail_covers_a_useful_part_of_the_bar(self):
        overlay = self._overlay()
        levels = self._levels(overlay, self.DURATION * 0.7)
        self.assertGreater(sum(1 for level in levels if level > 0), 3)

    def test_it_is_not_symmetric(self):
        # What tells it apart from the bloom at a glance.
        overlay = self._overlay()
        levels = self._levels(overlay, self.DURATION * 0.5)
        self.assertNotEqual(levels, levels[::-1])

    def test_reverse_turns_it_round(self):
        # A flash never passes the renderer, so REVERSE has to be applied here
        # or the comet runs against every other effect on the strip.
        forward = self._levels(self._overlay(), self.DURATION * 0.4)
        backward = self._levels(self._overlay(reverse=True),
                                self.DURATION * 0.4)
        self.assertEqual(backward, forward[::-1])

    def test_reverse_leaves_a_symmetric_shape_alone(self):
        for style in (notify.STYLE_BLOOM, notify.STYLE_PULSE,
                      notify.STYLE_DOUBLE_FLASH):
            plain = notify.NotificationOverlay(duration=2.0, led_count=self.LEDS,
                                               style=style)
            flipped = notify.NotificationOverlay(duration=2.0,
                                                 led_count=self.LEDS,
                                                 style=style, reverse=True)
            for overlay in (plain, flipped):
                overlay.trigger("achievement", 0.0)
            self.assertEqual(plain.frame(0.5), flipped.frame(0.5), style)

    def test_works_on_other_strip_lengths(self):
        for count in (1, 2, 5, 30, 144):
            overlay = self._overlay(led_count=count)
            lit = [max(self._levels(overlay, step / 50.0 * self.DURATION))
                   for step in range(1, 50)]
            self.assertEqual(len(self._levels(overlay, 1.0)), count)
            self.assertGreater(max(lit), 0, "%d LEDs stayed dark" % count)


class AlternateShapeTest(ShapeTestCase):
    """Two halves in antiphase - the shape that says something is wrong."""

    STYLE = notify.STYLE_ALTERNATE

    def _sides(self, levels):
        """(left lit, right lit), counting either half separately."""
        half = len(levels) // 2
        gap = 1 if len(levels) % 2 and len(levels) >= 5 else 0
        return (sum(1 for level in levels[:half] if level > 0),
                sum(1 for level in levels[half + gap:] if level > 0))

    def _phase(self, when):
        return self._sides(self._levels(self._overlay(), when))

    def test_one_side_at_a_time(self):
        # Whichever moment you look at, the bar is never lit on both sides -
        # that is the whole shape, and a bar lit throughout is a pulse.
        overlay = self._overlay()
        for step in range(200):
            left, right = self._sides(
                self._levels(overlay, step / 200.0 * self.DURATION))
            self.assertFalse(left and right, "both sides at step %d" % step)

    def test_both_sides_get_their_turn(self):
        seen = {self._phase(step / 100.0 * self.DURATION)
                for step in range(100)}
        self.assertIn((8, 0), seen, "the left half alone")
        self.assertIn((0, 8), seen, "the right half alone")

    def test_a_lit_side_is_lit_whole(self):
        overlay = self._overlay()
        levels = self._levels(overlay, notify.ALTERNATE_PERIOD * 0.2)
        self.assertEqual(levels[:self.LEDS // 2], [255] * (self.LEDS // 2))

    def test_the_middle_led_stays_dark_on_an_odd_strip(self):
        # It belongs to neither side, and the gap is what makes two halves
        # read as two rather than as a bar with a moving edge.
        overlay = self._overlay()
        for step in range(50):
            levels = self._levels(overlay, step / 50.0 * self.DURATION)
            self.assertEqual(levels[self.LEDS // 2], 0, "step %d" % step)

    def test_it_switches_at_its_own_rate(self):
        # Seconds, not fractions: an alarm that slows down when the flash is
        # made longer stops looking like an alarm.
        overlay = self._overlay(duration=10.0)
        changes = 0
        previous = None
        for step in range(2000):
            phase = self._sides(self._levels(overlay, step / 200.0))
            if previous is not None and phase != previous:
                changes += 1
            previous = phase
        self.assertGreater(changes, 30, "10 s should hold about 20 periods")

    def test_it_ends_dark(self):
        overlay = self._overlay()
        self.assertEqual(max(self._levels(overlay, self.DURATION - 0.01)), 0)

    def test_it_is_the_shape_a_warning_gets(self):
        overlay = notify.NotificationOverlay(duration=self.DURATION,
                                             led_count=self.LEDS)
        overlay.trigger("warning", 0.0)
        left, right = self._sides(self._levels(overlay, 0.05))
        self.assertTrue(left and not right)

    def test_works_on_other_strip_lengths(self):
        for count in (1, 2, 3, 5, 30, 144):
            overlay = self._overlay(led_count=count)
            lit = [max(self._levels(overlay, step / 40.0 * self.DURATION))
                   for step in range(40)]
            self.assertEqual(len(self._levels(overlay, 0.05)), count)
            self.assertGreater(max(lit), 0, "%d LEDs stayed dark" % count)


class SparkleShapeTest(ShapeTestCase):
    """Glitter: every LED on its own little clock, and none of them agreeing."""

    STYLE = notify.STYLE_SPARKLE

    def _samples(self, overlay, count=200):
        return [self._levels(overlay, step / float(count) * self.DURATION)
                for step in range(count)]

    def test_the_leds_do_not_move_together(self):
        # The whole point. If they shared a clock this would be the pulse,
        # drawn the hard way.
        overlay = self._overlay()
        agreed = 0
        for levels in self._samples(overlay):
            lit = [level > 0 for level in levels]
            if all(lit) or not any(lit):
                agreed += 1
        self.assertLess(agreed, 20, "the strip is blinking as one")

    def test_every_led_gets_a_turn(self):
        # An LED whose grain never fires would look like a dead pixel on a
        # strip somebody just bought.
        overlay = self._overlay()
        best = [0] * self.LEDS
        for levels in self._samples(overlay):
            best = [max(seen, level) for seen, level in zip(best, levels)]
        self.assertTrue(all(peak > 200 for peak in best), best)

    def test_a_grain_decays_rather_than_switching_off(self):
        # Sampled inside one grain's life: it has to be on the way down, not
        # holding at full and then vanishing.
        overlay = self._overlay()
        for led in range(self.LEDS):
            trace = [levels[led] for levels in self._samples(overlay, 400)]
            peak = max(trace)
            self.assertGreater(len({level for level in trace
                                    if 0 < level < peak}), 2, led)

    def test_it_ends_dark(self):
        overlay = self._overlay()
        self.assertEqual(max(self._levels(overlay, self.DURATION - 0.001)), 0)

    def test_it_fades_out_rather_than_stopping(self):
        # A grain cut off mid-life reads as a fault rather than as an ending.
        overlay = self._overlay()
        late = max(self._levels(overlay, self.DURATION * 0.95))
        early = max(max(levels) for levels in self._samples(overlay, 40)[:20])
        self.assertLess(late, early)

    def test_the_tempo_does_not_stretch_with_the_duration(self):
        # Timed in seconds like the flashes: a longer notification means more
        # glitter, not slower glitter.
        short = self._overlay(duration=2.0)
        long = self._overlay(duration=8.0)
        self.assertEqual(self._levels(short, 0.4), self._levels(long, 0.4))

    def test_the_same_moment_draws_the_same_frame(self):
        # Spread by arithmetic rather than by a random draw, so a dropped
        # frame resumes where it would have been.
        self.assertEqual(self._levels(self._overlay(), 1.234),
                         self._levels(self._overlay(), 1.234))

    def test_works_on_other_strip_lengths(self):
        for count in (1, 2, 3, 5, 30, 144):
            overlay = self._overlay(led_count=count)
            lit = [max(self._levels(overlay, step / 40.0 * self.DURATION))
                   for step in range(40)]
            self.assertEqual(len(self._levels(overlay, 0.05)), count)
            self.assertGreater(max(lit), 0, "%d LEDs stayed dark" % count)


if __name__ == "__main__":
    unittest.main()
