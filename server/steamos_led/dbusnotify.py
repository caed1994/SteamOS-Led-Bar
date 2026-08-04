"""Flash on desktop notifications, read off the session bus.

The other LED project drives its overlay from D-Bus, and this is the same
idea - with one correction. It subscribes with a *signal* match on
`org.freedesktop.Notifications` / `Notify`, but Notify is a method call, and
method calls are unicast: a signal match never sees one. Measured on a private
bus, that rule catches a hand-sent signal and zero method calls, while a plain
monitor catches the call. So this listens in monitor mode instead.

`dbus-monitor` is spawned as a subprocess rather than binding dbus-python and
PyGObject, which keeps the service dependency-free on a read-only rootfs.
"""

from __future__ import annotations

import errno
import logging
import os
import re
import shutil
import subprocess

LOG = logging.getLogger(__name__)

MATCH = "interface='org.freedesktop.Notifications'"

# "member=Notify" appears on the header line of every notification call.
_CALL_RE = re.compile(r"^method call .*member=Notify\s*$")
_STRING_RE = re.compile(r'^\s+string "(.*)"\s*$')
_HEADER_RE = re.compile(r"^(method call|method return|signal|error) ")

# Argument order of org.freedesktop.Notifications.Notify: app_name, replaces_id,
# app_icon, summary, body, ... - so among the plain strings, index 0 is the
# application and 2 is the summary.
ARG_APP_NAME = 0
ARG_SUMMARY = 2

# Which word in the summary means "achievement" rather than a message. Kept as
# substrings so localised summaries still match.
ACHIEVEMENT_WORDS = ("achievement", "errungenschaft", "conquista", "logro",
                     "succès", "trofeo", "trophy")


def monitor_command():
    """The command that prints notifications, or None if none is installed."""
    if shutil.which("dbus-monitor"):
        return ["dbus-monitor", "--session", MATCH]
    if shutil.which("gdbus"):
        return ["gdbus", "monitor", "--session",
                "--dest", "org.freedesktop.Notifications"]
    return None


def classify(app_name, summary):
    """Which flash a notification deserves, or None to ignore it."""
    text = (summary or "").lower()
    if any(word in text for word in ACHIEVEMENT_WORDS):
        return "achievement"
    del app_name
    return "message"


class NotificationParser:
    """Turns dbus-monitor output into (app_name, summary) pairs."""

    def __init__(self):
        self._in_call = False
        self._strings = []
        self._buffer = ""

    def feed(self, text):
        """Append output and return the notifications completed by it."""
        self._buffer += text
        found = []
        while "\n" in self._buffer:
            line, _, self._buffer = self._buffer.partition("\n")
            result = self._line(line)
            if result is not None:
                found.append(result)
        return found

    def _line(self, line):
        if _CALL_RE.match(line):
            # A new call starts; anything half-collected was not a Notify.
            self._in_call = True
            self._strings = []
            return None

        if not self._in_call:
            return None

        if _HEADER_RE.match(line):
            # Another message began, so the previous call is complete.
            return self._finish()

        match = _STRING_RE.match(line)
        if match:
            self._strings.append(match.group(1))
            if len(self._strings) > ARG_SUMMARY:
                # Everything needed is in hand; do not wait for the trailing
                # arrays, which may be followed by nothing for a while.
                return self._finish()
        return None

    def _finish(self):
        strings, self._strings, self._in_call = self._strings, [], False
        if len(strings) <= ARG_SUMMARY:
            return None
        return strings[ARG_APP_NAME], strings[ARG_SUMMARY]

    def flush(self):
        """Complete a call left open at the end of the stream."""
        if not self._in_call:
            return None
        return self._finish()


class DbusNotificationWatcher:
    """Runs dbus-monitor and reports the notifications worth flashing."""

    def __init__(self, app_filter="steam"):
        # Without a filter every desktop notification would light up the bar.
        self.app_filter = (app_filter or "").strip().lower()
        self.process = None
        self.parser = NotificationParser()

    def open(self):
        command = monitor_command()
        if command is None:
            raise RuntimeError(
                "neither dbus-monitor nor gdbus is installed, so desktop "
                "notifications cannot be read")
        if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
            LOG.debug("DBUS_SESSION_BUS_ADDRESS is unset; dbus-monitor will "
                      "try to find the bus itself")

        # Binary, not text: a non-blocking text stream returns None rather
        # than an empty string when nothing is buffered, and the decoder then
        # fails with "can't concat NoneType to bytes".
        self.process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        os.set_blocking(self.process.stdout.fileno(), False)
        LOG.info("watching desktop notifications via %s", command[0])

    @property
    def alive(self):
        return self.process is not None and self.process.poll() is None

    def poll(self):
        """Return a flash kind for each matching notification seen."""
        if self.process is None:
            return []
        if self.process.poll() is not None:
            LOG.warning("dbus-monitor exited with %s", self.process.returncode)
            self.process = None
            return []

        try:
            chunk = os.read(self.process.stdout.fileno(), 65536)
        except BlockingIOError:
            return []
        except OSError as exc:
            if exc.errno != errno.EINTR:
                LOG.warning("reading dbus-monitor failed: %s", exc)
            return []
        if not chunk:
            return []

        kinds = []
        for app_name, summary in self.parser.feed(chunk.decode("utf-8", "replace")):
            if self.app_filter and self.app_filter not in (app_name or "").lower():
                LOG.debug("ignoring notification from %r", app_name)
                continue
            kind = classify(app_name, summary)
            if kind:
                LOG.info("notification from %s: %r -> %s", app_name, summary, kind)
                kinds.append(kind)
        return kinds

    def close(self):
        if self.process is None:
            return
        try:
            self.process.terminate()
            self.process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            try:
                self.process.kill()
            except OSError:
                pass
        finally:
            self.process = None
