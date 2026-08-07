"""Framed binary protocol between the host service and the ESP.

    +------+------+---------+------+--------+---------+-------+
    | 0xA5 | 0x5A | version | type | length | payload | crc16 |
    +------+------+---------+------+--------+---------+-------+
       1      1        1       1      2 LE      n       2 LE

The CRC covers version, type, length and payload, so a receiver resynchronising
mid-stream cannot mistake noise for a frame.
"""

from __future__ import annotations

import logging
import struct
import time

from .serialport import (BAUD_CONSTANTS, SerialError, SerialPort, describe,
                         find_port)

LOG = logging.getLogger(__name__)

SOF = b"\xA5\x5A"
PROTOCOL_VERSION = 1
HEADER = struct.Struct("<BBH")  # version, type, length
MAX_PAYLOAD = 4096

# host -> esp
MSG_HELLO = 0x01
MSG_FRAME = 0x10
MSG_FILL = 0x11
MSG_BLANK = 0x20
MSG_PING = 0x40
# esp -> host
MSG_INFO = 0x02
MSG_STATS = 0x30
MSG_LOG = 0x31
MSG_PONG = 0x41

# Tried in order when the configured rate gets no answer: the shipped firmware's
# rate first, then what earlier and hand-rolled builds are likely to use.
FALLBACK_BAUD_RATES = (230400, 460800, 921600, 115200)

MSG_NAMES = {
    MSG_HELLO: "HELLO", MSG_FRAME: "FRAME", MSG_FILL: "FILL",
    MSG_BLANK: "BLANK", MSG_PING: "PING", MSG_INFO: "INFO",
    MSG_STATS: "STATS", MSG_LOG: "LOG", MSG_PONG: "PONG",
}


def _crc16_table():
    table = []
    for value in range(256):
        crc = value << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
        table.append(crc)
    return tuple(table)


# A frame is checksummed per LED update, so this runs FPS times a second over
# the whole payload - byte at a time rather than bit at a time.
_CRC16_TABLE = _crc16_table()


def crc16(data):
    """CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF)."""
    crc = 0xFFFF
    for byte in data:
        crc = ((crc << 8) & 0xFFFF) ^ _CRC16_TABLE[(crc >> 8) ^ byte]
    return crc


def build(msg_type, payload=b""):
    if len(payload) > MAX_PAYLOAD:
        raise ValueError("payload too large: %d bytes" % len(payload))
    body = HEADER.pack(PROTOCOL_VERSION, msg_type, len(payload)) + payload
    return SOF + body + struct.pack("<H", crc16(body))


class FrameParser:
    """Incremental parser for the esp -> host direction."""

    def __init__(self, max_payload=MAX_PAYLOAD):
        self.max_payload = max_payload
        self._buffer = bytearray()

    def feed(self, data):
        """Append bytes and yield every complete frame as (type, payload)."""
        self._buffer.extend(data)
        frames = []
        while True:
            start = self._buffer.find(SOF)
            if start < 0:
                # Keep a single byte in case the SOF straddles two reads.
                del self._buffer[:max(0, len(self._buffer) - 1)]
                break
            if start:
                del self._buffer[:start]
            if len(self._buffer) < 2 + HEADER.size + 2:
                break
            version, msg_type, length = HEADER.unpack_from(self._buffer, 2)
            if version != PROTOCOL_VERSION or length > self.max_payload:
                del self._buffer[:2]
                continue
            total = 2 + HEADER.size + length + 2
            if len(self._buffer) < total:
                break
            body = bytes(self._buffer[2:2 + HEADER.size + length])
            (checksum,) = struct.unpack_from("<H", self._buffer, 2 + HEADER.size + length)
            if checksum == crc16(body):
                frames.append((msg_type, body[HEADER.size:]))
                del self._buffer[:total]
            else:
                LOG.debug("crc mismatch, resynchronising")
                del self._buffer[:2]
        return frames


