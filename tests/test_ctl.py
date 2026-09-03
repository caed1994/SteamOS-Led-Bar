# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""The control surface that speaks JSON, and the promises it makes.

Nothing here writes to /etc and nothing here runs a program with root. The
appliers are recorded rather than run: a test that ran pkexec would ask
whoever runs the suite for a password.

Three of these tests are the reason this module exists at all.

`status` must start no process. A front end asks for it again and again while
a person watches a page, and a fork for each answer is a cost that a game
pays.

Nothing must print on stdout but the answer. A caller parses that text, and a
warning in the middle of it is an answer that no caller can read.

Nothing must ask for a password unless the caller permits it. Game Mode has no
polkit agent and no terminal, so a question there has nobody to answer it.
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "server"))

from steamos_utility_center import ctl               # noqa: E402
from steamos_utility_center import mounts            # noqa: E402
from steamos_utility_center import syssettings       # noqa: E402


class Recorder:
    """Stands in for the program runner and records what it was given."""

    def __init__(self, code=0, said="done"):
        self.code = code
        self.said = said
        self.commands = []

    def __call__(self, command, timeout=120):
        self.commands.append(list(command))
        return self.code, self.said


class AnswerTest(unittest.TestCase):
    """One JSON object on stdout, and an exit status that agrees with it."""

    def _said(self, argv):
        held = io.StringIO()
        with redirect_stdout(held):
            code = ctl.main(argv)
        return code, held.getvalue()

    def test_an_answer_is_one_line_of_json(self):
        code, said = self._said(["areas"])
        self.assertEqual(code, 0)
        self.assertEqual(len(said.strip().splitlines()), 1)
        self.assertTrue(json.loads(said)["ok"])

    def test_a_refusal_is_json_too_and_not_a_stack_trace(self):
        """A front end parses stdout. A traceback there is an answer it loses."""
        code, said = self._said(["set", "keyboard", "not json at all"])
        self.assertEqual(code, 1)
        answer = json.loads(said)
        self.assertFalse(answer["ok"])
        self.assertIn("JSON", answer["error"])

    def test_every_command_says_whether_it_worked(self):
        for argv in (["areas"], ["status"], ["get", "strip"]):
            _code, said = self._said(argv)
            self.assertIn("ok", json.loads(said), argv)

    def test_an_area_that_does_not_exist_names_the_ones_that_do(self):
        with self.assertRaises(ctl.CtlError) as caught:
            ctl.get("bar")
        for name in ctl.AREAS:
            self.assertIn(name, str(caught.exception))


class CheapStatusTest(unittest.TestCase):
    """The status a front end can ask for again and again."""

    def test_the_plain_status_starts_no_process(self):
        """The whole reason for the two halves.

        A plugin in Game Mode polls this while a game runs. One fork for each
        answer is a cost that the game pays, so the plain status reads files
        and nothing more.
        """
        runner = Recorder()
        ctl.status(run=runner)
        self.assertEqual(runner.commands, [])

    def test_the_plain_status_carries_every_area(self):
        answer = ctl.status()
        self.assertEqual(sorted(answer["areas"]), sorted(ctl.AREAS))
        self.assertFalse(answer["full"])

    def test_the_full_status_asks_about_the_units(self):
        runner = Recorder(said="ActiveState=active\nUnitFileState=enabled\n")
        answer = ctl.status(full=True, run=runner)
        self.assertTrue(runner.commands)
        self.assertEqual(answer["units"][ctl.UNITS[0]],
                         {"active": "active", "enabled": "enabled"})

    def test_the_status_says_whether_the_sudo_rule_is_there(self):
        """The usual reason a write fails, answered before it is tried."""
        self.assertIn("sudo_rule", ctl.status())


class EscalationTest(unittest.TestCase):
    """Nothing asks for a password unless the caller permits it."""

    def test_the_default_never_asks(self):
        self.assertEqual(ctl.escalate(["/bin/true"])[:2], ["sudo", "-n"])

    def test_a_caller_with_a_desktop_may_ask(self):
        self.assertEqual(ctl.escalate(["/bin/true"], may_prompt=True)[0],
                         "pkexec")

    def test_sudo_refusing_names_the_file_that_would_permit_it(self):
        """"sudo: a password is required" tells a person nothing to do."""
        runner = Recorder(code=1, said="sudo: a password is required")
        with self.assertRaises(ctl.CtlError) as caught:
            ctl.privileged(["/bin/true"], run=runner)
        self.assertIn(ctl.SUDO_RULE, str(caught.exception))

    def test_another_failure_is_reported_as_it_came(self):
        runner = Recorder(code=1, said="the drive is not connected")
        with self.assertRaises(ctl.CtlError) as caught:
            ctl.privileged(["/bin/true"], run=runner)
        self.assertIn("not connected", str(caught.exception))

    def test_every_applier_is_an_absolute_path_that_does_not_move(self):
        """A sudoers rule names a program. A clone is not a stable name.

        A rule for a path inside somebody's clone permits whatever that person
        puts at the path, which is not a rule at all.
        """
        for path in (ctl.APPLY_CONFIG, ctl.APPLY_POWER, ctl.APPLY_MOUNTS):
            self.assertTrue(path.startswith(ctl.INSTALL_DIR), path)
            self.assertTrue(os.path.isabs(path), path)


