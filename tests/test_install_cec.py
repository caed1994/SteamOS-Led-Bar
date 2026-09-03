# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""The bridge between a window with no terminal and an installer wanting sudo.

The toolkit's installer refuses to run as root and calls `sudo` about forty
times. This script is what lets a GUI drive it: it runs as root through
pkexec, drops back to the desktop user, and lends that user a sudo rule for
the length of the install.

The rule needs the most tests. A bad file in /etc/sudoers.d stops sudo on
the complete machine. A file that stays gives a permission that no user
asked to keep. So the tests here prove two things: the file is valid before
the install, and each exit removes it.

Most of this only runs as root, because the thing being tested is a program
whose first act is to check that it is root. The decision tests above that
line run anywhere.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "scripts", "install-cec.sh")
RULE = "/etc/sudoers.d/zz-steamos-utility-center-cec-install"

# Somebody who is not root and is not the user running the tests, to install
# for. Read from the machine rather than named, so this does not depend on a
# particular distribution's accounts.
def _somebody():
    for name in ("ubuntu", "deck", "claude", "nobody"):
        try:
            import pwd
            entry = pwd.getpwnam(name)
        except KeyError:
            continue
        if entry.pw_uid and os.path.isdir(entry.pw_dir):
            return name
    return ""


SOMEBODY = _somebody()
AS_ROOT = unittest.skipUnless(os.geteuid() == 0, "needs root")
HAS_USER = unittest.skipUnless(SOMEBODY, "no ordinary user with a home here")


def run(*args, **kwargs):
    return subprocess.run(["bash", SCRIPT] + list(args), capture_output=True,
                          text=True, **kwargs)


class ArgumentTest(unittest.TestCase):
    """What it refuses before it does anything at all."""

    def test_resume_wake_wants_on_or_off(self):
        # The second argument is a state here, not a directory, and anything
        # else is a switch that would silently do nothing.
        self.assertEqual(run("resume-wake").returncode, 2)
        self.assertEqual(run("resume-wake", "/some/dir").returncode, 2)

    @AS_ROOT
    def test_resume_wake_says_so_when_the_unit_is_not_installed(self):
        with tempfile.TemporaryDirectory() as root:
            done = run("resume-wake", "on", env=dict(os.environ, ROOT=root))
        self.assertEqual(done.returncode, 1)
        self.assertIn("install the CEC toolkit first", done.stderr)

    @AS_ROOT
    def test_resume_wake_enables_the_unit_and_nothing_else(self):
        with tempfile.TemporaryDirectory() as room:
            root = os.path.join(room, "root")
            os.makedirs(os.path.join(root, "etc/systemd/system"))
            with open(os.path.join(root, "etc/systemd/system",
                                   "steamos-cec-resume-wake.service"),
                      "w") as handle:
                handle.write("[Unit]\n")
            stubs = os.path.join(room, "stubs")
            os.makedirs(stubs)
            log = os.path.join(room, "calls")
            with open(os.path.join(stubs, "systemctl"), "w") as handle:
                handle.write('#!/usr/bin/env bash\necho "$*" >> "%s"\n' % log)
            os.chmod(os.path.join(stubs, "systemctl"), 0o755)
            done = run("resume-wake", "on",
                       env=dict(os.environ, ROOT=root,
                                PATH=stubs + ":" + os.environ["PATH"]))
            self.assertEqual(done.returncode, 0, done.stderr)
            with open(log) as handle:
                self.assertEqual(handle.read().split("\n")[0],
                                 "enable steamos-cec-resume-wake.service")

    def test_it_parses(self):
        done = subprocess.run(["bash", "-n", SCRIPT], capture_output=True,
                              text=True)
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_no_arguments_is_the_usage(self):
        done = run()
        self.assertEqual(done.returncode, 2)
        self.assertIn("usage:", done.stderr)

    def test_an_action_it_does_not_have_is_refused(self):
        # Not treated as one of the two it knows. "reinstall" reaching the
        # install path would be an install nobody asked for.
        self.assertEqual(run("reinstall", "/tmp").returncode, 2)

    def test_it_wants_somewhere_to_install_from(self):
        self.assertEqual(run("install").returncode, 2)


# The search for the radio of a controller was a subject of this file before.
# This script added the radios that the USB wake of the toolkit did not find.
# The class check is now correct in the toolkit, so its tests moved there. They
# are in UsbWakeMatchTest in tests/test_cec_toolkit.py, against
# cec-toolkit/bin/steamos-cec-usb-wake-apply.


