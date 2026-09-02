# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""Serial port support, from the termios module in the standard library.

SteamOS has a read-only root filesystem. To use pyserial thus needs either
`steamos-readonly disable` or a virtualenv, and each OS update breaks the
virtualenv.

A USB CDC or UART device needs only some termios and TIOCM ioctls. This module
calls them directly, and the service thus needs no other package.
"""

from __future__ import annotations

import errno
import glob
import os
import select
import struct
import termios
from fcntl import ioctl

TIOCMBIS = 0x5416
TIOCMBIC = 0x5417
TIOCM_DTR = 0x002
TIOCM_RTS = 0x004

_BAUD_RATES = (
    9600, 19200, 38400, 57600, 115200, 230400, 460800,
    500000, 576000, 921600, 1000000, 1152000, 1500000, 2000000,
)
BAUD_CONSTANTS = {
    rate: getattr(termios, "B%d" % rate)
    for rate in _BAUD_RATES
    if hasattr(termios, "B%d" % rate)
}

# The USB-serial bridges on most ESP8266 and ESP32 development boards. The
# list also has the native USB CDC of the ESP32-S2, S3 and C3, whose Espressif
# vendor id is 0x303a.
KNOWN_ADAPTERS = {
    (0x10C4, 0xEA60): "Silicon Labs CP210x",
    (0x10C4, 0xEA70): "Silicon Labs CP2105",
    (0x1A86, 0x7523): "QinHeng CH340",
    (0x1A86, 0x55D4): "QinHeng CH9102",
    (0x1A86, 0x7522): "QinHeng CH340",
    (0x0403, 0x6001): "FTDI FT232R",
    (0x0403, 0x6015): "FTDI FT231X",
    (0x303A, 0x1001): "Espressif USB CDC",
    (0x303A, 0x0002): "Espressif USB CDC",
    (0x303A, 0x4001): "Espressif USB CDC",
}


class SerialError(OSError):
    """A port search or a port configuration failed."""


def _read_sysfs(path):
    try:
        with open(path, "r") as handle:
            return handle.read().strip()
    except OSError:
        return None


def _usb_attributes(tty_name):
    """Goes up the sysfs device chain of a tty to the USB device node."""
    node = "/sys/class/tty/%s/device" % tty_name
    if not os.path.exists(node):
        return {}
    node = os.path.realpath(node)
    for _ in range(8):
        vendor = _read_sysfs(os.path.join(node, "idVendor"))
        product = _read_sysfs(os.path.join(node, "idProduct"))
        if vendor and product:
            try:
                vid, pid = int(vendor, 16), int(product, 16)
            except ValueError:
                return {}
            return {
                "vid": vid,
                "pid": pid,
                "manufacturer": _read_sysfs(os.path.join(node, "manufacturer")),
                "product": _read_sysfs(os.path.join(node, "product")),
                "serial": _read_sysfs(os.path.join(node, "serial")),
            }
        parent = os.path.dirname(node)
        if parent == node or parent == "/sys":
            break
        node = parent
    return {}


def _by_id_link(device):
    for link in glob.glob("/dev/serial/by-id/*"):
        if os.path.realpath(link) == os.path.realpath(device):
            return link
    return None


def list_ports():
    """Returns the possible USB serial devices, the most probable first."""
    ports = []
    for device in sorted(glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*")):
        info = _usb_attributes(os.path.basename(device))
        adapter = KNOWN_ADAPTERS.get((info.get("vid"), info.get("pid")))
        description = " ".join(
            part for part in (info.get("manufacturer"), info.get("product")) if part
        )
        ports.append({
            "device": device,
            "by_id": _by_id_link(device),
            "vid": info.get("vid"),
            "pid": info.get("pid"),
            "serial": info.get("serial"),
            "description": description or adapter or "unknown",
            "adapter": adapter,
        })
    # A known ESP adapter has priority over a modem or a printer on ttyACM*.
    ports.sort(key=lambda port: (port["adapter"] is None, port["device"]))
    return ports


def find_port(preferred=None):
    """Returns a port from a device path, a by-id link, ``auto`` or None."""
    if preferred and preferred != "auto":
        if os.path.exists(preferred):
            return os.path.realpath(preferred)
        return None
    ports = list_ports()
    if not ports:
        return None
    return ports[0]["device"]


def describe(device):
    for port in list_ports():
        if os.path.realpath(port["device"]) == os.path.realpath(device):
            extra = ""
            if port["vid"] is not None:
                extra = " [%04x:%04x]" % (port["vid"], port["pid"])
            return "%s (%s%s)" % (port["device"], port["description"], extra)
    return device


class SerialPort:
    """A raw 8N1 serial port. Reads do not block. Writes block."""

    def __init__(self, device, baudrate):
        if baudrate not in BAUD_CONSTANTS:
            raise SerialError(
                "unsupported baud rate %s (supported: %s)"
                % (baudrate, ", ".join(str(rate) for rate in sorted(BAUD_CONSTANTS)))
            )
        self.device = device
        self.baudrate = baudrate
        self.fd = -1
        self._open()

    def _open(self):
        # O_NONBLOCK at the open prevents a stop on a port with no carrier.
        self.fd = os.open(self.device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            self._configure()
        except termios.error as exc:
            # The open succeeds for each file, but only a tty accepts this
            # configuration. termios.error is not an OSError, so a caller that
            # handles a failed open does not catch it. The service would then
            # stop on a SERIAL_PORT that names another file.
            self.close()
            raise SerialError("%s is not a serial port: %s"
                              % (self.device, exc))
        except Exception:
            self.close()
            raise

    def _configure(self):
        speed = BAUD_CONSTANTS[self.baudrate]
        iflag, oflag, cflag, lflag, _ispeed, _ospeed, cc = termios.tcgetattr(self.fd)

        # Raw mode: no translation, no echo, no flow control, no signals.
        # Each of the four would change the bytes on the wire.
        iflag &= ~(
            termios.IGNBRK | termios.BRKINT | termios.PARMRK | termios.ISTRIP
            | termios.INLCR | termios.IGNCR | termios.ICRNL | termios.IXON
            | termios.IXOFF | termios.IXANY
        )
        oflag &= ~termios.OPOST
        lflag &= ~(
            termios.ECHO | termios.ECHONL | termios.ICANON
            | termios.ISIG | termios.IEXTEN
        )
        cflag &= ~(termios.CSIZE | termios.PARENB | termios.CSTOPB | termios.CRTSCTS)
        # HUPCL sets DTR low at the close and thus resets the ESP at each
        # restart of the service.
        cflag &= ~termios.HUPCL
        cflag |= termios.CS8 | termios.CREAD | termios.CLOCAL

        cc = list(cc)
        cc[termios.VMIN] = 0
        cc[termios.VTIME] = 0

        termios.tcsetattr(
            self.fd, termios.TCSANOW,
            [iflag, oflag, cflag, lflag, speed, speed, cc],
        )
        self.flush_input()

    def set_dtr_rts(self, state):
        """Sets DTR and RTS. Both low keeps an ESP board out of a reset."""
        request = TIOCMBIS if state else TIOCMBIC
        try:
            ioctl(self.fd, request, struct.pack("I", TIOCM_DTR | TIOCM_RTS))
        except OSError:
            # A native USB CDC device has no modem control lines.
            pass

    def flush_input(self):
        try:
            termios.tcflush(self.fd, termios.TCIFLUSH)
        except termios.error:
            pass

    def write(self, data, timeout=2.0):
        """Writes each byte, or raises. USB CDC back-pressure is the control."""
        view = memoryview(data)
        while view:
            try:
                written = os.write(self.fd, view)
                view = view[written:]
            except BlockingIOError:
                _, writable, _ = select.select([], [self.fd], [], timeout)
                if not writable:
                    raise SerialError("write timeout on %s" % self.device)
            except OSError as exc:
                if exc.errno == errno.EINTR:
                    continue
                raise

    def read(self, size=4096):
        """Returns the buffered bytes, or b'' when there are none."""
        try:
            return os.read(self.fd, size)
        except BlockingIOError:
            return b""
        except OSError as exc:
            if exc.errno == errno.EINTR:
                return b""
            raise

    def wait_readable(self, timeout):
        readable, _, _ = select.select([self.fd], [], [], timeout)
        return bool(readable)

    def close(self):
        if self.fd >= 0:
            try:
                os.close(self.fd)
            finally:
                self.fd = -1

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()
