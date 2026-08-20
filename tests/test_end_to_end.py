# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""Runs the real service process against a fake shim and a fake ESP.

The shim is stood in for by a FIFO: the service only ever opens, polls and
reads it, which a FIFO models well enough for the main loop. The ESP side is
a pty with a minimal responder. No hardware and no kernel module involved.
"""

import os
import pty
import subprocess
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "server"))

from steamos_led import link, notify, shim  # noqa: E402

ENTRY_POINT = os.path.join(HERE, "..", "server", "steamos-led-serial")
RUN_SECONDS = 2.5
# The notification case needs room for the flash to finish inside the window.
NOTIFY_RUN_SECONDS = 5.0


class ShimWriter(threading.Thread):
    """Feeds snapshots into the FIFO, switching effect part way through."""

    daemon = True

    def __init__(self, path, switch_at=10):
        super().__init__()
        self.path = path
        self.switch_at = switch_at      # None keeps the static colour throughout
        self.stop = threading.Event()

    def run(self):
        try:
            fd = os.open(self.path, os.O_WRONLY)  # blocks until the service opens it
        except OSError:
            return
        snapshot = shim.make_snapshot(shim.EFFECT_MANUAL, (10, 200, 30))
        seq = 0
        try:
            while not self.stop.is_set():
                seq += 1
                if self.switch_at is not None and seq == self.switch_at:
                    snapshot = shim.make_snapshot(shim.EFFECT_RAINBOW)
                try:
                    os.write(fd, shim.encode(snapshot, seq))
                except OSError:
                    return
                time.sleep(0.05)
        finally:
            os.close(fd)


class EndToEndTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.fifo = os.path.join(self.tmpdir, "valve-leds-shim")
        os.mkfifo(self.fifo)
        self.master, self.slave = pty.openpty()
        self.device = os.ttyname(self.slave)
        self.writer = None
        self.addCleanup(self._teardown)

    def start_writer(self, switch_at=10):
        self.writer = ShimWriter(self.fifo, switch_at=switch_at)
        self.writer.start()
        return self.writer

    def _teardown(self):
        if self.writer is not None:
            self.writer.stop.set()
        for fd in (self.master, self.slave):
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            os.unlink(self.fifo)
            os.rmdir(self.tmpdir)
        except OSError:
            pass

    def _start_service(self, *extra, **env_overrides):
        env = dict(os.environ)
        env.update(env_overrides)
        proc = subprocess.Popen(
            [sys.executable, ENTRY_POINT,
             "--config", "/dev/null",
             "--device", self.fifo,
             "--serial-port", self.device,
             "--leds", "17", "--fps", "40", "-v", *extra],
            stderr=subprocess.PIPE, text=True, env=env)
        self.addCleanup(proc.kill)
        return proc

    def _collect(self, proc, seconds, on_frame=None):
        """Answer the handshake and gather FRAME payloads for a while."""
        parser = link.FrameParser()
        frames = []
        os.set_blocking(self.master, False)
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                data = os.read(self.master, 16384)
            except BlockingIOError:
                time.sleep(0.02)
                continue
            for msg_type, payload in parser.feed(data):
                if msg_type == link.MSG_HELLO:
                    info = bytes([1]) + (300).to_bytes(2, "little") + bytes([2])
                    os.write(self.master, link.build(link.MSG_INFO, info + b"fake"))
                elif msg_type == link.MSG_FRAME:
                    frames.append(payload)
                    if on_frame is not None:
                        on_frame(len(frames))
        del proc
        return frames

    def test_snapshots_reach_the_strip(self):
        self.start_writer()
        proc = self._start_service()

        frames = self._collect(proc, RUN_SECONDS)

        proc.terminate()
        try:
            _, stderr = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            _, stderr = proc.communicate()

        self.assertTrue(frames, "no frames reached the fake ESP:\n%s" % stderr)

        # Frame layout: LED count followed by RGB triplets.
        self.assertEqual(int.from_bytes(frames[0][:2], "little"), 17)
        self.assertEqual(len(frames[0]), 2 + 17 * 3)

        # The static colour Steam "set" must arrive unchanged...
        self.assertEqual(tuple(frames[0][2:5]), (10, 200, 30))
        # ...and the rainbow that follows must actually animate.
        self.assertGreater(len({bytes(frame[2:8]) for frame in frames}), 5)

        # SIGTERM has to blank the strip on the way out.
        self.assertIn("shutting down", stderr)

    def test_notification_takes_over_the_bar_and_gives_it_back(self):
        # Hold a static colour so "went back to normal" is unambiguous.
        self.start_writer(switch_at=None)
        notify_fifo = os.path.join(self.tmpdir, "notify")
        # Shorter than the test window so the hand-back is observable. Set
        # through the environment, which also exercises that override path.
        proc = self._start_service("--notify-fifo", notify_fifo,
                                   STEAMOS_LED_NOTIFY_DURATION="0.6")

        # Fire as soon as frames flow, and leave a wide margin afterwards: the
        # link waits out the ESP boot delay first, so frames only start about
        # two seconds in. Triggering late used to run past the window and made
        # this test flaky.
        def maybe_trigger(count):
            if count == 3:
                notify.send(notify_fifo, "achievement")

        frames = self._collect(proc, NOTIFY_RUN_SECONDS, on_frame=maybe_trigger)
        proc.terminate()
        try:
            _, stderr = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            _, stderr = proc.communicate()

        self.assertTrue(frames, "no frames at all:\n%s" % stderr)
        self.assertIn("notification: achievement", stderr)

        # The achievement colour, read out of the table rather than described
        # here: "red > green > 0" was gold, and stopped being an achievement
        # at all when that colour moved. Mid-flash every channel is scaled by
        # the envelope, so it is the proportions that are compared.
        wanted = notify.KINDS[notify.KIND_ACHIEVEMENT]

        def is_flash(frame):
            shown = (frame[2], frame[3], frame[4])
            if max(shown) == 0:
                return False
            return all(abs(is_ / max(shown) - was / max(wanted)) < 0.02
                       for was, is_ in zip(wanted, shown))

        lit = [index for index, frame in enumerate(frames) if is_flash(frame)]
        self.assertTrue(lit, "the bar never flashed:\n%s" % stderr)

        # Steam's own colour was green (10, 200, 30) - it has to come back.
        after = frames[lit[-1] + 1:]
        self.assertTrue(after, "flash ran to the end of the test, cannot tell")
        self.assertEqual(tuple(after[-1][2:5]), (10, 200, 30),
                         "the bar did not go back to what Steam is showing")


if __name__ == "__main__":
    unittest.main()
