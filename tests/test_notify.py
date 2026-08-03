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
        self.assertEqual(notify.parse_color("MESSAGE"), (0, 120, 255))

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
        # Every LED shows the same colour...
        self.assertEqual(len({frame[i:i + 3] for i in range(0, 12, 3)}), 1)
        # ...and it is gold: red high, some green, no blue.
        red, green, blue = frame[0], frame[1], frame[2]
        self.assertGreater(red, green)
        self.assertGreater(green, blue)
        self.assertEqual(blue, 0)

    def test_hands_the_bar_back_when_it_expires(self):
        self.overlay.trigger("achievement", 100.0)
        self.assertNotEqual(self.overlay.apply(self.base, 100.5), self.base)
        self.assertEqual(self.overlay.apply(self.base, 102.1), self.base)
        self.assertFalse(self.overlay.active)

    def test_brightness_varies_over_the_flash(self):
        self.overlay.trigger("achievement", 0.0)
        levels = {self.overlay.apply(self.base, step / 40.0)[0]
                  for step in range(80)}
        self.assertGreater(len(levels), 5, "flash should pulse, not sit still")

    def test_ends_dark_so_there_is_no_hard_edge(self):
        self.overlay.trigger("achievement", 0.0)
        tail = self.overlay.apply(self.base, 1.98)
        self.assertLess(max(tail[:3]), 60)

    def test_retrigger_restarts_the_flash(self):
        self.overlay.trigger("achievement", 0.0)
        self.overlay.trigger("message", 1.0)
        frame = self.overlay.apply(self.base, 1.5)
        self.assertGreater(frame[2], frame[0], "should be the blue one now")

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
        self.assertEqual(frame[0], 0)
        self.assertEqual(frame[1], 0)
        self.assertGreater(frame[2], 0)


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


if __name__ == "__main__":
    unittest.main()
