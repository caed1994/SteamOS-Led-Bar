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

from steamos_led import link, shim  # noqa: E402

ENTRY_POINT = os.path.join(HERE, "..", "server", "steamos-led-serial")
RUN_SECONDS = 2.5


class ShimWriter(threading.Thread):
    """Feeds snapshots into the FIFO, switching effect part way through."""

    daemon = True

    def __init__(self, path):
        super().__init__()
        self.path = path
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
                if seq == 10:
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
        self.writer = ShimWriter(self.fifo)
        self.writer.start()
        self.addCleanup(self._teardown)

    def _teardown(self):
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

    def test_snapshots_reach_the_strip(self):
        proc = subprocess.Popen(
            [sys.executable, ENTRY_POINT,
             "--config", "/dev/null",
             "--device", self.fifo,
             "--serial-port", self.device,
             "--leds", "17", "--fps", "40", "-v"],
            stderr=subprocess.PIPE, text=True)
        self.addCleanup(proc.kill)

        parser = link.FrameParser()
        frames = []
        os.set_blocking(self.master, False)
        deadline = time.monotonic() + RUN_SECONDS
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


if __name__ == "__main__":
    unittest.main()
