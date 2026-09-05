# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""End-to-end test of the serial layer against a fake ESP on a pty.

Exercises the real termios configuration, the handshake and frame delivery
without any hardware attached.
"""

import os
import pty
import re
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "server"))

from steamos_utility_center import link, render, shim  # noqa: E402
from steamos_utility_center.serialport import SerialError, SerialPort  # noqa: E402

BAUD = 115200


class FakeEsp(threading.Thread):
    """Answers HELLO with INFO and records everything the host sends."""

    daemon = True

    def __init__(self, master_fd, caps=None):
        super().__init__()
        self.fd = master_fd
        self.parser = link.FrameParser()
        self.received = []
        self.lock = threading.Lock()
        self.running = True
        # None is a board flashed before CAPS existed, which answers HELLO
        # with INFO and nothing else.
        self.caps = caps

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
                    # CAPS first, as the firmware sends it: the host returns
                    # from its handshake on INFO.
                    if self.caps is not None:
                        os.write(self.fd, link.build(link.MSG_CAPS,
                                                     bytes([self.caps])))
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


class FirmwareAgreesTest(unittest.TestCase):
    """The numbers on the wire live in two files, so they can disagree.

    The firmware is C++ and no test compiles it here. It is read as text
    instead, which catches the one mistake that matters: a number changed on
    one side of the cable and not on the other. Such a mistake is not a
    failure that anybody sees. It is a strip that draws the wrong thing.
    """

    SOURCE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "..", "firmware", "led-client", "src", "main.cpp")

    def setUp(self):
        with open(self.SOURCE, "r", encoding="utf-8") as handle:
            self.text = handle.read()

    def _value(self, name):
        found = re.search(r"static const uint8_t %s = (0x[0-9a-fA-F]+|\d+);"
                          % name, self.text)
        self.assertIsNotNone(found, "%s is not in the firmware" % name)
        return int(found.group(1), 0)

    def test_the_message_numbers_agree(self):
        for name, wanted in (("MSG_HELLO", link.MSG_HELLO),
                             ("MSG_INFO", link.MSG_INFO),
                             ("MSG_CAPS", link.MSG_CAPS),
                             ("MSG_STANDBY", link.MSG_STANDBY)):
            self.assertEqual(self._value(name), wanted, name)

    def test_the_standby_shapes_agree(self):
        self.assertEqual(self._value("STANDBY_BREATH"), link.STANDBY_BREATH)
        self.assertEqual(self._value("STANDBY_DOT"), link.STANDBY_DOT)
        self.assertEqual(self._value("CAP_STANDBY_SHAPES"),
                         link.CAP_STANDBY_SHAPES)

    def test_the_breath_is_zero(self):
        """A board that ignores the shape byte breathes. The number of that
        shape must therefore be the number a host sends for it, or the two
        boards draw different things from the same message.
        """
        self.assertEqual(link.STANDBY_BREATH, 0)

    def test_the_firmware_says_what_it_can_do(self):
        """Without this the host cannot tell a board that draws the dot from
        one that breathes whatever it is asked for.
        """
        self.assertIn("sendCaps()", self.text)
        self.assertIn("CAPABILITIES", self.text)

    def test_it_refuses_a_shape_it_does_not_know(self):
        """A newer host is not a reason for a dark strip through a suspend."""
        self.assertIn("payload[5] <= STANDBY_LAST", self.text)


class SerialLoopbackTest(unittest.TestCase):
    def setUp(self):
        self.master, self.slave = pty.openpty()
        self.device = os.ttyname(self.slave)
        self.esp = None
        self.addCleanup(self._teardown)

    def start_esp(self, caps=None):
        """Attaches the fake ESP, for a test that does not read the master.

        The reader thread takes the bytes of a test that reads the master.
        """
        # The slave fd stays open on purpose: with no process on that end,
        # reads on the master fail with EIO.
        self.esp = FakeEsp(self.master, caps)
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

    def test_a_board_from_before_caps_reports_none(self):
        """Every board flashed before that message. Silence is the answer,
        and the host must read it as "none of them" and not as a failure.
        """
        self.start_esp()
        bridge = link.EspLink(port=self.device, baudrate=BAUD, led_count=17)
        bridge.BOOT_DELAY = 0.05
        self.addCleanup(bridge.disconnect)
        self.assertTrue(bridge.connect())
        self.assertEqual(bridge.caps, 0)
        self.assertFalse(bridge.standby_shapes)

    def test_a_board_that_says_so_is_read(self):
        self.start_esp(caps=link.CAP_STANDBY_SHAPES)
        bridge = link.EspLink(port=self.device, baudrate=BAUD, led_count=17)
        bridge.BOOT_DELAY = 0.05
        self.addCleanup(bridge.disconnect)
        self.assertTrue(bridge.connect())
        self.assertTrue(bridge.standby_shapes)

    def test_the_standby_message_carries_the_shape(self):
        """Six bytes, and the sixth is the shape. The firmware that came
        before it reads five and returns, so such a board breathes.
        """
        esp = self.start_esp(caps=link.CAP_STANDBY_SHAPES)
        bridge = link.EspLink(port=self.device, baudrate=BAUD, led_count=17)
        bridge.BOOT_DELAY = 0.05
        self.addCleanup(bridge.disconnect)
        self.assertTrue(bridge.connect())
        self.assertTrue(bridge.send_standby((9, 8, 7), 6000,
                                            link.STANDBY_DOT))
        payload = esp.wait_for(link.MSG_STANDBY)
        self.assertIsNotNone(payload, "the message never arrived")
        self.assertEqual(payload[:3], bytes((9, 8, 7)))
        self.assertEqual(int.from_bytes(payload[3:5], "little"), 6000)
        self.assertEqual(payload[5], link.STANDBY_DOT)

    def test_blank_on_shutdown(self):
        esp = self.start_esp()
        bridge = link.EspLink(port=self.device, baudrate=BAUD, led_count=17)
        bridge.BOOT_DELAY = 0.05
        self.assertTrue(bridge.connect())
        bridge.shutdown()
        self.assertIsNotNone(esp.wait_for(link.MSG_BLANK))
        self.assertFalse(bridge.connected)

    def test_baud_candidates_start_with_the_configured_rate(self):
        bridge = link.EspLink(port=self.device, baudrate=230400)
        candidates = bridge._baud_candidates(self.device)
        self.assertEqual(candidates[0], 230400)
        self.assertEqual(len(candidates), len(set(candidates)), "duplicate rates")
        for rate in link.FALLBACK_BAUD_RATES:
            self.assertIn(rate, candidates)

    def test_every_shipped_firmware_rate_is_settable_on_linux(self):
        # 250000 baud is a common rate for WS2812 work, and it has no termios
        # constant. No program can talk to a firmware that uses that rate.
        from steamos_utility_center.serialport import BAUD_CONSTANTS
        for rate in link.FALLBACK_BAUD_RATES:
            self.assertIn(rate, BAUD_CONSTANTS,
                          "firmware rate %d cannot be set on Linux" % rate)

    def test_unsettable_rates_are_filtered_out(self):
        bridge = link.EspLink(port=self.device, baudrate=250000)
        self.assertNotIn(250000, bridge._baud_candidates(self.device))

    def test_baud_autodetect_can_be_disabled(self):
        bridge = link.EspLink(port=self.device, baudrate=230400,
                              autodetect_baud=False)
        self.assertEqual(bridge._baud_candidates(self.device), [230400])

    def test_successful_rate_is_reused_on_reconnect(self):
        self.start_esp()
        # 460800 is not the configured rate, so it can only come from a scan.
        bridge = link.EspLink(port=self.device, baudrate=BAUD, led_count=17)
        bridge.BOOT_DELAY = 0.05
        self.addCleanup(bridge.disconnect)
        self.assertTrue(bridge.connect())
        bridge._known_good[os.path.realpath(self.device)] = 460800

        candidates = bridge._baud_candidates(os.path.realpath(self.device))
        self.assertEqual(candidates[0], 460800,
                         "a rate that worked before must be tried first")

    def test_quiet_device_falls_back_to_blind_streaming(self):
        # No fake ESP: nothing ever answers HELLO.
        bridge = link.EspLink(port=self.device, baudrate=BAUD, led_count=17)
        bridge.BOOT_DELAY = 0.0
        bridge.HELLO_ATTEMPTS = 1
        bridge.HELLO_TIMEOUT = 0.01
        self.addCleanup(bridge.disconnect)

        self.assertTrue(bridge.connect(), "should stream blind, not give up")
        self.assertIsNone(bridge.info)
        self.assertEqual(bridge.active_baudrate, BAUD,
                         "blind mode must use the configured rate")
        # The fruitless scan must not repeat on every reconnect.
        self.assertIn(os.path.realpath(self.device), bridge._scanned)
        self.assertEqual(bridge._baud_candidates(os.path.realpath(self.device)),
                         [BAUD])

    def test_a_later_rate_answering_does_not_leak_the_first_port(self):
        # The link keeps the first candidate open, in case no rate answers and
        # it must send with no answer. After a later rate answers, that port
        # has no use. One file descriptor for each reconnect is also many file
        # descriptors over a long session.
        self.start_esp()
        bridge = link.EspLink(port=self.device, baudrate=BAUD, led_count=17)
        bridge.BOOT_DELAY = 0.0
        self.addCleanup(bridge.disconnect)

        opened = []
        open_port, greet = bridge._open, bridge._greet
        bridge._open = lambda device, rate: opened.append(
            open_port(device, rate)) or opened[-1]
        # Silence only the first candidate, so the scan moves on to the next.
        # _greet answers (info, caps), and no answer is (None, 0).
        bridge._greet = lambda port: (None, 0) if len(opened) == 1 else greet(port)

        self.assertTrue(bridge.connect())
        self.assertGreater(len(opened), 1, "expected a second candidate")
        for port in opened:
            if port is not bridge.serial:
                self.assertEqual(port.fd, -1,
                                 "a rejected candidate was left open")

    def test_a_port_that_is_not_a_tty_is_refused_not_fatal(self):
        # SERIAL_PORT accepts each path that exists, but only a tty accepts the
        # serial settings. termios raises an error type of its own, and that
        # type is not an OSError. Such an error therefore passes each handler
        # and stops the service. Under Restart=always that repeats.
        with self.assertRaises(SerialError):
            SerialPort("/dev/null", BAUD)

        bridge = link.EspLink(port="/dev/null", baudrate=BAUD, led_count=17,
                              reconnect_delay=0.0)
        self.assertFalse(bridge.connect(), "must report failure, not raise")
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