class AreaTest(unittest.TestCase):
    """Every area answers the same three questions."""

    def test_every_area_reads_offers_and_writes(self):
        for name in ctl.AREAS:
            self.assertEqual(sorted(ctl.AREA[name]),
                             ["keys", "offers", "read", "write"], name)

    def test_every_reader_takes_a_home_directory(self):
        """So that a test never touches the home of whoever runs it."""
        for name in ctl.AREAS:
            ctl.AREA[name]["read"](home=tempfile.gettempdir())

    def test_get_returns_the_settings_and_what_the_machine_offers(self):
        answer = ctl.get("strip")
        self.assertEqual(answer["area"], "strip")
        self.assertIn("NOTIFY", answer["settings"])
        self.assertIn("MAPPING", answer["offers"])

    def test_the_offers_are_the_menus_and_not_a_second_copy_of_them(self):
        """A front end that carried its own list would drift from this one."""
        from steamos_utility_center import config as config_module
        self.assertEqual(ctl.get("strip")["offers"]["MAPPING"],
                         list(config_module.MAPPINGS))


class KeyboardTest(unittest.TestCase):
    """The one area that needs no rights at all."""

    def setUp(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.home = holder.name

    def test_writing_a_layout_needs_no_program_and_no_password(self):
        runner = Recorder()
        answer = ctl.set_values("keyboard", {syssettings.LAYOUT: "de"},
                                run=runner, home=self.home)
        self.assertEqual(runner.commands, [])
        self.assertEqual(answer["settings"][syssettings.LAYOUT], "de")

    def test_a_layout_this_machine_does_not_know_is_refused(self):
        with self.assertRaises(ValueError):
            ctl.set_values("keyboard", {syssettings.LAYOUT: "not a layout"},
                           home=self.home)

    def test_the_refusal_writes_nothing(self):
        try:
            ctl.set_values("keyboard", {syssettings.LAYOUT: "!!"},
                           home=self.home)
        except ValueError:
            pass
        self.assertEqual(syssettings.read(self.home)[syssettings.LAYOUT],
                         syssettings.UNSET)


class StripTest(unittest.TestCase):
    """A change to one setting keeps every other one."""

    def setUp(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.path = os.path.join(holder.name, "steamos-utility-center.conf")
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("# a comment that must survive\nNOTIFY=1\nSPEED=2.0\n")
        self.was = ctl.CONFIG_PATH
        ctl.CONFIG_PATH = self.path
        self.addCleanup(setattr, ctl, "CONFIG_PATH", self.was)
        self.runner = Recorder()

    def _staged(self):
        """The file that the applier was given, read back."""
        self.assertTrue(self.runner.commands, "nothing reached the applier")
        return self.staged_text

    def test_a_change_keeps_the_other_settings_and_the_comments(self):
        held = {}

        def keep(command, timeout=120):
            self.runner.commands.append(list(command))
            with open(command[-1], encoding="utf-8") as handle:
                held["text"] = handle.read()
            return 0, "applied"

        ctl.set_values("strip", {"SPEED": 3.0}, run=keep)
        self.assertIn("SPEED=3.0", held["text"])
        self.assertIn("NOTIFY=1", held["text"])
        self.assertIn("a comment that must survive", held["text"])

    def test_a_value_the_service_refuses_never_reaches_the_applier(self):
        """Refused here, before a file that operates is replaced."""
        with self.assertRaises(ValueError):
            ctl.set_values("strip", {"LED_COUNT": -5}, run=self.runner)
        self.assertEqual(self.runner.commands, [])

    def test_a_setting_with_a_spelling_error_is_refused(self):
        """LED_COUTN=60 would write, and the service would then not start.

        The file is read again at the next start of the service, and a key it
        does not know stops it. A machine thus comes back from a reboot with no
        strip, because of a line that nothing refused when it was written.
        """
        with self.assertRaises(ctl.CtlError) as caught:
            ctl.set_values("strip", {"LED_COUTN": 60}, run=self.runner)
        self.assertIn("LED_COUTN", str(caught.exception))
        self.assertIn("LED_COUNT", str(caught.exception))
        self.assertEqual(self.runner.commands, [])

    def test_the_staged_file_is_removed_after_the_applier_ran(self):
        held = []

        def keep(command, timeout=120):
            held.append(command[-1])
            return 0, "applied"

        ctl.set_values("strip", {"SPEED": 3.0}, run=keep)
        self.assertFalse(os.path.exists(held[0]), "the staged file stayed")

    def test_the_staged_file_is_removed_when_the_applier_refused(self):
        def refuse(command, timeout=120):
            self.path_seen = command[-1]
            return 1, "no"

        with self.assertRaises(ctl.CtlError):
            ctl.set_values("strip", {"SPEED": 3.0}, run=refuse)
        self.assertFalse(os.path.exists(self.path_seen))


class DrivesTest(unittest.TestCase):
    """A drive is a record, so this area takes the whole list."""

    GAMES = {"uuid": "12345678-1234-1234-1234-123456789abc",
             "where": "/mnt/games", "type": "ext4",
             "options": "defaults,noatime", "timeout": "5s"}

    def test_the_whole_list_goes_to_the_applier(self):
        held = {}

        def keep(command, timeout=120):
            with open(command[-1], encoding="utf-8") as handle:
                held["text"] = handle.read()
            return 0, "one drive mounted"

        ctl.set_values("drives", {"drives": [self.GAMES]}, run=keep)
        self.assertEqual(json.loads(held["text"])[0]["where"], "/mnt/games")

    def test_a_mount_point_that_belongs_to_steamos_is_refused(self):
        runner = Recorder()
        with self.assertRaises(ValueError):
            ctl.set_values("drives", {"drives": [dict(self.GAMES,
                                                      where="/usr")]},
                           run=runner)
        self.assertEqual(runner.commands, [])

    def test_a_change_that_is_not_a_list_is_refused(self):
        with self.assertRaises(ctl.CtlError):
            ctl.set_values("drives", {"drives": "/mnt/games"},
                           run=Recorder())

    def test_repairing_runs_the_applier_on_the_record(self):
        runner = Recorder()
        ctl.action("repair-drives", run=runner)
        self.assertEqual(runner.commands[0][-2:],
                         [ctl.APPLY_MOUNTS, mounts.STATE_PATH])


class ActionTest(unittest.TestCase):
    """What is not a setting."""

    def test_an_action_that_does_not_exist_names_the_ones_that_do(self):
        with self.assertRaises(ctl.CtlError) as caught:
            ctl.action("explode")
        self.assertIn("cec-wake", str(caught.exception))

    def test_waking_the_television_asks_the_toolkit_and_not_root(self):
        """The toolkit has its own rule for the helper that needs root."""
        runner = Recorder()
        ctl.action("cec-wake", run=runner, home="/home/nobody")
        self.assertNotIn("sudo", runner.commands[0])
        self.assertNotIn("pkexec", runner.commands[0])
        self.assertIn("wake", runner.commands[0])

    def test_a_failed_action_is_a_refusal_and_not_a_crash(self):
        runner = Recorder(code=1, said="no adapter on /dev/cec0")
        with self.assertRaises(ctl.CtlError) as caught:
            ctl.action("cec-wake", run=runner)
        self.assertIn("no adapter", str(caught.exception))


class FullStatusTest(unittest.TestCase):
    """One question that cannot be answered must not lose the others."""

    def test_a_machine_with_no_cec_adapter_still_gets_its_units(self):
        """The toolkit answers in JSON. Anything else means it did not run.

        A page that showed nothing because of that would report the wrong
        fault: the units and the drives are knowable, and a person looking for
        them would be told about CEC.
        """
        runner = Recorder(said="bash: steamos-cec-toolkitctl: not found")
        answer = ctl.status(full=True, run=runner)
        self.assertIn("error", answer["cec"])
        self.assertIn(ctl.UNITS[0], answer["units"])

    def test_a_probe_that_raises_becomes_a_sentence(self):
        def explode():
            raise RuntimeError("lsblk is not on this machine")

        self.assertIn("lsblk", ctl._tried(explode)["error"])


class InstallerTest(unittest.TestCase):
    """The paths this module names are the paths the installer writes.

    A name that the two do not agree on is a `set` that fails on a machine
    where everything is installed, and the answer names a file that is there
    under another name.
    """

    def _install_text(self):
        with open(os.path.join(HERE, "..", "install.sh"),
                  encoding="utf-8") as handle:
            return handle.read()

    def test_the_installer_writes_every_applier_this_module_runs(self):
        text = self._install_text()
        for path in (ctl.APPLY_CONFIG, ctl.APPLY_POWER, ctl.APPLY_MOUNTS):
            self.assertIn(os.path.basename(path), text, path)

    def test_the_installer_installs_the_ctl_itself(self):
        self.assertIn("steamos-utility-centerctl", self._install_text())

    def test_the_uninstaller_removes_the_link_the_installer_makes(self):
        with open(os.path.join(HERE, "..", "uninstall.sh"),
                  encoding="utf-8") as handle:
            self.assertIn("CTL_COMMAND_LINK", handle.read())


class DiscoveryTest(unittest.TestCase):
    """`areas` is what lets a front end be written against this build."""

    def test_it_lists_every_area_and_every_action(self):
        answer = ctl.run_command(["areas"])
        self.assertEqual(answer["areas"], list(ctl.AREAS))
        self.assertEqual(answer["actions"], list(ctl.ACTIONS))


if __name__ == "__main__":
    unittest.main()
