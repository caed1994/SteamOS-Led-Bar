"""End-to-end test of the serial layer against a fake ESP on a pty.

Exercises the real termios configuration, the handshake and frame delivery
without any hardware attached.
"""

import os
import pty
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "server"))

from steamos_led import link, render, shim  # noqa: E402
from steamos_led.serialport import SerialPort  # noqa: E402

BAUD = 115200


class FakeEsp(threading.Thread):
    """Answers HELLO with INFO and records everything the host sends."""

    daemon = True

    def __init__(self, master_fd):
        super().__init__()
        self.fd = master_fd
        self.parser = link.FrameParser()
        self.received = []
        self.lock = threading.Lock()
        self.running = True

    def run(self):
        while self.running:
            try:
                data = os.read(self.fd, 4096)
            except OSError:
                # EIO simply means no process currently holds the slave end.
                time.sleep(0.01)
                continue
            if not data:
                time.sleep(0.01)
                continue
            for msg_type, payload in self.parser.feed(data):
                with self.lock:
                    self.received.append((msg_type, payload))
                if msg_type == link.MSG_HELLO:
                    info = bytes([1]) + (300).to_bytes(2, "little") + bytes([2])
                    os.write(self.fd, link.build(link.MSG_INFO,
                                                 info + b"fake-esp"))
                elif msg_type == link.MSG_PING:
                    os.write(self.fd, link.build(link.MSG_PONG))

    def wait_for(self, msg_type, timeout=3.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.lock:
                for received_type, payload in self.received:
                    if received_type == msg_type:
                        return payload
            time.sleep(0.01)
        return None


class SerialLoopbackTest(unittest.TestCase):
    def setUp(self):
        self.master, self.slave = pty.openpty()
        self.device = os.ttyname(self.slave)
        self.esp = None
        self.addCleanup(self._teardown)

    def start_esp(self):
        """Attach the fake ESP. Only for tests that do not read the master
        themselves - the reader thread would otherwise swallow their bytes."""
        # The slave fd stays open on purpose: with no process on that end,
        # reads on the master fail with EIO.
        self.esp = FakeEsp(self.master)
        self.esp.start()
        return self.esp

    def _teardown(self):
        if self.esp is not None:
            self.esp.running = False
        for fd in (self.master, self.slave):
            try:
                os.close(fd)
            except OSError:
                pass

    def test_raw_port_roundtrip(self):
        port = SerialPort(self.device, BAUD)
        self.addCleanup(port.close)
        port.write(b"hello esp")
        self.assertEqual(os.read(self.master, 64), b"hello esp")

        os.write(self.master, b"hello host")
        self.assertTrue(port.wait_readable(2.0))
        self.assertEqual(port.read(64), b"hello host")

    def test_handshake_and_frames(self):
        esp = self.start_esp()
        bridge = link.EspLink(port=self.device, baudrate=BAUD, led_count=17)
        bridge.BOOT_DELAY = 0.05
        self.addCleanup(bridge.disconnect)

        self.assertTrue(bridge.connect())
        self.assertIsNotNone(bridge.info, "no INFO reply parsed")
        self.assertEqual(bridge.info.max_leds, 300)
        self.assertEqual(bridge.info.name, "fake-esp")

        payload = render.Renderer(led_count=17).render(
            shim.make_snapshot(shim.EFFECT_MANUAL, (10, 20, 30)), 0.0)
        self.assertTrue(bridge.send_frame(payload, 17))

        received = esp.wait_for(link.MSG_FRAME)
        self.assertIsNotNone(received, "frame never arrived at the fake ESP")
        self.assertEqual(int.from_bytes(received[:2], "little"), 17)
        self.assertEqual(received[2:], payload)
        self.assertEqual(received[2:5], bytes((10, 20, 30)))

    def test_blank_on_shutdown(self):
        esp = self.start_esp()
        bridge = link.EspLink(port=self.device, baudrate=BAUD, led_count=17)
        bridge.BOOT_DELAY = 0.05
        self.assertTrue(bridge.connect())
        bridge.shutdown()
        self.assertIsNotNone(esp.wait_for(link.MSG_BLANK))
        self.assertFalse(bridge.connected)

    def test_reconnect_after_device_disappears(self):
        bridge = link.EspLink(port="/dev/does-not-exist", baudrate=BAUD,
                              led_count=17, reconnect_delay=0.0)
        self.assertFalse(bridge.connect())
        self.assertFalse(bridge.connected)
        # A dead link must not raise, it just reports the send as failed.
        self.assertFalse(bridge.send_frame(b"\x00" * 51, 17))


if __name__ == "__main__":
    unittest.main()
