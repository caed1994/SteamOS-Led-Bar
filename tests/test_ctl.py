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
import shutil
import subprocess
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

    def test_repairing_stages_the_record_rather_than_naming_it(self):
        """One rule for each applier, and the repair needs none of its own.

        A rule that also named the record would be a second file that a
        program with root reads on the word of the caller.
        """
        runner = Recorder()
        ctl.action("repair-drives", run=runner)
        self.assertEqual(runner.commands[0][-2], ctl.APPLY_MOUNTS)
        self.assertNotEqual(runner.commands[0][-1], mounts.STATE_PATH)


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


class SudoersTest(unittest.TestCase):
    """The rule that makes a password unnecessary, and its limits."""

    def test_there_is_no_wildcard_in_it(self):
        """A rule with a `*` permits every argument.

        The argument of these programs is a file that they read as root, so a
        rule with a wildcard is a rule that lets a caller name any file.
        """
        for line in ctl.sudoers_text("deck").splitlines():
            if line.startswith("deck"):
                self.assertNotIn("*", line, line)

    def _rules(self):
        return [line for line in ctl.sudoers_text("deck").splitlines()
                if line.startswith("deck")]

    def test_each_line_names_one_program_and_one_argument(self):
        """Three appliers, and the switch that takes one of two words."""
        rules = self._rules()
        self.assertEqual(len(rules), 5)
        for line in rules:
            after = line.split("NOPASSWD:")[1].split()
            self.assertEqual(len(after), 2, line)
            self.assertTrue(after[0].startswith(ctl.INSTALL_DIR), line)

    def test_the_appliers_are_permitted_one_staged_file_each(self):
        for line in self._rules():
            program, argument = line.split("NOPASSWD:")[1].split()
            if program in ctl.APPLIER.values():
                self.assertTrue(argument.startswith(ctl.STAGED_DIR), line)

    def test_the_switch_is_permitted_its_two_words_and_nothing_else(self):
        """Two lines rather than one with a `*` in it.

        The argument is one of two words. Both words thus fit in the rule, and
        a wildcard would permit every other word with them.
        """
        words = sorted(line.split()[-1] for line in self._rules()
                       if ctl.RESUME_WAKE in line)
        self.assertEqual(words, ["off", "on"])

    def test_it_permits_exactly_the_files_the_command_stages(self):
        """A rule for a file the command never writes is a rule with no use."""
        rules = ctl.sudoers_text("deck")
        for path in ctl.STAGED.values():
            self.assertIn(path, rules)

    def test_no_rule_permits_the_chown(self):
        """It walks a whole drive as root, so a person answers for it.

        The comments name it, because a reader of the file must know why it is
        not there. This looks at the rules and not at the comments.
        """
        rules = [line for line in ctl.sudoers_text("deck").splitlines()
                 if not line.startswith("#") and line.strip()]
        self.assertTrue(rules)
        for line in rules:
            self.assertNotIn("chown", line)
            # The applier takes the directory to give away as a second
            # argument. A rule of two words after NOPASSWD cannot carry one.
            self.assertEqual(len(line.split("NOPASSWD:")[1].split()), 2, line)

    def test_a_user_name_that_could_add_a_line_is_refused(self):
        for name in ("deck ALL=(root) NOPASSWD: /bin/sh", "a\nroot", "",
                     "../root", "deck ALL"):
            with self.assertRaises(ctl.CtlError, msg=name):
                ctl.sudoers_text(name)

    def test_the_rule_is_read_by_visudo_before_it_is_installed(self):
        """A sudoers file that does not parse takes sudo away from a machine."""
        runner = Recorder()
        ctl.permit("deck", run=runner)
        self.assertEqual(runner.commands[0][0], "visudo")
        self.assertIn("-c", runner.commands[0])

    def test_a_rule_that_visudo_refuses_is_never_installed(self):
        class Refuse(Recorder):
            def __call__(self, command, timeout=120):
                self.commands.append(list(command))
                if command[0] == "visudo":
                    return 1, ">>> syntax error"
                return 0, ""

        runner = Refuse()
        with self.assertRaises(ctl.CtlError):
            ctl.permit("deck", run=runner)
        self.assertEqual(len(runner.commands), 1, "it went on after visudo")

    def test_the_staging_directory_belongs_to_that_user(self):
        runner = Recorder()
        ctl.permit("deck", run=runner)
        made = [one for one in runner.commands if one[:2] == ["install", "-d"]]
        self.assertEqual(len(made), 1)
        self.assertIn("deck", made[0])
        self.assertIn(ctl.STAGED_DIR, made[0])

    def test_visudo_itself_parses_the_rule(self):
        """The one check that a test in this file cannot do by reading.

        A rule that this project believes in and sudo does not is a machine
        where nothing works and the reason is in a file nobody opens.
        """
        if not shutil.which("visudo"):
            self.skipTest("no visudo on this machine")
        staged = tempfile.NamedTemporaryFile("w", suffix=".sudoers",
                                             delete=False)
        with staged:
            staged.write(ctl.sudoers_text("deck"))
        self.addCleanup(os.unlink, staged.name)
        done = subprocess.run(["visudo", "-c", "-f", staged.name],
                              capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_the_keep_list_carries_the_rule_across_an_update(self):
        """Without it, an update leaves Game Mode unable to change a setting."""
        self.assertIn(ctl.SUDO_RULE, mounts.PROJECT_FILES)

    def test_the_uninstaller_removes_the_same_file(self):
        sys.path.insert(0, HERE)
        from shellvalues import shell_value
        self.assertEqual(shell_value("SUDO_RULE_PATH"), ctl.SUDO_RULE)


class StagedFileTest(unittest.TestCase):
    """What each applier does with the file it is given.

    The staged file is in a directory that the desktop user can write, and the
    applier reads it as root. That is the one place where a caller with no
    rights reaches a program that has them.
    """

    APPLIERS = ("apply-config.sh", "apply-power.sh", "apply-mounts.sh")

    def _text(self, name):
        with open(os.path.join(HERE, "..", "scripts", name),
                  encoding="utf-8") as handle:
            return handle.read()

    def test_every_applier_refuses_a_symlink(self):
        """install(1) as root follows one, and copies a file nobody can read.

        /etc/shadow into /etc/steamos-utility-center.conf, which is 0644.
        """
        for name in self.APPLIERS:
            self.assertIn('[[ ! -L "$STAGED" ]]', self._text(name), name)

    def test_every_applier_refuses_a_file_of_another_user(self):
        for name in self.APPLIERS:
            self.assertIn("STAGED_OWNER", self._text(name), name)

    def test_the_check_is_before_the_file_is_used(self):
        """A check after the read is a check that never ran in time."""
        for name in self.APPLIERS:
            text = self._text(name)
            self.assertLess(text.index("STAGED_OWNER"), text.index("install "),
                            name)

    def test_the_command_stages_where_the_appliers_are_permitted(self):
        for area, path in ctl.STAGED.items():
            self.assertEqual(os.path.dirname(path), ctl.STAGED_DIR, area)

    def _refusal(self, name, path, **environment):
        """Runs one applier and returns (exit status, what it printed).

        Only the refusals are run here. Each of them exits before the applier
        writes anything, so no test touches /etc.
        """
        where = dict(os.environ)
        where.pop("PKEXEC_UID", None)
        where.pop("SUDO_UID", None)
        where.update(environment)
        done = subprocess.run(
            ["bash", os.path.join(HERE, "..", "scripts", name), path],
            capture_output=True, text=True, env=where)
        return done.returncode, done.stdout + done.stderr

    def test_a_symlink_is_refused_by_every_applier(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        link = os.path.join(holder.name, "staged.conf")
        os.symlink("/etc/shadow", link)
        for name in self.APPLIERS:
            code, said = self._refusal(name, link)
            self.assertEqual(code, 2, "%s: %s" % (name, said))
            self.assertIn("symlink", said, name)

    def test_a_file_of_another_user_is_refused(self):
        """The staging directory belongs to one user. A file of another one
        in it is a file that this program has no reason to read as root.
        """
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        path = os.path.join(holder.name, "staged.conf")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("NOTIFY=1\n")
        mine = os.stat(path).st_uid
        for name in self.APPLIERS:
            # Whoever runs the suite made that file, so any other uid is a
            # different user. The suite runs as root on some machines, and
            # root is not exempt: only a call with no user at all is, which is
            # the boot-time unit.
            code, said = self._refusal(name, path, SUDO_UID=str(mine + 1))
            self.assertEqual(code, 2, "%s: %s" % (name, said))
            self.assertIn("does not belong", said, name)

    def test_a_file_that_is_not_there_is_refused(self):
        for name in self.APPLIERS:
            code, said = self._refusal(name, "/no/such/staged.conf")
            self.assertEqual(code, 2, "%s: %s" % (name, said))

    def test_a_directory_it_cannot_write_falls_back_to_a_temporary_file(self):
        """An installation with no staging directory still answers.

        `sudo -n` then refuses the argument, and that refusal names the rule
        that is missing. That is a better answer than a failure to write.
        """
        path = ctl.stage("HELLO=1\n", "/proc/no/such/place/strip.conf")
        self.addCleanup(os.unlink, path)
        self.assertTrue(os.path.exists(path))


class GraphicsCardTest(unittest.TestCase):
    """The card, which LACT drives and this command does not.

    Nothing here speaks to a daemon. The build machine has none, and a test
    that needed one would pass on one machine and not on another.
    """

    def setUp(self):
        from steamos_utility_center import lact
        self.lact = lact
        self.was = (lact.available, lact.state, lact.set_gpu_config,
                    lact.confirm)
        self.addCleanup(self._put_back)
        self.sent = []
        lact.available = lambda path=None: True
        lact.state = lambda path=None, ask=None: {
            "gpu": "1002:1234", "name": "A card",
            "config": {"power_cap": 100.0},
            "clocks": {}, "stats": {"power": {"cap_current": 100.0,
                                              "cap_min": 50.0,
                                              "cap_max": 200.0,
                                              "cap_default": 150.0}}}
        lact.set_gpu_config = lambda gpu, config, path=None: (
            self.sent.append((gpu, config)), 5)[1]
        lact.confirm = lambda path=None, keep=True: self.sent.append(
            ("confirm", keep))

    def _put_back(self):
        (self.lact.available, self.lact.state, self.lact.set_gpu_config,
         self.lact.confirm) = self.was

    def test_the_cheap_status_asks_no_daemon(self):
        """A socket has a timeout, and the cheap half must not wait on one."""
        self.assertEqual(ctl.gpu_read(), {"available": True})

    def test_the_controls_come_from_the_card(self):
        offered = ctl.gpu_offers()
        self.assertEqual(offered["gpu"], "1002:1234")
        keys = [knob["key"] for knob in offered["knobs"]]
        self.assertIn("power_cap", keys)

    def test_a_control_this_card_does_not_have_is_refused(self):
        with self.assertRaises(ctl.CtlError) as caught:
            ctl.set_values("gpu", {"max_core_clock": 2000})
        self.assertIn("max_core_clock", str(caught.exception))
        self.assertEqual(self.sent, [])

    def test_the_whole_document_goes_back_and_not_a_patch(self):
        """set_gpu_config replaces the document rather than patching it.

        Anything not carried across is a setting silently turned off on
        somebody's card: a fan curve that they set in the panel, for one.
        """
        self.lact.state = lambda path=None, ask=None: {
            "gpu": "1002:1234", "name": "A card",
            "config": {"power_cap": 100.0, "fan_control_settings": {"mode":
                                                                    "curve"}},
            "clocks": {}, "stats": {"power": {"cap_current": 100.0,
                                              "cap_min": 50.0,
                                              "cap_max": 200.0,
                                              "cap_default": 150.0}}}
        ctl.set_values("gpu", {"power_cap": 120})
        _gpu, config = self.sent[0]
        self.assertEqual(config["power_cap"], 120)
        self.assertIn("fan_control_settings", config)

    def test_writing_never_confirms_by_itself(self):
        """The daemon puts the card back unless it is told to keep it.

        That is not a step to skip. A voltage offset that is too low hangs
        the card, and a hang that was kept comes back at every boot.
        """
        said = ctl.set_values("gpu", {"power_cap": 120})["said"]
        self.assertNotIn("confirm", [one[0] for one in self.sent])
        self.assertIn("gpu-keep", said)

    def test_keeping_it_is_an_action_of_its_own(self):
        ctl.action("gpu-keep")
        self.assertEqual(self.sent[-1], ("confirm", True))

    def test_putting_it_back_is_another(self):
        ctl.action("gpu-revert")
        self.assertEqual(self.sent[-1], ("confirm", False))

    def test_a_machine_with_no_card_says_so_and_does_not_fail(self):
        self.lact.state = lambda path=None, ask=None: None
        self.assertEqual(ctl.gpu_offers()["knobs"], [])
        with self.assertRaises(ctl.CtlError):
            ctl.set_values("gpu", {"power_cap": 120})


class DiscoveryTest(unittest.TestCase):
    """`areas` is what lets a front end be written against this build."""

    def test_it_lists_every_area_and_every_action(self):
        answer = ctl.run_command(["areas"])
        self.assertEqual(answer["areas"], list(ctl.AREAS))
        self.assertEqual(answer["actions"], list(ctl.ACTIONS))


if __name__ == "__main__":
    unittest.main()