class SourceTest(unittest.TestCase):

    @AS_ROOT
    def test_a_directory_with_no_installer_in_it_is_refused(self):
        """Before the sudo rule is written, not after.

        The order is more important than the refusal. An incorrect path that
        the script finds after the write of the rule leaves the machine with a
        permission for an install that cannot start.
        """
        with tempfile.TemporaryDirectory() as empty:
            done = run("install", empty)
            self.assertEqual(done.returncode, 1)
            self.assertIn("CEC toolkit", done.stderr)
            self.assertFalse(os.path.exists(RULE))

    @AS_ROOT
    def test_installing_for_root_is_refused(self):
        # Half of what the toolkit installs is user systemd units and a
        # WirePlumber config in a home directory. In root's home they would be
        # in a session that never runs a television.
        with _fake_toolkit() as source:
            done = run("install", source, "root", env=_no_pkexec())
            self.assertEqual(done.returncode, 1)
            self.assertIn("root", done.stderr)
            self.assertFalse(os.path.exists(RULE))


class NotRootTest(unittest.TestCase):

    @AS_ROOT
    @HAS_USER
    def test_run_as_an_ordinary_user_it_says_so_rather_than_half_working(self):
        with _fake_toolkit() as source:
            done = subprocess.run(
                ["runuser", "-u", SOMEBODY, "--", "bash", SCRIPT,
                 "install", source],
                capture_output=True, text=True, env=_no_pkexec())
        self.assertEqual(done.returncode, 1)
        self.assertIn("pkexec", done.stderr)


def _no_pkexec():
    """The environment without PKEXEC_UID, so the argument is what decides."""
    env = dict(os.environ)
    env.pop("PKEXEC_UID", None)
    return env


class _fake_toolkit:
    """A source directory shaped like the vendored tree, that installs nothing.

    The real installer needs cec-ctl, a CEC adapter and a session bus. These
    tests examine the script around it: the user of the run, the arguments, and
    the cleanup step. So this fixture replaces the installer with a program that
    reports its own conditions and exits.
    """

    def __init__(self, code=0):
        self.code = code

    def __enter__(self):
        self.holder = tempfile.TemporaryDirectory()
        # A second user must reach it. A directory from root has mode 0700, and
        # this fixture needs a second user for the programs in it. The real
        # tree has the same shape: it is in the clone of the desktop user.
        os.chmod(self.holder.name, 0o755)
        for name in ("install.sh", "uninstall.sh"):
            path = os.path.join(self.holder.name, name)
            with open(path, "w") as handle:
                handle.write(
                    "#!/usr/bin/env bash\n"
                    "echo \"ran=%s\"\n" % name +
                    "echo \"whoami=$(id -un)\"\n"
                    "echo \"home=$HOME\"\n"
                    "echo \"runtime=$XDG_RUNTIME_DIR\"\n"
                    "echo \"bus=$DBUS_SESSION_BUS_ADDRESS\"\n"
                    "echo \"flags=$*\"\n"
                    # One the rule grants and one it does not, so the probe
                    # says both that the bridge works and that it is narrowed.
                    "sudo -n install --version >/dev/null 2>&1"
                    " && echo 'sudo=yes' || echo 'sudo=no'\n"
                    "sudo -n id -u >/dev/null 2>&1"
                    " && echo 'anything=yes' || echo 'anything=no'\n"
                    "exit %d\n" % self.code)
            os.chmod(path, 0o755)
        return self.holder.name

    def __exit__(self, *_exc):
        self.holder.cleanup()
        return False


