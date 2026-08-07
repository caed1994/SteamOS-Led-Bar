"""Tests for the control panel's logic, which is kept out of the widgets.

There is no display here and there will not be one on a build machine, so
everything worth testing - what counts as broken, and what fixes it - lives in
ledpanel.py with no tkinter in sight.
"""

import ast
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "server"))
sys.path.insert(0, os.path.join(HERE, "..", "gui"))

import ledpanel  # noqa: E402
from steamos_led import config as config_module  # noqa: E402


class FakeProbe:
    """Answers about a machine, without being one."""

    def __init__(self, present=(), fifos=(), active=(), user_active=(),
                 release="6.11.11-valve"):
        self.present = set(present)
        self.fifos = set(fifos)
        self.active = set(active)
        self.user_active = set(user_active)
        self.release = release

    def exists(self, path):
        return path in self.present

    def is_fifo(self, path):
        return path in self.fifos

    def unit_active(self, unit, user=False):
        return unit in (self.user_active if user else self.active)

    def kernel_release(self):
        return self.release


def healthy(release="6.11.11-valve"):
    return FakeProbe(
        present=(ledpanel.BINARY, ledpanel.UNIT_PATH, ledpanel.CONFIG_PATH,
                 ledpanel.UDEV_PATH, ledpanel.SHIM_DEVICE,
                 ledpanel.module_path(release)),
        fifos=("/run/steamos-led-serial/notify",),
        active=(ledpanel.SERVICE,),
        user_active=(ledpanel.WATCHER,),
        release=release)


class HealthyInstallationTest(unittest.TestCase):
    def test_nothing_is_reported_broken(self):
        checks = ledpanel.run_checks(probe=healthy())
        self.assertEqual(ledpanel.broken(checks), [])
        self.assertIn("in order", ledpanel.repair_summary(checks))

    def test_the_notification_check_is_skipped_when_switched_off(self):
        probe = healthy()
        probe.fifos = set()
        checks = ledpanel.run_checks(probe=probe, config={"NOTIFY": False})
        self.assertEqual(ledpanel.broken(checks), [])

    def test_a_custom_pipe_location_is_honoured(self):
        probe = healthy()
        probe.fifos = {"/run/elsewhere/notify"}
        checks = ledpanel.run_checks(
            probe=probe, config={"NOTIFY": True,
                                 "NOTIFY_FIFO": "/run/elsewhere/notify"})
        self.assertEqual(ledpanel.broken(checks), [])


class AfterASteamUpdateTest(unittest.TestCase):
    """The case this panel exists for.

    A SteamOS update brings a new kernel. The module was built for the old
    one, so it is simply not there any more and the LED device disappears with
    it - while everything else looks perfectly healthy, which is what makes it
    puzzling from the outside.
    """

    def setUp(self):
        self.probe = healthy(release="6.11.11-valve")
        self.probe.release = "6.14.2-valve"          # the update landed
        self.probe.present.discard(ledpanel.SHIM_DEVICE)
        self.checks = ledpanel.run_checks(probe=self.probe)

    def test_the_missing_module_is_named(self):
        names = [check.name for check in ledpanel.broken(self.checks)]
        self.assertTrue(any("Kernel module" in name for name in names), names)

    def test_the_new_kernel_version_is_in_the_message(self):
        module = next(check for check in self.checks
                      if check.name.startswith("Kernel module"))
        self.assertIn("6.14.2-valve", module.name)
        self.assertIn("6.14.2-valve", module.detail)

    def test_the_summary_explains_rather_than_counts(self):
        summary = ledpanel.repair_summary(self.checks)
        self.assertIn("SteamOS update", summary)
        self.assertIn("reinstall", summary.lower())

    def test_all_of_it_is_repairable(self):
        self.assertTrue(all(check.repairable
                            for check in ledpanel.broken(self.checks)))


class NotInstalledTest(unittest.TestCase):
    def test_everything_is_reported_missing(self):
        checks = ledpanel.run_checks(probe=FakeProbe())
        self.assertEqual(len(ledpanel.broken(checks)), len(checks))

    def test_the_summary_counts_them(self):
        checks = ledpanel.run_checks(probe=FakeProbe())
        self.assertIn("problem", ledpanel.repair_summary(checks))


