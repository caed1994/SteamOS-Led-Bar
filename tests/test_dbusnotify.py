"""Tests for reading desktop notifications off the session bus.

The fixture below is real dbus-monitor output, captured from a private bus
rather than written from memory, so the parser is checked against the format
it will actually meet.
"""

import os
import shutil
import subprocess
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "server"))

from steamos_led import dbusnotify  # noqa: E402

# Captured with:
#   dbus-monitor --session "interface='org.freedesktop.Notifications'"
REAL_OUTPUT = '''signal time=1785842096.474245 sender=org.freedesktop.DBus -> destination=:1.0 serial=2 path=/org/freedesktop/DBus; interface=org.freedesktop.DBus; member=NameAcquired
   string ":1.0"
method call time=1785842097.476902 sender=:1.1 -> destination=org.freedesktop.DBus serial=2 path=/org/freedesktop/Notifications; interface=org.freedesktop.Notifications; member=Notify
   string "Steam"
   uint32 0
   string "steam"
   string "Achievement Unlocked"
   string "Brotato - First Blood"
   array [
   ]
   array [
   ]
   int32 5000
'''


class ParserTest(unittest.TestCase):
    def setUp(self):
        self.parser = dbusnotify.NotificationParser()

    def test_reads_a_real_capture(self):
        found = self.parser.feed(REAL_OUTPUT)
        self.assertEqual(found, [("Steam", "Achievement Unlocked")])

    def test_ignores_unrelated_signals(self):
        self.assertEqual(self.parser.feed(REAL_OUTPUT.split("method call")[0]), [])

    def test_survives_being_split_mid_line(self):
        # A pipe hands over arbitrary chunks, not whole lines.
        half = len(REAL_OUTPUT) // 2
        first = self.parser.feed(REAL_OUTPUT[:half])
        second = self.parser.feed(REAL_OUTPUT[half:])
        self.assertEqual(first + second, [("Steam", "Achievement Unlocked")])

    def test_reads_two_notifications_in_a_row(self):
        found = self.parser.feed(REAL_OUTPUT + REAL_OUTPUT)
        self.assertEqual(len(found), 2)

    def test_summary_is_the_third_string(self):
        # app_name, app_icon, summary - picking the wrong index would flash on
        # the icon name and never on the text.
        found = self.parser.feed(REAL_OUTPUT)
        self.assertEqual(found[0][1], "Achievement Unlocked")


class ClassifyTest(unittest.TestCase):
    def test_achievement_wording(self):
        self.assertEqual(dbusnotify.classify("Steam", "Achievement Unlocked"),
                         "achievement")

    def test_achievement_wording_in_other_languages(self):
        for summary in ("Errungenschaft freigeschaltet", "Conquista desbloqueada",
                        "Logro desbloqueado"):
            self.assertEqual(dbusnotify.classify("Steam", summary), "achievement",
                             summary)

    def test_anything_else_is_a_message(self):
        self.assertEqual(dbusnotify.classify("Steam", "Nachricht von Kumpel"),
                         "message")

    def test_missing_summary_does_not_crash(self):
        self.assertEqual(dbusnotify.classify("Steam", None), "message")


class FilterTest(unittest.TestCase):
    def test_only_steam_by_default(self):
        watcher = dbusnotify.DbusNotificationWatcher()
        self.assertEqual(watcher.app_filter, "steam")

    def test_empty_filter_accepts_everything(self):
        watcher = dbusnotify.DbusNotificationWatcher("")
        self.assertEqual(watcher.app_filter, "")

    def test_poll_before_open_is_quiet(self):
        self.assertEqual(dbusnotify.DbusNotificationWatcher().poll(), [])

    def test_close_before_open_is_safe(self):
        dbusnotify.DbusNotificationWatcher().close()


@unittest.skipUnless(shutil.which("dbus-daemon") and shutil.which("dbus-send")
                     and dbusnotify.monitor_command(),
                     "needs a D-Bus session to talk to")
class LiveBusTest(unittest.TestCase):
    """Drives the watcher against a real bus, end to end."""

    def setUp(self):
        address = subprocess.run(
            ["dbus-daemon", "--session", "--fork", "--print-address"],
            capture_output=True, text=True, check=True).stdout.strip()
        self.previous = os.environ.get("DBUS_SESSION_BUS_ADDRESS")
        os.environ["DBUS_SESSION_BUS_ADDRESS"] = address
        self.addCleanup(self._restore)

    def _restore(self):
        if self.previous is None:
            os.environ.pop("DBUS_SESSION_BUS_ADDRESS", None)
        else:
            os.environ["DBUS_SESSION_BUS_ADDRESS"] = self.previous

    def _notify(self, app, summary):
        subprocess.run(
            ["dbus-send", "--session", "--type=method_call",
             "--dest=org.freedesktop.DBus", "/org/freedesktop/Notifications",
             "org.freedesktop.Notifications.Notify",
             "string:%s" % app, "uint32:0", "string:icon",
             "string:%s" % summary, "string:body",
             "array:string:", "dict:string:string:,", "int32:5000"],
            capture_output=True)

    def _drain(self, watcher, seconds=3.0):
        kinds = []
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            kinds.extend(watcher.poll())
            if kinds:
                break
            time.sleep(0.05)
        return kinds

    def test_sees_a_real_notification(self):
        watcher = dbusnotify.DbusNotificationWatcher("steam")
        watcher.open()
        self.addCleanup(watcher.close)
        time.sleep(0.6)                       # let dbus-monitor attach
        self._notify("Steam", "Achievement Unlocked")
        self.assertEqual(self._drain(watcher), ["achievement"])

    def test_message_flashes_blue(self):
        watcher = dbusnotify.DbusNotificationWatcher("steam")
        watcher.open()
        self.addCleanup(watcher.close)
        time.sleep(0.6)
        self._notify("Steam", "Nachricht von einem Freund")
        self.assertEqual(self._drain(watcher), ["message"])

    def test_other_applications_are_ignored(self):
        watcher = dbusnotify.DbusNotificationWatcher("steam")
        watcher.open()
        self.addCleanup(watcher.close)
        time.sleep(0.6)
        self._notify("Firefox", "Download finished")
        self.assertEqual(self._drain(watcher, seconds=1.5), [])


if __name__ == "__main__":
    unittest.main()