class DeviceInfo:
    def __init__(self, protocol, max_leds, pin, name):
        self.protocol = protocol
        self.max_leds = max_leds
        self.pin = pin
        self.name = name

    def __str__(self):
        return "%s (protocol %d, max %d LEDs, data pin %d)" % (
            self.name or "esp-led-client", self.protocol, self.max_leds, self.pin)

    @classmethod
    def parse(cls, payload):
        if len(payload) < 4:
            raise ValueError("short INFO payload")
        protocol = payload[0]
        max_leds = int.from_bytes(payload[1:3], "little")
        pin = payload[3]
        name = payload[4:].decode("ascii", "replace").strip("\x00")
        return cls(protocol, max_leds, pin, name)


class EspLink:
    """Owns the serial port, the handshake and reconnect behaviour."""

    BOOT_DELAY = 1.8       # ESP boot time after the port asserts DTR
    HELLO_ATTEMPTS = 5
    HELLO_TIMEOUT = 0.4

    def __init__(self, port="auto", baudrate=230400, led_count=17,
                 reconnect_delay=2.0, autodetect_baud=True):
        self.configured_port = port
        self.baudrate = baudrate
        self.led_count = led_count
        self.reconnect_delay = reconnect_delay
        self.autodetect_baud = autodetect_baud
        self.serial = None
        self.info = None
        self.device = None
        self.active_baudrate = None
        self._parser = FrameParser()
        self._next_attempt = 0.0
        self._warned_missing = False
        self._known_good = {}   # device -> baud rate that answered before
        self._scanned = set()   # devices where a full scan already came up empty

    @property
    def connected(self):
        return self.serial is not None

    def connect(self):
        """Try to open and greet the ESP. Returns True once connected."""
        if self.connected:
            return True
        now = time.monotonic()
        if now < self._next_attempt:
            return False
        self._next_attempt = now + self.reconnect_delay

        device = find_port(self.configured_port)
        if device is None:
            if not self._warned_missing:
                LOG.warning("no ESP serial device found (configured: %s)",
                            self.configured_port)
                self._warned_missing = True
            return False
        self._warned_missing = False

        candidates = self._baud_candidates(device)
        if not candidates:
            LOG.error("no usable baud rate for %s (configured: %s)",
                      device, self.baudrate)
            return False
        preferred = None

        for index, rate in enumerate(candidates):
            port = self._open(device, rate)
            if port is None:
                if index == 0:
                    return False
                continue

            info = self._greet(port)
            if info is not None:
                if index > 0:
                    self._scanned.discard(device)
                    if preferred is not None:
                        # Held open for the blind-mode fallback below, which
                        # this answer makes unnecessary.
                        preferred.close()
                self._adopt(port, device, rate, info)
                return True

            if index == 0:
                preferred = port    # keep it open for blind mode below
            else:
                port.close()

        if len(candidates) > 1:
            # Remember the miss so later reconnects do not repeat the scan.
            self._scanned.add(device)

        # Nothing answered - the firmware may predate the handshake, so stream
        # at the configured rate rather than give up.
        if preferred is not None:
            self._adopt(preferred, device, candidates[0], None)
            LOG.warning("no HELLO reply from %s; streaming at %d baud anyway",
                        describe(device), candidates[0])
            return True
        return False

    def _baud_candidates(self, device):
        """Rates to try, most likely first.

        A baud mismatch looks exactly like a dead strip, so rather than trust
        the config file alone, fall back to the rates the firmware ships with.
        """
        first = self._known_good.get(device, self.baudrate)
        candidates = [first]
        if self.autodetect_baud and device not in self._scanned:
            for rate in (self.baudrate,) + FALLBACK_BAUD_RATES:
                if rate not in candidates:
                    candidates.append(rate)
        # Only rates termios has a constant for can actually be set.
        return [rate for rate in candidates if rate in BAUD_CONSTANTS]

    def _open(self, device, baudrate):
        try:
            port = SerialPort(device, baudrate)
        except (OSError, SerialError) as exc:
            LOG.warning("cannot open %s at %d baud: %s", device, baudrate, exc)
            return None
        # Opening the tty pulses DTR/RTS and resets most dev boards, so drop
        # both lines and let the firmware come up before greeting it.
        port.set_dtr_rts(False)
        time.sleep(self.BOOT_DELAY)
        port.flush_input()
        return port

    def _greet(self, port):
        """Send HELLO and wait for INFO. Returns None if the ESP stays quiet."""
        parser = FrameParser()
        for _ in range(self.HELLO_ATTEMPTS):
            try:
                port.write(build(MSG_HELLO))
            except (OSError, SerialError) as exc:
                LOG.warning("handshake write failed: %s", exc)
                return None
            deadline = time.monotonic() + self.HELLO_TIMEOUT
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                if not port.wait_readable(remaining):
                    continue
                try:
                    data = port.read()
                except OSError:
                    return None
                for msg_type, payload in parser.feed(data):
                    if msg_type == MSG_INFO:
                        try:
                            return DeviceInfo.parse(payload)
                        except ValueError as exc:
                            LOG.warning("malformed INFO: %s", exc)
                    elif msg_type == MSG_LOG:
                        LOG.info("esp: %s", payload.decode("ascii", "replace").strip())
        return None

    def _adopt(self, port, device, baudrate, info):
        self.serial = port
        self.device = device
        self.active_baudrate = baudrate
        self.info = info
        self._parser = FrameParser()
        if info is None:
            return

        self._known_good[device] = baudrate
        LOG.info("connected to %s at %d baud: %s", describe(device), baudrate, info)
        if baudrate != self.baudrate:
            LOG.warning(
                "firmware talks %d baud but BAUD=%d is configured; using %d. "
                "Set BAUD=%d in the config file to skip this search.",
                baudrate, self.baudrate, baudrate, baudrate)
        if self.led_count > info.max_leds:
            LOG.warning(
                "configured LED_COUNT=%d exceeds firmware maximum %d; "
                "extra LEDs will stay dark", self.led_count, info.max_leds)

    def poll(self):
        """Drain the incoming direction; keeps STATS/LOG visible in the journal."""
        if not self.connected:
            return
        try:
            data = self.serial.read()
        except OSError as exc:
            LOG.warning("read failed: %s", exc)
            self.disconnect()
            return
        if not data:
            return
        for msg_type, payload in self._parser.feed(data):
            if msg_type == MSG_LOG:
                LOG.info("esp: %s", payload.decode("ascii", "replace").strip())
            elif msg_type == MSG_STATS and len(payload) >= 8:
                frames = int.from_bytes(payload[0:4], "little")
                errors = int.from_bytes(payload[4:6], "little")
                resyncs = int.from_bytes(payload[6:8], "little")
                LOG.debug("esp stats: frames=%d crc_errors=%d resyncs=%d",
                          frames, errors, resyncs)
            elif msg_type == MSG_INFO:
                try:
                    self.info = DeviceInfo.parse(payload)
                except ValueError:
                    pass

    def _send(self, frame):
        if not self.connected:
            return False
        try:
            self.serial.write(frame)
            return True
        except (OSError, SerialError) as exc:
            LOG.warning("write to %s failed: %s", self.device, exc)
            self.disconnect()
            return False

    def send_frame(self, pixels, led_count):
        """pixels is led_count * 3 bytes of RGB."""
        payload = led_count.to_bytes(2, "little") + pixels
        return self._send(build(MSG_FRAME, payload))

    def send_fill(self, colour, led_count):
        payload = led_count.to_bytes(2, "little") + bytes(colour)
        return self._send(build(MSG_FILL, payload))

    def send_blank(self):
        return self._send(build(MSG_BLANK))

    def disconnect(self):
        if self.serial is not None:
            self.serial.close()
            self.serial = None
        self.info = None
        self.active_baudrate = None
        self._next_attempt = time.monotonic() + self.reconnect_delay

    def shutdown(self):
        """Blank the strip before we go away so it does not stay lit."""
        if self.connected:
            self.send_blank()
            time.sleep(0.05)
        self.disconnect()
