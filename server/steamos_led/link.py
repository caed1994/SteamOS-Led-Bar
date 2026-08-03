"""Framed binary protocol between the host service and the ESP.

    +------+------+---------+------+--------+---------+-------+
    | 0xA5 | 0x5A | version | type | length | payload | crc16 |
    +------+------+---------+------+--------+---------+-------+
       1      1        1       1      2 LE      n       2 LE

The CRC (CRC-16/CCITT-FALSE) covers version, type, length and payload, so a
receiver that resynchronises mid-stream cannot mistake noise for a frame.
"""

from __future__ import annotations

import logging
import struct
import time

from .serialport import SerialError, SerialPort, describe, find_port

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

MSG_NAMES = {
    MSG_HELLO: "HELLO", MSG_FRAME: "FRAME", MSG_FILL: "FILL",
    MSG_BLANK: "BLANK", MSG_PING: "PING", MSG_INFO: "INFO",
    MSG_STATS: "STATS", MSG_LOG: "LOG", MSG_PONG: "PONG",
}


def crc16(data):
    """CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF)."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
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

    def __init__(self, port="auto", baudrate=460800, led_count=17,
                 reconnect_delay=2.0):
        self.configured_port = port
        self.baudrate = baudrate
        self.led_count = led_count
        self.reconnect_delay = reconnect_delay
        self.serial = None
        self.info = None
        self.device = None
        self._parser = FrameParser()
        self._next_attempt = 0.0
        self._warned_missing = False

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

        try:
            port = SerialPort(device, self.baudrate)
        except (OSError, SerialError) as exc:
            LOG.warning("cannot open %s: %s", device, exc)
            return False

        # Opening the tty pulses DTR/RTS and resets most dev boards; drop both
        # lines and give the firmware time to come up before we greet it.
        port.set_dtr_rts(False)
        time.sleep(self.BOOT_DELAY)
        port.flush_input()

        self.serial = port
        self.device = device
        self._parser = FrameParser()
        self._warned_missing = False

        self.info = self._handshake()
        if not self.connected:
            # The handshake write failed and tore the port down again.
            return False
        if self.info is not None:
            LOG.info("connected to %s: %s", describe(device), self.info)
            if self.led_count > self.info.max_leds:
                LOG.warning(
                    "configured LED_COUNT=%d exceeds firmware maximum %d; "
                    "extra LEDs will stay dark",
                    self.led_count, self.info.max_leds)
        else:
            LOG.warning("no HELLO reply from %s; sending frames anyway",
                        describe(device))
        return True

    def _handshake(self):
        for _ in range(self.HELLO_ATTEMPTS):
            try:
                self.serial.write(build(MSG_HELLO))
            except (OSError, SerialError) as exc:
                LOG.warning("handshake write failed: %s", exc)
                self.disconnect()
                return None
            deadline = time.monotonic() + self.HELLO_TIMEOUT
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                if not self.serial.wait_readable(remaining):
                    continue
                for msg_type, payload in self._parser.feed(self.serial.read()):
                    if msg_type == MSG_INFO:
                        try:
                            return DeviceInfo.parse(payload)
                        except ValueError as exc:
                            LOG.warning("malformed INFO: %s", exc)
                    elif msg_type == MSG_LOG:
                        LOG.info("esp: %s", payload.decode("ascii", "replace").strip())
        return None

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
        self._next_attempt = time.monotonic() + self.reconnect_delay

    def shutdown(self):
        """Blank the strip before we go away so it does not stay lit."""
        if self.connected:
            self.send_blank()
            time.sleep(0.05)
        self.disconnect()