class CommandTest(unittest.TestCase):
    """What the buttons actually run."""

    def test_repairing_never_touches_the_board(self):
        # Reinstalling after a system update must not reflash the ESP: the
        # firmware survives it, and a surprise flash is the last thing someone
        # fixing a dark bar needs.
        command = ledpanel.reinstall_command("/home/deck/SteamOS-Led-Bar")
        self.assertIn("--flash", command)
        self.assertEqual(command[command.index("--flash") + 1], "0")

    def test_repairing_rebuilds_the_module_unattended(self):
        command = ledpanel.reinstall_command("/home/deck/SteamOS-Led-Bar")
        self.assertIn("--rebuild-module", command)
        self.assertIn("--yes", command)

    def test_repairing_asks_for_rights(self):
        self.assertEqual(ledpanel.reinstall_command("/repo")[0], "pkexec")

    def test_flashing_the_bar_asks_for_nothing(self):
        # The trigger pipe is world-writable on purpose, so trying a colour
        # must not put a password prompt in the way.
        self.assertNotIn("pkexec", ledpanel.notify_command("achievement"))

    def test_steam_questions_run_as_the_user(self):
        # Steamworks talks to the logged-in user's Steam client; as root it
        # would find nothing at all.
        for command in (ledpanel.steam_check_command(),
                        ledpanel.probe_messages_command()):
            self.assertNotIn("pkexec", command)

    def test_the_self_test_needs_rights_to_free_the_port(self):
        command = ledpanel.self_test_command("/repo", seconds=5)
        self.assertEqual(command[0], "pkexec")
        self.assertIn("5", command)

    def test_applying_a_config_goes_through_the_helper(self):
        command = ledpanel.apply_config_command("/repo", "/tmp/staged.conf")
        self.assertEqual(command[0], "pkexec")
        self.assertIn("/repo/scripts/apply-config.sh", command)
        self.assertIn("/tmp/staged.conf", command)


class PanelSettingsTest(unittest.TestCase):
    """The settings list has to agree with the configuration it edits."""

    def _settings(self):
        # Read the table out of the panel rather than importing it: importing
        # pulls in tkinter, which a build machine has no reason to have.
        path = os.path.join(HERE, "..", "gui", "steamos-led-panel")
        with open(path) as handle:
            tree = ast.parse(handle.read())
        for node in tree.body:
            if (isinstance(node, ast.Assign)
                    and getattr(node.targets[0], "id", "") == "SETTINGS"):
                return [entry.elts[0].value for entry in node.value.elts]
        self.fail("SETTINGS not found in the panel")

    def test_every_setting_shown_is_a_real_option(self):
        for key in self._settings():
            self.assertIn(key, config_module.DEFAULTS, key)

    def test_the_install_time_ones_are_left_out(self):
        # Serial port, baud rate, device path and library paths are decisions
        # made once, at install time. A slider is the wrong shape for them,
        # and getting one wrong takes the bar down rather than changing how it
        # looks - which is what everything in this panel should do.
        shown = self._settings()
        for key in ("SERIAL_PORT", "BAUD", "DEVICE", "STEAM_LIBRARY",
                    "STEAM_ROUTE"):
            self.assertNotIn(key, shown, key)

    def test_the_panel_never_writes_a_value_the_service_would_reject(self):
        # The sliders repeat the bounds validate() enforces. If the two ever
        # disagree, the panel offers a value that kills the service on
        # restart - so check the ends of every numeric range against it.
        path = os.path.join(HERE, "..", "gui", "steamos-led-panel")
        with open(path) as handle:
            tree = ast.parse(handle.read())
        table = next(node.value for node in tree.body
                     if isinstance(node, ast.Assign)
                     and getattr(node.targets[0], "id", "") == "SETTINGS")

        for entry in table.elts:
            key, _label, kind = (entry.elts[0].value, entry.elts[1].value,
                                 entry.elts[2].value)
            if kind not in ("int", "float"):
                continue
            low, high = entry.elts[3].value, entry.elts[4].value
            for edge in (low, high):
                candidate = dict(config_module.DEFAULTS)
                candidate[key] = int(edge) if kind == "int" else float(edge)
                # The two frame rates are coupled, and the panel resolves
                # that itself by moving the idle rate - so mirror that rule
                # here instead of pretending the ends are independent.
                if key == "IDLE_FPS":
                    candidate["FPS"] = max(candidate["FPS"], candidate[key])
                if key == "FPS":
                    candidate["IDLE_FPS"] = min(candidate["IDLE_FPS"],
                                                candidate[key])
                try:
                    config_module.validate(candidate)
                except config_module.ConfigError as exc:
                    self.fail("the panel offers %s=%s, which the service "
                              "rejects: %s" % (key, edge, exc))


if __name__ == "__main__":
    unittest.main()
