# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""The installer's prerequisite hunting, without installing anything.

A first install on a fresh SteamOS stalls before the module is ever compiled:
the rootfs is read-only, pacman's keyring has never been initialised, and the
headers are named after the exact kernel rather than after "linux". The last
of those is the one you cannot guess, so it is worked out from the running
kernel - and that arithmetic is what is checked here. Everything that would
touch the system is left to the machine it runs on.
"""

import os
import re
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
INSTALLER = os.path.join(HERE, "..", "install.sh")

from shellvalues import shell_value                   # noqa: E402


def _function(name):
    """One shell function lifted out of the installer, by source.

    Sourcing the whole script is not an option: it installs things. Lifting
    the function keeps the test honest - it runs the same text that ships.
    """
    with open(INSTALLER) as handle:
        text = handle.read()
    match = re.search(r"^%s\(\) \{.*?^\}$" % re.escape(name),
                      text, re.M | re.S)
    assert match, "%s not found in install.sh" % name
    return match.group(0)


def _run(functions, call, roots=("/usr/lib/modules", "/lib/modules")):
    """Run one call against the real function bodies, in a bash of our own."""
    script = "set -euo pipefail\nMODULES_ROOTS=(%s)\n%s\n%s\n" % (
        " ".join('"%s"' % root for root in roots),
        "\n".join(_function(name) for name in functions),
        call)
    return subprocess.run(["bash", "-c", script],
                          capture_output=True, text=True)


class KernelHeadersPackageTest(unittest.TestCase):
    """Which headers package matches the kernel that is running.

    Getting this wrong is not a failed install but a worse one: headers for a
    kernel you are not running build a module whose vermagic will not load.
    """

    FUNCTIONS = ("kernel_headers_package",)

    def _package(self, release, pkgbase=None):
        root = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, root, True)
        if pkgbase is not None:
            os.makedirs(os.path.join(root, release))
            with open(os.path.join(root, release, "pkgbase"), "w") as handle:
                handle.write(pkgbase + "\n")
        done = _run(self.FUNCTIONS,
                    'kernel_headers_package "%s" || true' % release,
                    roots=(root,))
        return done.stdout.strip()

    def test_the_package_beside_the_modules_wins(self):
        # Arch writes it there, so it is the answer rather than a guess.
        self.assertEqual(self._package("6.6.1-arch1-1", "linux"),
                         "linux-headers")

    def test_a_steamos_kernel_is_read_the_same_way(self):
        self.assertEqual(
            self._package("6.16.5-valve1-3-neptune-616", "linux-neptune-616"),
            "linux-neptune-616-headers")

    def test_without_pkgbase_the_release_still_names_it(self):
        # Reported from a fresh install: this is the package that was needed,
        # and "pacman -Ss headers" is what the installer used to offer instead.
        self.assertEqual(self._package("6.16.5-valve1-3-neptune-616"),
                         "linux-neptune-616-headers")

    def test_older_neptune_kernels_too(self):
        for release, expected in (
                ("6.11.11-valve20-1-neptune-611", "linux-neptune-611-headers"),
                ("6.1.52-valve16-1-neptune-61", "linux-neptune-61-headers")):
            self.assertEqual(self._package(release), expected, release)

    def test_a_kernel_it_cannot_name_says_so(self):
        # Better to print the distribution's own instructions than to invent
        # a package name and have pacman refuse it.
        self.assertEqual(self._package("5.15.0-generic"), "")


class BuildDirectoryTest(unittest.TestCase):
    FUNCTIONS = ("kernel_build_dir",)

    def test_it_finds_the_headers_in_either_module_root(self):
        root = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, root, True)
        release = "6.16.5-valve1-3-neptune-616"
        os.makedirs(os.path.join(root, release, "build"))
        done = _run(self.FUNCTIONS, 'kernel_build_dir "%s"' % release,
                    roots=(root,))
        self.assertEqual(done.returncode, 0)
        self.assertTrue(done.stdout.strip().endswith("/build"))

    def test_it_fails_when_they_are_not_installed(self):
        root = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, root, True)
        done = _run(self.FUNCTIONS, 'kernel_build_dir "6.16.5-neptune-616"',
                    roots=(root,))
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(done.stdout.strip(), "")


USER_UNIT = os.path.join(HERE, "..", "scripts", "user-unit.sh")


class InvokingUserTest(unittest.TestCase):
    """Which desktop user a root script is working on behalf of.

    Reported from a Steam Machine: after updating from the control panel,
    "Services survive Game Mode" was the one thing broken. The installer does
    turn lingering on - but only after working out who to turn it on for, and
    that answer came from SUDO_USER alone. The panel runs the installer with
    pkexec, which sets PKEXEC_UID and no SUDO_USER, so the whole step decided
    there was nobody to install for: no lingering, no menu entry, and any new
    user unit never installed either.
    """

    def _sourced(self, call, **environment):
        """Run one call against the real file, with `id` and `getent` stubbed.

        Stubbed rather than run against this machine's accounts: the point is
        which variable is read, and a build container has no desktop user to
        ask about.
        """
        stubs = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, stubs, True)
        home = os.path.join(stubs, "home")
        os.makedirs(home)
        self._write(stubs, "id", 'case "$1" in\n'
                                 '  -nu) [[ "$2" == "1000" ]] && echo deck ;;\n'
                                 '  -u)  echo 1000 ;;\n'
                                 'esac\n')
        self._write(stubs, "getent",
                    'echo "deck:x:1000:1000::%s:/bin/bash"\n' % home)

        env = {"PATH": stubs + ":" + os.environ.get("PATH", ""),
               "HOME": home}
        env.update(environment)
        return subprocess.run(
            ["bash", "-c", 'set -euo pipefail\nsource "%s"\n%s' %
             (USER_UNIT, call)],
            capture_output=True, text=True, env=env)

    def _write(self, directory, name, body):
        path = os.path.join(directory, name)
        with open(path, "w") as handle:
            handle.write("#!/usr/bin/env bash\n" + body)
        os.chmod(path, 0o755)

    def test_pkexec_says_who_asked(self):
        done = self._sourced('watcher_user_dirs && echo "$WATCHER_USER"',
                             PKEXEC_UID="1000")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.strip(), "deck")

    def test_sudo_from_a_terminal_still_works(self):
        done = self._sourced('watcher_user_dirs && echo "$WATCHER_USER"',
                             SUDO_USER="deck")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.strip(), "deck")

    def test_neither_means_there_is_nobody_to_install_for(self):
        # Quietly: the caller owns that message, and it has one.
        done = self._sourced("watcher_user_dirs")
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(done.stdout.strip(), "")

    def test_root_is_not_a_desktop_user(self):
        # A root shell has no session to run the watchers in, and installing
        # them into /root is worse than not installing them.
        done = self._sourced("watcher_user_dirs", SUDO_USER="root")
        self.assertNotEqual(done.returncode, 0)


class LingerTest(unittest.TestCase):
    """Turning lingering on, and then checking rather than assuming."""

    FUNCTIONS = ("linger_is_on", "enable_linger")

    def _with_loginctl(self, call, body):
        stubs = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, stubs, True)
        path = os.path.join(stubs, "loginctl")
        with open(path, "w") as handle:
            handle.write("#!/usr/bin/env bash\n" + body)
        os.chmod(path, 0o755)
        script = 'set -euo pipefail\nwarn() { echo "warn: $*" >&2; }\n%s\n%s' % (
            "\n".join(_function(name) for name in self.FUNCTIONS), call)
        env = dict(os.environ, PATH=stubs + ":" + os.environ.get("PATH", ""),
                   STATE=os.path.join(stubs, "state"))
        return subprocess.run(["bash", "-c", script], capture_output=True,
                              text=True, env=env)

    def test_it_is_on_after_being_turned_on(self):
        done = self._with_loginctl('enable_linger deck', '''
case "$1" in
  enable-linger) touch "$STATE" ;;
  show-user) [[ -e "$STATE" ]] && echo "Linger=yes" || echo "Linger=no" ;;
esac
''')
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_a_loginctl_that_says_yes_and_does_nothing_is_not_believed(self):
        """The failure this was written for cannot be seen any other way.

        enable-linger returning 0 and leaving it off looks exactly like
        success from here, and the machine then disagrees with the log.
        """
        done = self._with_loginctl('enable_linger deck', '''
case "$1" in
  enable-linger) exit 0 ;;
  show-user) echo "Linger=no" ;;
esac
''')
        self.assertNotEqual(done.returncode, 0)

    def test_what_loginctl_said_is_passed_on(self):
        done = self._with_loginctl('enable_linger deck', '''
case "$1" in
  enable-linger) echo "Could not enable linger: Access denied" >&2; exit 1 ;;
  show-user) echo "Linger=no" ;;
esac
''')
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("Access denied", done.stderr)

    def test_one_already_on_is_left_alone(self):
        done = self._with_loginctl('enable_linger deck', '''
case "$1" in
  enable-linger) echo "asked" >&2; exit 1 ;;
  show-user) echo "Linger=yes" ;;
esac
''')
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertNotIn("asked", done.stderr)


UNINSTALLER = os.path.join(HERE, "..", "uninstall.sh")
SLEEP_HOOK = os.path.join(HERE, "..", "systemd-sleep", "steamos-led-serial")


class RootfsTest(unittest.TestCase):
    """Unlocking SteamOS's read-only root, and putting it back.

    Against the shared file rather than against either script, because being
    shared is the fix: the installer had this and the uninstaller had a later
    copy of its own that only covered the kernel module - so removing the
    suspend hook, three steps earlier, failed on a read-only /usr and took the
    whole uninstall with it.
    """

    def _with_readonly(self, call, state="enabled"):
        """Run one call with a steamos-readonly that answers and records."""
        stubs = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, stubs, True)
        with open(os.path.join(stubs, "state"), "w") as handle:
            handle.write(state + "\n")
        path = os.path.join(stubs, "steamos-readonly")
        with open(path, "w") as handle:
            handle.write(
                '#!/usr/bin/env bash\n'
                'case "$1" in\n'
                '  status)  cat "%s/state" ;;\n'
                '  disable) echo disabled > "%s/state"\n'
                '           echo disable >> "%s/log" ;;\n'
                '  enable)  echo enabled > "%s/state"\n'
                '           echo enable >> "%s/log" ;;\n'
                'esac\n' % ((stubs,) * 5))
        os.chmod(path, 0o755)

        done = subprocess.run(
            ["bash", "-c", 'set -euo pipefail\nsource "%s"\n%s' %
             (USER_UNIT, call)], capture_output=True, text=True,
            env={"PATH": stubs + ":" + os.environ.get("PATH", "")})
        try:
            with open(os.path.join(stubs, "log")) as handle:
                did = handle.read().split()
        except OSError:
            did = []
        return done, did

    def test_a_locked_rootfs_is_unlocked(self):
        done, did = self._with_readonly(
            'unlock_rootfs; echo "relock=$ROOTFS_RELOCK"')
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(did[0], "disable")
        self.assertIn("relock=1", done.stdout)

    def test_one_somebody_else_unlocked_is_left_as_they_left_it(self):
        # Running steamos-readonly disable yourself and then this should not
        # end with the rootfs locked again behind you.
        done, did = self._with_readonly(
            'unlock_rootfs; echo "relock=$ROOTFS_RELOCK"', state="disabled")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(did, [])
        self.assertIn("relock=0", done.stdout)

    def test_an_abandoned_run_still_locks_it_again(self):
        # Including the paths that die() rather than reaching the end.
        done, did = self._with_readonly('unlock_rootfs; exit 1')
        self.assertEqual(done.returncode, 1)
        self.assertEqual(did, ["disable", "enable"])

    def test_it_is_locked_once_however_it_ends(self):
        done, did = self._with_readonly('unlock_rootfs; relock_rootfs')
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(did, ["disable", "enable"])

    def test_a_machine_without_it_is_not_a_failure(self):
        # Every distribution that is not SteamOS. No stub on the PATH here,
        # which is exactly what such a machine looks like.
        done = subprocess.run(
            ["bash", "-c", 'set -euo pipefail\nsource "%s"\n'
                           'unlock_rootfs; echo "$ROOTFS_RELOCK"' % USER_UNIT],
            capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.strip(), "0")


class SleepHookTest(unittest.TestCase):
    """The word that hands the strip to the ESP before the machine sleeps."""

    def _setup(self, config="", make_pipe=True):
        room = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, room, True)
        pipe = os.path.join(room, "notify")
        if make_pipe:
            os.mkfifo(pipe)
        path = os.path.join(room, "conf")
        with open(path, "w") as handle:
            handle.write(config.replace("@PIPE@", pipe))
        return room, pipe, path

    def _run(self, action, config_path, timeout=10, **environment):
        env = dict(os.environ, STEAMOS_LED_CONFIG=config_path)
        env.update(environment)
        return subprocess.run(["sh", SLEEP_HOOK, action, "suspend"],
                              capture_output=True, text=True, env=env,
                              timeout=timeout)

    def _read(self, pipe, action, config_path, **environment):
        """What arrives in the pipe, with something actually reading it."""
        reader = os.open(pipe, os.O_RDONLY | os.O_NONBLOCK)
        self.addCleanup(os.close, reader)
        self._run(action, config_path, **environment)
        try:
            return os.read(reader, 4096).decode()
        except BlockingIOError:
            return ""

    def test_it_writes_into_the_pipe_the_configuration_names(self):
        """NOTIFY_FIFO is a setting, and this used to know only the default.

        A machine that had moved the pipe got a hook writing where nothing was
        reading: the strip went dark on suspend instead of breathing, with
        nothing anywhere connecting the two.
        """
        _room, pipe, config = self._setup("NOTIFY_FIFO=@PIPE@\n")
        self.assertEqual(self._read(pipe, "pre", config), "standby\n")

    def test_the_last_line_wins_the_way_the_service_reads_it(self):
        _room, pipe, config = self._setup(
            "# a comment\nNOTIFY_FIFO=/tmp/moved-since\n"
            "NOTIFY_FIFO = \"@PIPE@\"  \n")
        self.assertEqual(self._read(pipe, "post", config), "resume\n")

    def test_the_environment_outranks_the_file(self):
        # Which is the order the service reads its own settings in.
        _room, pipe, config = self._setup("NOTIFY_FIFO=/tmp/not-this-one\n")
        self.assertEqual(
            self._read(pipe, "pre", config, STEAMOS_LED_NOTIFY_FIFO=pipe),
            "standby\n")

    def test_a_file_that_says_nothing_leaves_the_default_standing(self):
        hook = open(SLEEP_HOOK).read()
        self.assertIn('DEFAULT_FIFO="/run/steamos-led-serial/notify"', hook)
        _room, _pipe, config = self._setup("LED_COUNT=17\n")
        # Nothing at the default path on a build machine, so it is the quiet
        # exit below rather than a write - which is the point.
        self.assertEqual(self._run("pre", config).returncode, 0)

    def test_a_pipe_nobody_is_reading_does_not_hang_the_suspend(self):
        """systemd waits here before it suspends.

        A FIFO opened for writing alone blocks until somebody opens the other
        end, so a pipe left behind by a service that died stopped the machine
        going to sleep at all - and the `|| exit 0` could not help, because
        nothing failed, it simply never returned.
        """
        _room, pipe, config = self._setup("NOTIFY_FIFO=@PIPE@\n")
        # Named through the environment as well as the file, so this reaches
        # the write however the path is worked out - it is the open that is
        # being tested, not the lookup above it.
        done = self._run("post", config, timeout=10,      # raises if it hangs
                         STEAMOS_LED_NOTIFY_FIFO=pipe)
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_no_pipe_at_all_is_a_quiet_success(self):
        # The service may be stopped, or notifications switched off.
        _room, _pipe, config = self._setup("NOTIFY_FIFO=@PIPE@\n",
                                           make_pipe=False)
        done = self._run("pre", config)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, "")

    def test_it_says_nothing_on_the_actions_that_are_not_ours(self):
        _room, pipe, config = self._setup("NOTIFY_FIFO=@PIPE@\n")
        self.assertEqual(self._read(pipe, "hibernate-nonsense", config), "")

    def test_it_parses(self):
        done = subprocess.run(["sh", "-n", SLEEP_HOOK])
        self.assertEqual(done.returncode, 0)


class InstallerShapeTest(unittest.TestCase):
    """Properties of install.sh that are easy to break from a distance."""

    def setUp(self):
        with open(INSTALLER) as handle:
            self.text = handle.read()

    def test_the_installer_parses(self):
        done = subprocess.run(["bash", "-n", INSTALLER],
                              capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_the_command_the_readme_names_is_one_you_can_type(self):
        """The README says "steamos-led-serial --x" fourteen times.

        Everything installs into /var/lib, which is on nobody's PATH, so for a
        long time every one of those was a command you could read and not run:
        the answer was "command not found" and no way to tell from the page
        what the real name was. The link is what makes the documentation true,
        so the two are checked against each other rather than against a memory
        of having fixed it.
        """
        link = shell_value("COMMAND_LINK")
        name = os.path.basename(link)
        # In a directory a shell actually searches. Naming the file correctly
        # somewhere nobody looks is exactly the bug this is about: it lived in
        # /var/lib, which is right for surviving a SteamOS update and useless
        # for typing.
        self.assertIn(os.path.dirname(link),
                      ("/usr/local/bin", "/usr/bin", "/bin"),
                      "%s is not on anybody's PATH" % link)

        with open(os.path.join(HERE, "..", "README.md")) as handle:
            readme = handle.read()
        # This project's own commands only - the page is full of git, pacman
        # and systemctl, and none of those are ours to install. An allow-list
        # of other people's tools would need extending every time one is
        # mentioned, which is a test that eventually gets edited rather than
        # read.
        typed = set(re.findall(r"^\s*(?:sudo )?(steamos-led-[a-z-]+)\b",
                               readme, re.M))
        self.assertIn(name, typed, "the README names no such command")
        self.assertEqual(typed - {name, "steamos-led-serial.conf"}, set(),
                         "the README names a command nothing installs")

    def test_the_user_units_are_installed_before_anything_that_can_fail(self):
        """Order, not presence - and this one was reported, not imagined.

        They used to be installed at the very end, after pacman, the kernel
        module and the firmware flash. Under set -e any of those ends the run,
        and then the units are simply not there: "Unit steamos-led-phone.
        service not found", on a machine where everything else had worked.
        They depend on none of it, so they go on disk first.
        """
        installed = self.text.index("\ninstall_user_units || true")
        for marker, what in (("pacman -S --needed", "installing packages"),
                             ('"$dir/install.sh"', "the kernel module"),
                             ("flash_firmware", "the firmware flash"),
                             ("install_control_panel ||", "the panel entry")):
            self.assertLess(installed, self.text.index(marker),
                            "%s runs before the user units are installed"
                            % what)

    def test_starting_them_stays_after_the_service_is_up(self):
        # The achievement watcher wants the service running and the bridge
        # wants its pipe, so starting early would only make both retry.
        started = self.text.index("\nstart_user_units || true")
        self.assertLess(self.text.index("systemctl restart steamos-led-serial"),
                        started)
        self.assertLess(self.text.index("\ninstall_user_units || true"), started)

    def test_the_uninstaller_takes_back_the_same_link(self):
        # And only when it is still ours: somebody who put their own there is
        # entitled to keep it.
        with open(os.path.join(HERE, "..", "uninstall.sh")) as handle:
            text = handle.read()
        self.assertIn("$COMMAND_LINK", text)
        self.assertIn("readlink", text)

    def test_platformio_is_installed_the_way_steamos_allows(self):
        # pip cannot write to a read-only rootfs, and --user lands in a
        # directory the next system update resets.
        self.assertIn("platformio-core-installer", self.text)
        self.assertNotIn("pip install --user platformio", self.text)

    def test_the_headers_hint_names_a_package_rather_than_a_search(self):
        # It used to print "pacman -Ss headers | grep ..." and leave the
        # reader to pick one, which is exactly where a first install stalls.
        self.assertNotIn("pacman -Ss", self.text)
        self.assertIn("kernel_headers_package", self.text)

    def test_the_keyring_is_prepared_before_pacman_is_used(self):
        # A SteamOS that has never installed a package fails every -S on
        # signatures, which reads like the package is missing.
        self.assertIn("pacman-key --init", self.text)
        self.assertIn("pacman-key --populate", self.text)

    def test_the_rootfs_is_unlocked_before_anything_is_written(self):
        """Order, not presence - that is what went wrong.

        Unlocking around each write in turn left the suspend hook being
        installed into /usr/lib while the rootfs had been locked again, and
        set -e turns that into an aborted install. It is unlocked once, before
        the first question is even asked.
        """
        unlock = self.text.index("\nunlock_rootfs || true")
        for path, what in (('SLEEP_HOOK_PATH"', "the suspend hook"),
                           ("pacman -S --needed", "installing packages"),
                           ('"$dir/install.sh"', "the kernel module")):
            self.assertLess(unlock, self.text.index(path),
                            "%s runs before the rootfs is unlocked" % what)

    def test_the_uninstaller_unlocks_before_it_removes_anything(self):
        """Order, and this one was measured rather than imagined.

        The suspend hook lives under /usr/lib/systemd, and `rm -f` on a locked
        rootfs does not quietly do nothing - it fails with "Read-only file
        system", which under set -e ended the uninstall three steps in: udev
        rule gone, and the service files, the command link and the
        configuration all still there with nothing said about it. The
        uninstaller had an unlock of its own, but only in front of the kernel
        module, forty lines further down.
        """
        with open(UNINSTALLER) as handle:
            text = handle.read()
        unlock = text.index("\nunlock_rootfs || true")
        for path, what in (('rm -f "$SLEEP_HOOK_PATH"', "the suspend hook"),
                           ('rm -f "$MODULE_PATH"', "the kernel module")):
            self.assertLess(unlock, text.index(path),
                            "%s is removed before the rootfs is unlocked"
                            % what)
        # And its own later copy is gone, or the two go on drifting.
        self.assertNotIn("ROOTFS_WAS_READONLY", text)

    def test_both_scripts_unlock_it_the_same_way(self):
        # Which is the whole point of the shared file - see RootfsTest.
        with open(USER_UNIT) as handle:
            shared = handle.read()
        self.assertIn("ROOTFS_RELOCK=1", shared)
        self.assertIn("trap relock_rootfs EXIT", shared)
        for path in (INSTALLER, UNINSTALLER):
            with open(path) as handle:
                text = handle.read()
            self.assertIn("unlock_rootfs || true", text, path)
            # Calling it, not carrying a copy of it. The advice printed for a
            # machine that has to install packages by hand still names the
            # command, which is a sentence and not a second implementation.
            self.assertNotIn("unlock_rootfs() {", text, path)
            self.assertNotIn("relock_rootfs() {", text, path)

    def test_platformio_is_offered_whatever_the_firmware_answer_was(self):
        """It used to be reached only from inside the flashing step.

        The firmware question defaults to "no", so pressing Enter through the
        installer meant PlatformIO was never mentioned - and the first time you
        did want to flash, it was a download away.
        """
        offer = self.text.index("\nensure_platformio || true")
        guard = self.text.index('[[ -n "$FLASH_ENV" ]] || return 0')
        self.assertLess(offer, self.text.index('if [[ -n "$FLASH_ENV" ]]; then'),
                        "the offer has to come before the flashing step")
        self.assertNotIn("install_platformio", self.text[guard:guard + 2000],
                         "flash_firmware should not ask a second time")

    def test_the_path_line_reaches_the_profile_unexpanded(self):
        # Written into .bashrc, so it has to survive as $HOME rather than as
        # whatever $HOME was on the machine that ran the installer.
        self.assertIn(
            """PLATFORMIO_PATH_LINE='export PATH="$HOME/.platformio/penv/bin:$PATH"'""",
            self.text)

    def test_the_path_line_is_not_stacked_on_every_run(self):
        self.assertIn("grep -qF '.platformio/penv/bin' \"$profile\"", self.text)

    def test_a_partial_upgrade_is_used_on_purpose(self):
        # -Syu would pull a newer kernel than the one now running, and the
        # headers would then match nothing.
        self.assertNotIn("pacman -Syu", self.text)


if __name__ == "__main__":
    unittest.main()
