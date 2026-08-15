# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""Updating the clone, against real git repositories.

This is the one script that reaches into somebody's working copy, so it is
tested against actual repositories rather than a mock of one: what matters is
that it refuses in the cases where finishing the job would cost somebody their
work, and that a refusal really does leave everything as it was.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
UPDATE = os.path.join(HERE, "..", "scripts", "update.sh")

sys.path.insert(0, os.path.join(HERE, "..", "server"))
sys.path.insert(0, os.path.join(HERE, "..", "gui"))

import ledpanel  # noqa: E402


def git(directory, *args):
    return subprocess.run(("git",) + args, cwd=directory, check=True,
                          capture_output=True, text=True).stdout.strip()


class UpdateScriptTest(unittest.TestCase):
    """A bare "origin", a clone of it, and the script in between."""

    def setUp(self):
        if not shutil.which("git"):                     # pragma: no cover
            self.skipTest("git is not installed")
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

        self.origin = os.path.join(self.root, "origin")
        self.work = os.path.join(self.root, "work")     # where commits are made
        os.makedirs(self.origin)
        git(self.origin, "init", "--bare", "--initial-branch=main", ".")

        os.makedirs(self.work)
        self._init(self.work)
        git(self.work, "remote", "add", "origin", self.origin)
        self._commit(self.work, "README.md", "first\n", "first commit")
        git(self.work, "push", "-u", "origin", "main")

        self.clone = os.path.join(self.root, "clone")
        git(self.root, "clone", self.origin, self.clone)
        self._init(self.clone)
        # The script finds the clone from its own location, so it has to be
        # run from a copy inside the clone - which is how it is installed.
        os.makedirs(os.path.join(self.clone, "scripts"), exist_ok=True)
        shutil.copy(UPDATE, os.path.join(self.clone, "scripts", "update.sh"))

    def _init(self, directory):
        git(directory, "init", "--initial-branch=main", ".")
        git(directory, "config", "user.email", "test@example.com")
        git(directory, "config", "user.name", "Test")

    def _commit(self, directory, name, text, message):
        path = os.path.join(directory, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as handle:
            handle.write(text)
        git(directory, "add", name)
        git(directory, "commit", "-m", message)

    def _upstream_commit(self, message="second commit", branch="main",
                         name="README.md", text="second\n"):
        git(self.work, "checkout", branch)
        self._commit(self.work, name, text, message)
        git(self.work, "push", "origin", branch)

    def _run(self, *args, cwd=None):
        return subprocess.run(
            ["bash", os.path.join(self.clone, "scripts", "update.sh")]
            + list(args),
            cwd=cwd or self.root, capture_output=True, text=True)

    def _head(self, directory=None):
        return git(directory or self.clone, "rev-parse", "HEAD")

    # -- the ordinary path -------------------------------------------------

    def test_it_brings_the_clone_up_to_date(self):
        before = self._head()
        self._upstream_commit()
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotEqual(self._head(), before)
        self.assertIn("second commit", result.stdout)

    def test_it_says_so_when_there_is_nothing_to_do(self):
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Already up to date", result.stdout)

    def test_it_reports_what_it_brought(self):
        self._upstream_commit("a change worth naming")
        self.assertIn("a change worth naming", self._run().stdout)

    def test_it_runs_from_anywhere(self):
        # The panel starts it with its own working directory, whatever that is.
        self._upstream_commit()
        result = self._run(cwd=os.path.expanduser("~"))
        self.assertEqual(result.returncode, 0, result.stderr)

    # -- switching branches ------------------------------------------------

    def test_it_switches_to_another_branch(self):
        git(self.work, "checkout", "-b", "experiment")
        self._commit(self.work, "new.txt", "x\n", "on the branch")
        git(self.work, "push", "-u", "origin", "experiment")

        result = self._run("experiment")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(git(self.clone, "symbolic-ref", "--short", "HEAD"),
                         "experiment")
        self.assertTrue(os.path.exists(os.path.join(self.clone, "new.txt")))

    def test_it_can_come_back_again(self):
        git(self.work, "checkout", "-b", "experiment")
        self._commit(self.work, "new.txt", "x\n", "on the branch")
        git(self.work, "push", "-u", "origin", "experiment")
        self._run("experiment")

        result = self._run("main")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(git(self.clone, "symbolic-ref", "--short", "HEAD"),
                         "main")

    def test_an_unknown_branch_is_refused_with_the_list(self):
        result = self._run("nonesuch")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no branch called nonesuch", result.stderr)
        self.assertIn("main", result.stderr, "it should say what there is")

    # -- the refusals ------------------------------------------------------

    def test_local_changes_stop_it_and_survive(self):
        with open(os.path.join(self.clone, "README.md"), "w") as handle:
            handle.write("mine\n")
        self._upstream_commit()
        before = self._head()

        result = self._run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("local changes", result.stderr)
        self.assertEqual(self._head(), before, "it must not have moved")
        with open(os.path.join(self.clone, "README.md")) as handle:
            self.assertEqual(handle.read(), "mine\n",
                             "the edit must still be there")

    def test_an_untracked_file_is_not_in_the_way(self):
        # Notes of your own next to the code cannot conflict with a
        # fast-forward, and refusing over them would be unhelpful.
        with open(os.path.join(self.clone, "notes.txt"), "w") as handle:
            handle.write("mine\n")
        self._upstream_commit()

        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.clone, "notes.txt")))

    def test_local_commits_stop_it_and_survive(self):
        self._commit(self.clone, "local.txt", "mine\n", "my own work")
        mine = self._head()
        self._upstream_commit()

        result = self._run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("commits of its own", result.stderr)
        self.assertEqual(self._head(), mine, "the local commit must survive")

    def test_it_says_where_it_is_not_a_clone(self):
        plain = os.path.join(self.root, "plain", "scripts")
        os.makedirs(plain)
        shutil.copy(UPDATE, os.path.join(plain, "update.sh"))
        result = subprocess.run(["bash", os.path.join(plain, "update.sh")],
                                cwd=self.root, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not a git clone", result.stderr)
        self.assertIn("git clone", result.stderr, "it should say what to do")

    # -- checking without changing anything --------------------------------

    def test_check_reports_what_would_arrive(self):
        self._upstream_commit("something new")
        before = self._head()

        result = self._run("--check")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("something new", result.stdout)
        self.assertEqual(self._head(), before, "--check must not touch it")

    def test_check_is_happy_when_there_is_nothing(self):
        result = self._run("--check")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Already up to date", result.stdout)

    def test_check_warns_about_what_would_stop_the_update(self):
        with open(os.path.join(self.clone, "README.md"), "w") as handle:
            handle.write("mine\n")
        self._upstream_commit()
        result = self._run("--check")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("local changes", result.stdout)

    def test_check_looks_at_the_branch_it_was_given(self):
        git(self.work, "checkout", "-b", "experiment")
        self._commit(self.work, "new.txt", "x\n", "only on experiment")
        git(self.work, "push", "-u", "origin", "experiment")

        result = self._run("--check", "experiment")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("only on experiment", result.stdout)
        self.assertEqual(git(self.clone, "symbolic-ref", "--short", "HEAD"),
                         "main", "checking is not switching")


class PanelUpdateHelpersTest(UpdateScriptTest):
    """What the panel needs to know about the clone before and after.

    Same repositories as above, because these read a real one - a mock of git
    would only prove that the mock agrees with itself.
    """

    def test_it_recognises_a_clone(self):
        self.assertTrue(ledpanel.is_git_clone(self.clone))
        self.assertFalse(ledpanel.is_git_clone(self.root))

    def test_it_reads_the_current_branch(self):
        self.assertEqual(ledpanel.current_branch(self.clone), "main")

    def test_a_detached_head_is_on_no_branch(self):
        git(self.clone, "checkout", "--detach", "HEAD")
        self.assertEqual(ledpanel.current_branch(self.clone), "")

    def test_the_menu_lists_the_branches_the_clone_knows(self):
        git(self.work, "checkout", "-b", "experiment")
        self._commit(self.work, "new.txt", "x\n", "on the branch")
        git(self.work, "push", "-u", "origin", "experiment")
        git(self.clone, "fetch", "origin")
        self.assertEqual(ledpanel.known_branches(self.clone),
                         ["experiment", "main"])

    def test_the_menu_leaves_out_the_head_pointer(self):
        # origin/HEAD is a symbolic ref, not somewhere to update to.
        git(self.clone, "remote", "set-head", "origin", "main")
        self.assertNotIn("HEAD", ledpanel.known_branches(self.clone))

    def test_the_module_counts_as_changed_when_it_changed(self):
        before = ledpanel.head_commit(self.clone)
        self._upstream_commit("touch the module",
                             name="leds-valve-shim/module.c", text="int x;\n")
        self._run()
        self.assertTrue(ledpanel.module_changed(self.clone, before))

    def test_an_update_elsewhere_does_not_force_a_rebuild(self):
        # Rebuilding costs half a minute and needs kernel headers, so it has
        # to be worth it.
        before = ledpanel.head_commit(self.clone)
        self._upstream_commit("only the README")
        self._run()
        self.assertFalse(ledpanel.module_changed(self.clone, before))

    def test_not_knowing_where_it_started_means_rebuild(self):
        # A stale module costs the bar; a needless rebuild costs a minute.
        self.assertTrue(ledpanel.module_changed(self.clone, ""))


class UpdateCommandTest(unittest.TestCase):
    """The commands the panel builds, without running them."""

    def test_updating_needs_no_privileges(self):
        # The clone belongs to the user; only installing what it brings does.
        command = ledpanel.update_command("/repo")
        self.assertNotIn("pkexec", command)
        self.assertIn("/repo/scripts/update.sh", command)

    def test_a_branch_is_passed_on(self):
        self.assertIn("debug", ledpanel.update_command("/repo", "debug"))

    def test_checking_is_the_same_command_with_a_flag(self):
        command = ledpanel.update_command("/repo", "main", check=True)
        self.assertIn("--check", command)
        self.assertIn("main", command)

    def test_no_branch_means_the_one_it_is_on(self):
        self.assertEqual(ledpanel.update_command("/repo"),
                         ["/repo/scripts/update.sh"])

    def test_branch_names_are_read_off_for_each_ref(self):
        self.assertEqual(
            ledpanel.parse_branches("main\nHEAD\ndebug\n\nmain\n"),
            ["debug", "main"])

    def test_nothing_known_is_an_empty_menu_not_a_crash(self):
        self.assertEqual(ledpanel.parse_branches(""), [])


if __name__ == "__main__":
    unittest.main()