class BridgeTest(unittest.TestCase):
    """Runs the complete path. This needs root, because it is a root script."""

    def setUp(self):
        self.addCleanup(self._clear)
        self._clear()

    def _clear(self):
        if os.geteuid() == 0 and os.path.exists(RULE):
            os.remove(RULE)

    def _said(self, output):
        return dict(line.split("=", 1) for line in output.splitlines()
                    if "=" in line and not line.startswith(" "))

    @AS_ROOT
    @HAS_USER
    def test_the_installer_runs_as_the_desktop_user_and_not_as_root(self):
        with _fake_toolkit() as source:
            done = run("install", source, SOMEBODY, env=_no_pkexec())
        self.assertEqual(done.returncode, 0, done.stderr)
        said = self._said(done.stdout)
        self.assertEqual(said["whoami"], SOMEBODY)
        self.assertEqual(said["ran"], "install.sh")

    @AS_ROOT
    @HAS_USER
    def test_it_hands_over_the_session_the_user_half_needs(self):
        """systemctl --user talks to a per-user bus.

        Without these the root half of the install succeeds and the user half
        fails, which is the worst of the outcomes available: it looks
        installed, and none of the services are there.
        """
        import pwd
        uid = pwd.getpwnam(SOMEBODY).pw_uid
        with _fake_toolkit() as source:
            done = run("install", source, SOMEBODY, env=_no_pkexec())
        said = self._said(done.stdout)
        self.assertEqual(said["home"], pwd.getpwnam(SOMEBODY).pw_dir)
        self.assertEqual(said["runtime"], "/run/user/%d" % uid)
        self.assertIn("/run/user/%d/bus" % uid, said["bus"])

    @AS_ROOT
    @HAS_USER
    @AS_ROOT
    @HAS_USER
    def test_a_radio_it_cannot_add_does_not_fail_the_install(self):
        """Waking is one feature of nine. Losing it must not lose the rest."""
        with _fake_toolkit() as source:
            done = run("install", source, SOMEBODY,
                       env=dict(_no_pkexec(), SYSFS_USB="/nowhere",
                                ROOT="/nowhere"))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("Installed.", done.stdout)

    @AS_ROOT
    @HAS_USER
    def test_the_installer_can_use_sudo_while_it_runs(self):
        # The whole point. Without the rule the installer's first `sudo
        # install` asks for a password at a terminal that is not there.
        with _fake_toolkit() as source:
            done = run("install", source, SOMEBODY, env=_no_pkexec())
        self.assertEqual(self._said(done.stdout)["sudo"], "yes")

    @AS_ROOT
    @HAS_USER
    def test_the_rule_covers_the_installer_and_not_everything(self):
        """Narrowing this is documentation, not containment, and says so.

        `sudo install` writes each file at each path, so a rule with that
        command gives root rights. The comment in the script says that. The
        narrow rule gives one result: the file names each program that the
        installer calls. A later version of the source project that calls
        another program then fails here, with a message. Under a rule with
        ALL it gets the complete machine with no message.
        """
        with _fake_toolkit() as source:
            done = run("install", source, SOMEBODY, env=_no_pkexec())
        self.assertEqual(self._said(done.stdout)["anything"], "no")

    @AS_ROOT
    @HAS_USER
    def test_a_rule_that_does_not_check_out_is_not_installed(self):
        """The check is consulted, not just run.

        A bad file in /etc/sudoers.d does not break one rule. sudo refuses to
        start for each user, until a user with a root shell removes the file.
        On a machine where this panel is the other access method, a user
        cannot repair that at the desk. So the important test is not that the
        file is usually correct. It is that a bad file stops the install.
        """
        shims = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, shims)
        liar = os.path.join(shims, "visudo")
        with open(liar, "w") as handle:
            handle.write("#!/usr/bin/env bash\necho 'parse error' >&2\nexit 1\n")
        os.chmod(liar, 0o755)
        env = _no_pkexec()
        env["PATH"] = shims + os.pathsep + env["PATH"]
        with _fake_toolkit() as source:
            done = run("install", source, SOMEBODY, env=env)
        self.assertEqual(done.returncode, 1)
        self.assertIn("nothing was changed", done.stderr)
        self.assertFalse(os.path.exists(RULE))
        # And it stopped rather than carrying on without the rule, which
        # would be an install that hangs on a password prompt.
        self.assertNotIn("ran=", done.stdout)

    def test_the_rule_lists_every_program_the_installers_sudo(self):
        """Derived from the toolkit's own installers, not from memory.

        A `sudo` call upstream adds that this rule does not cover is an
        install that stops halfway with a password prompt nobody can answer -
        on somebody else's machine, with half the files written.
        """
        import re
        tree = os.path.join(HERE, "..", "cec-toolkit")
        called = set()
        for name in ("install.sh", "uninstall.sh"):
            with open(os.path.join(tree, name)) as handle:
                # At the command position only. A simple search also matches
                # the word in `echo "Configuring sudo permissions"`. This test
                # failed the first time for that reason: its own regular
                # expression was wrong, and the rule was correct.
                called.update(re.findall(
                    r"(?m)(?:^|&&|\|\||;)\s*(?:if\s+)?sudo\s+([a-z][a-z0-9-]*)\b",
                    handle.read()))
        with open(SCRIPT) as handle:
            listed = re.search(r"for program in ([^;]+); do",
                               handle.read()).group(1).split()
        # Two of the toolkit's own root helpers are called by absolute path
        # and are covered by the permanent sudoers file it installs before it
        # reaches them; the regex above only catches bare program names.
        self.assertEqual(sorted(called - set(listed)), [],
                         "the installers sudo something the rule omits")

    @AS_ROOT
    @HAS_USER
    def test_the_rule_is_gone_when_it_finishes(self):
        with _fake_toolkit() as source:
            run("install", source, SOMEBODY, env=_no_pkexec())
        self.assertFalse(os.path.exists(RULE), "the grant outlived the install")

    @AS_ROOT
    @HAS_USER
    def test_the_rule_is_gone_when_the_install_fails_too(self):
        """The path that gets forgotten.

        An installer with an exit code above zero is the normal case here. A
        machine with no CEC adapter, or with no cec-ctl, gives that code. A
        cleanup step after a success only therefore leaves the rule on most
        real machines.
        """
        with _fake_toolkit(code=1) as source:
            done = run("install", source, SOMEBODY, env=_no_pkexec())
        self.assertNotEqual(done.returncode, 0)
        self.assertFalse(os.path.exists(RULE))

    @AS_ROOT
    @HAS_USER
    def test_a_rule_left_behind_by_a_killed_run_is_cleared_by_the_next(self):
        # The one case the trap cannot cover is the one signal it cannot see.
        with open(RULE, "w") as handle:
            handle.write("# left over from a run that was killed\n")
        os.chmod(RULE, 0o440)
        with _fake_toolkit() as source:
            run("install", source, SOMEBODY, env=_no_pkexec())
        self.assertFalse(os.path.exists(RULE))

    @AS_ROOT
    @HAS_USER
    def test_what_it_writes_is_a_file_sudo_will_accept(self):
        """A malformed file in sudoers.d takes sudo down for the machine.

        The fault is not in one rule. sudo refuses to run at all. On a machine
        where this panel is the other access method, that condition is
        difficult to repair. So the script checks the file with the checker of
        sudo, before the file is in place.
        """
        with _fake_toolkit() as source:
            done = run("install", source, SOMEBODY, env=_no_pkexec())
        self.assertEqual(done.returncode, 0, done.stderr)
        # And sudo is still working afterwards, which is the actual claim.
        self.assertEqual(subprocess.run(["sudo", "-n", "true"]).returncode, 0)

    @AS_ROOT
    @HAS_USER
    def test_nothing_is_switched_on_by_the_install(self):
        """The page has eight switches, so the install must leave eight off.

        The installer of the toolkit turns the volume integration on by
        default. Without this step, the page opens with one function on, and no
        user selected it. set-external-volume also writes its own files, so the
        user can turn it on from the page later.
        """
        with _fake_toolkit() as source:
            done = run("install", source, SOMEBODY, env=_no_pkexec())
        flags = self._said(done.stdout)["flags"]
        self.assertIn("--no-external-volume", flags)
        self.assertNotIn("--enable-", flags)

    @AS_ROOT
    @HAS_USER
    def test_removing_runs_the_uninstaller_not_the_installer(self):
        with _fake_toolkit() as source:
            done = run("remove", source, SOMEBODY, env=_no_pkexec())
        self.assertEqual(done.returncode, 0, done.stderr)
        said = self._said(done.stdout)
        self.assertEqual(said["ran"], "uninstall.sh")
        # And no --no-external-volume: the uninstaller has no such flag and
        # would refuse the whole run over it.
        self.assertEqual(said["flags"].strip(), "")


class ToolkitTreeTest(unittest.TestCase):
    """The bridge and the tree it drives have to agree about the file names."""

    def test_the_installers_it_looks_for_are_the_ones_that_are_there(self):
        tree = os.path.join(HERE, "..", "cec-toolkit")
        for name in ("install.sh", "uninstall.sh"):
            self.assertTrue(os.path.exists(os.path.join(tree, name)), name)

    def test_the_uninstaller_really_has_no_feature_flags(self):
        # The claim above, checked against the file rather than remembered.
        with open(os.path.join(HERE, "..", "cec-toolkit",
                               "uninstall.sh")) as handle:
            self.assertNotIn("--no-external-volume", handle.read())


if __name__ == "__main__":                                  # pragma: no cover
    unittest.main()
