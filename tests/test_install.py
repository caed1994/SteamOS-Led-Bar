# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""The installer's prerequisite hunting, without installing anything.

A first install on a new SteamOS stops before the build of the module. The
rootfs is read-only, the keyring of pacman is empty, and the headers carry
the name of the exact kernel and not the name "linux". A user cannot guess
that last name, so the installer calculates it from the running kernel.
These tests examine that calculation. Each step that changes the system runs
on the machine of the user only.
"""

import glob
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
INSTALLER = os.path.join(HERE, "..", "install.sh")

from shellvalues import shell_names, shell_value      # noqa: E402


def _function(name):
    """One shell function lifted out, by source, wherever it lives.

    A source of the complete installer is not possible, because it installs
    programs. This method takes the text of the function from the file, so the
    test runs the same text as the machine. It searches both files, because a
    function that the uninstaller also uses moves into scripts/user-unit.sh. A
    test with one file fails at that move and not at a change of behaviour.
    """
    for path in (INSTALLER, os.path.join(HERE, "..", "scripts",
                                         "user-unit.sh"),
                 os.path.join(HERE, "..", "uninstall.sh")):
        with open(path) as handle:
            match = re.search(r"^%s\(\) \{.*?^\}$" % re.escape(name),
                              handle.read(), re.M | re.S)
        if match:
            return match.group(0)
    raise AssertionError("%s is in none of the three scripts" % name)


def _run(functions, call, roots=("/usr/lib/modules", "/lib/modules")):
    """Run one call against the real function bodies, in a bash of our own."""
    script = "set -euo pipefail\nMODULES_ROOTS=(%s)\n%s\n%s\n" % (
        " ".join('"%s"' % root for root in roots),
        "\n".join(_function(name) for name in functions),
        call)
    return subprocess.run(["bash", "-c", script],
                          capture_output=True, text=True)


class KernelHeadersPackageTest(unittest.TestCase):
    """The headers package for the kernel of this machine.

    An incorrect name here does not give a failed install. It gives a worse
    result: headers of another kernel build a module with a vermagic that the
    kernel refuses.
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
    """The desktop user of a root script.

    A Steam Machine gave this report: after an update from the control panel,
    "Services survive Game Mode" was the one broken item. The installer turns
    lingering on, but first it must find the user. That answer came from
    SUDO_USER only. The panel starts the installer with pkexec, and pkexec sets
    PKEXEC_UID and no SUDO_USER. The complete step therefore found no user: no
    lingering, no menu entry, and no new user unit.
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
SLEEP_HOOK = os.path.join(HERE, "..", "systemd-sleep", "steamos-utility-center")


class RootfsTest(unittest.TestCase):
    """Unlocking SteamOS's read-only root, and putting it back.

    These tests read the shared file and not one of the two scripts, because
    the shared file is the correction. The installer had this code, and the
    uninstaller had a later copy of its own for the kernel module only. The
    removal of the suspend hook, three steps earlier, therefore failed on a
    read-only /usr and stopped the complete uninstall.
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
        # A user can run steamos-readonly disable first. This code must then
        # not lock the rootfs again at the end.
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


class UninstallHomeTest(unittest.TestCase):
    """What the installer wrote into the desktop user's home, taken back.

    None of these files is under a root path, so no rm -f line of the
    uninstaller reached them. The menu entry stayed in the launcher, for a
    project that was no longer on the machine. Its icon stayed in the icon
    theme. The PATH line from the installer stayed in .bashrc, below a comment
    with the name of an installer that was gone.
    """

    OWN_BASHRC = "# mine\nalias ll='ls -l'\nexport EDITOR=vim\n"

    def _home(self, sizes=(512,), bashrc=None):
        room = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, room, True)
        home = os.path.join(room, "home")

        applications = os.path.join(home, ".local", "share", "applications")
        os.makedirs(applications)
        entry = os.path.join(applications, "steamos-utility-center.desktop")
        with open(entry, "w") as handle:
            handle.write("[Desktop Entry]\nName=SteamOS Utility Center\n")

        icons = []
        for size in sizes:
            where = os.path.join(home, ".local", "share", "icons", "hicolor",
                                 "%dx%d" % (size, size), "apps")
            os.makedirs(where)
            icons.append(os.path.join(where, "steamos-utility-center.png"))
            with open(icons[-1], "wb") as handle:
                handle.write(b"\x89PNG\r\n")

        profile = os.path.join(home, ".bashrc")
        if bashrc is not None:
            with open(profile, "w") as handle:
                handle.write(bashrc)
        return room, home, entry, icons, profile

    def _call(self, call, room, home):
        """Run one uninstaller function, with the machine's answers stubbed."""
        stubs = os.path.join(room, "stubs")
        os.makedirs(stubs, exist_ok=True)
        self._write(stubs, "id", 'case "$1" in\n'
                                 '  -nu) [[ "$2" == "1000" ]] && echo deck ;;\n'
                                 '  -u)  echo 1000 ;;\n'
                                 'esac\n')
        self._write(stubs, "getent",
                    'echo "deck:x:1000:1000::%s:/bin/bash"\n' % home)
        # runuser is how a root script does something as the user; here the
        # test already *is* that user, so it drops the wrapper and runs it.
        self._write(stubs, "runuser",
                    'while [[ $# -gt 0 ]]; do\n'
                    '  case "$1" in\n'
                    '    -u) shift 2 ;;\n'
                    '    --) shift; break ;;\n'
                    '    *) break ;;\n'
                    '  esac\n'
                    'done\n'
                    'exec "$@"\n')
        self._write(stubs, "update-desktop-database", "exit 0\n")

        script = ('set -euo pipefail\nsource "%s"\nPLATFORMIO_NOTE=0\n%s\n%s\n'
                  % (USER_UNIT,
                     "\n".join(_function(name) for name in
                               ("remove_menu_entry", "remove_platformio_path",
                                "add_platformio_to_path")),
                     call))
        return subprocess.run(
            ["bash", "-c", script], capture_output=True, text=True,
            env={"PATH": stubs + ":" + os.environ.get("PATH", ""),
                 "PKEXEC_UID": "1000", "HOME": home})

    def _write(self, directory, name, body):
        path = os.path.join(directory, name)
        with open(path, "w") as handle:
            handle.write("#!/usr/bin/env bash\n" + body)
        os.chmod(path, 0o755)

    def test_the_menu_entry_goes(self):
        room, home, entry, icons, _profile = self._home()
        done = self._call("remove_menu_entry", room, home)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertFalse(os.path.exists(entry), "still in the launcher")
        self.assertFalse(os.path.exists(icons[0]), "icon still in the theme")

    def test_an_icon_left_at_another_size_goes_too(self):
        # The width from the PNG gives the directory, so an older install can
        # have put a file in a directory that this code does not read.
        room, home, _entry, icons, _profile = self._home(sizes=(128, 512))
        self._call("remove_menu_entry", room, home)
        for icon in icons:
            self.assertFalse(os.path.exists(icon), icon)

    def test_a_home_with_none_of_it_says_nothing(self):
        room = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, room, True)
        home = os.path.join(room, "home")
        os.makedirs(home)
        done = self._call("remove_menu_entry", room, home)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.strip(), "")

    def test_the_path_line_goes_and_nothing_else_does(self):
        """Under both comments: the current one, and the one before it.

        The uninstaller deletes these two lines from the .bashrc of a user by an
        exact match, and that makes a change to their file safe. The text is
        therefore an interface. The new name changed the comment. Without the old
        spelling, each .bashrc from an older install keeps a line with the name of
        an installer that no longer exists, and no code removes it.
        """
        line = shell_value("PLATFORMIO_PATH_LINE")
        for note in (shell_value("PLATFORMIO_PATH_NOTE"),
                     shell_value("OLD_PLATFORMIO_PATH_NOTE")):
            room, home, _entry, _icons, profile = self._home(
                bashrc=self.OWN_BASHRC + "\n" + note + "\n" + line + "\n")
            done = self._call("remove_platformio_path", room, home)
            self.assertEqual(done.returncode, 0, done.stderr)
            with open(profile) as handle:
                left = handle.read()
            self.assertNotIn(".platformio", left, note)
            self.assertNotIn("added by the SteamOS", left, note)
            for kept in self.OWN_BASHRC.strip().splitlines():
                self.assertIn(kept, left, "it ate something of theirs")

    def test_a_profile_it_never_touched_is_left_alone(self):
        room, home, _entry, _icons, profile = self._home(bashrc=self.OWN_BASHRC)
        done = self._call("remove_platformio_path", room, home)
        self.assertEqual(done.returncode, 0, done.stderr)
        with open(profile) as handle:
            self.assertEqual(handle.read(), self.OWN_BASHRC)

    def test_a_line_somebody_wrote_themselves_is_not_ours_to_delete(self):
        """Only the two exact lines, and only under that comment.

        A user who added PlatformIO to their own PATH before this project must
        keep that line.
        """
        theirs = 'export PATH="$HOME/.platformio/penv/bin:/opt/bin:$PATH"'
        room, home, _entry, _icons, profile = self._home(
            bashrc=self.OWN_BASHRC + theirs + "\n")
        self._call("remove_platformio_path", room, home)
        with open(profile) as handle:
            self.assertIn(theirs, handle.read())

    def test_no_bashrc_at_all_is_not_a_failure(self):
        room, home, _entry, _icons, _profile = self._home()
        done = self._call("remove_platformio_path", room, home)
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_what_the_installer_writes_is_what_this_takes_back(self):
        """The two halves against each other, which is the only real proof.

        Both use the constants of the shared file. But a test of that alone
        passes for two functions that agree about one string and are different
        in each other property, such as a space at the end or another comment.
        So this test runs the writer, then the remover, and the file must be
        exactly the first file.
        """
        room, home, _entry, _icons, profile = self._home(bashrc=self.OWN_BASHRC)
        # WATCHER_HOME is what add_platformio_to_path writes against; the
        # remover works it out for itself through watcher_user_dirs.
        done = self._call(
            'WATCHER_USER=deck WATCHER_HOME="%s" add_platformio_to_path\n'
            'grep -ci platformio "%s"\n'
            'remove_platformio_path' % (home, profile), room, home)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("2", done.stdout.split(),
                      "the installer wrote no line to take back")
        with open(profile) as handle:
            self.assertEqual(handle.read().rstrip("\n"),
                             self.OWN_BASHRC.rstrip("\n"))


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

        On a machine with another path for the pipe, the hook wrote to a path
        with no reader. The strip then went dark at a suspend and did not
        breathe, and no message connected the two.
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
        self.assertIn('DEFAULT_FIFO="/run/steamos-utility-center/notify"', hook)
        _room, _pipe, config = self._setup("LED_COUNT=17\n")
        # A build machine has no file at the default path, so the hook takes
        # the exit below and writes nothing. That is the subject of this test.
        self.assertEqual(self._run("pre", config).returncode, 0)

    def test_it_goes_as_soon_as_the_service_says_it_is_ready(self):
        """It waited half a second on every suspend of every machine.

        The wait is there because systemd suspends the machine when this
        script returns, and the strip must have the message first. But half a
        second was a guess. The service says when it is done, and that is
        usually some tens of milliseconds.
        """
        room, pipe, config = self._setup("NOTIFY_FIFO=@PIPE@\n")
        reader = os.open(pipe, os.O_RDONLY | os.O_NONBLOCK)
        self.addCleanup(os.close, reader)

        def answer():
            time.sleep(0.05)
            open(os.path.join(room, "standby-done"), "w").close()

        said = threading.Thread(target=answer)
        said.start()
        self.addCleanup(said.join)
        started = time.monotonic()
        self._run("pre", config)
        took = time.monotonic() - started
        self.assertLess(took, 0.4, "it waited past the answer")

    def test_a_service_that_does_not_answer_costs_what_it_always_did(self):
        """A machine too old to know that file is one this cannot slow down.

        The wait ends by itself, and the strip gets the same half second it
        had before there was anything to wait for.
        """
        _room, pipe, config = self._setup("NOTIFY_FIFO=@PIPE@\n")
        reader = os.open(pipe, os.O_RDONLY | os.O_NONBLOCK)
        self.addCleanup(os.close, reader)
        started = time.monotonic()
        self._run("pre", config)
        took = time.monotonic() - started
        self.assertGreater(took, 0.4, "it did not wait for the strip at all")
        self.assertLess(took, 1.5, "it waited longer than it ever did")

    def test_the_mark_of_the_last_suspend_is_not_an_answer_about_this_one(self):
        """Without the removal, every suspend after the first went at once,
        and the strip went dark on each of them.
        """
        room, pipe, config = self._setup("NOTIFY_FIFO=@PIPE@\n")
        reader = os.open(pipe, os.O_RDONLY | os.O_NONBLOCK)
        self.addCleanup(os.close, reader)
        open(os.path.join(room, "standby-done"), "w").close()
        started = time.monotonic()
        self._run("pre", config)
        self.assertGreater(time.monotonic() - started, 0.4)

    def test_the_service_and_the_hook_name_the_same_file(self):
        """Two names for one file is a wait that never ends."""
        sys.path.insert(0, os.path.join(HERE, "..", "server"))
        from steamos_utility_center import service
        self.assertIn('"$(dirname "$FIFO")/%s"' % service.STANDBY_DONE,
                      open(SLEEP_HOOK).read())

    def test_a_pipe_nobody_is_reading_does_not_hang_the_suspend(self):
        """systemd waits here before it suspends.

        An open of a FIFO for a write waits until a program opens the other end.
        A pipe from a service that stopped therefore stopped each suspend of the
        machine. The `|| exit 0` does not help there, because nothing failed. The
        call did not return.
        """
        _room, pipe, config = self._setup("NOTIFY_FIFO=@PIPE@\n")
        # The environment and the file both give the path, so this test
        # reaches the write for each lookup method. The subject of the test is
        # the open call and not the lookup above it.
        done = self._run("post", config, timeout=10,      # raises if it hangs
                         STEAMOS_LED_NOTIFY_FIFO=pipe)
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_no_pipe_at_all_is_a_quiet_success(self):
        # The service can be stopped, or the notifications can be off.
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
        """The README says "steamos-utility-center --x" fourteen times.

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
        # The commands of this project only. The page also holds git, pacman
        # and systemctl, and this project installs none of those. A list of the
        # tools of other projects needs a new entry at each new mention. A
        # person then edits that test and does not read it.
        typed = set(re.findall(r"^\s*(?:sudo )?(steamos-utility-center[a-z-]*)\b",
                               readme, re.M))
        self.assertIn(name, typed, "the README names no such command")
        # Each command that this project puts on the PATH, read out of the
        # script rather than named here. A second program came into the
        # project, and the README gave it to the users before a link existed.
        # A list in this file is a list that the third program is not in, so
        # this asks the script which links it makes.
        linked = {os.path.basename(shell_value(each))
                  for each in shell_names("COMMAND_LINK")}
        self.assertEqual(typed - linked - {"steamos-utility-center.conf"}, set(),
                         "the README names a command nothing installs")

    def _body(self, name):
        """The text of one function of the installer, without its neighbours.

        The steps of a module are in a function of that module now, so an
        order in this file is an order inside one function. A search of the
        whole file would find the same call in another module's function.
        """
        start = self.text.index("\n%s() {\n" % name)
        return self.text[start:self.text.index("\n}\n", start)]

    def test_the_user_units_are_installed_before_anything_that_can_fail(self):
        """The order, and not the presence. A user reported this fault.

        The installer wrote them at the end before, after pacman, the kernel
        module and the firmware flash. Under set -e each of those steps can end
        the run, and the units are then absent. A machine gave "Unit
        steamos-utility-center-phone.service not found", and each other step
        worked there. The units need none of those steps, so they go to disk
        first.

        All four are steps of the LED module now, so the order is the order
        inside install_led.
        """
        body = self._body("install_led")
        installed = body.index("install_user_units || true")
        for marker, what in (("install_led_module",
                              "the packages and the kernel module"),
                             ("install_led_firmware", "the firmware flash"),
                             ("start_led_service", "starting the service")):
            self.assertLess(installed, body.index(marker),
                            "%s runs before the user units are installed"
                            % what)

    def test_starting_them_stays_after_the_service_is_up(self):
        # The achievement watcher wants the service running and the bridge
        # wants its pipe, so starting early would only make both retry.
        body = self._body("start_led_service")
        started = body.index("start_user_units || true")
        self.assertLess(
            body.index("systemctl restart steamos-utility-center"), started)
        # And the units are on disk before that, which install_led decides.
        led = self._body("install_led")
        self.assertLess(led.index("install_user_units || true"),
                        led.index("start_led_service"))

    def test_the_uninstaller_takes_back_the_same_link(self):
        # And only when it is still ours: somebody who put their own there is
        # entitled to keep it.
        with open(os.path.join(HERE, "..", "uninstall.sh")) as handle:
            text = handle.read()
        self.assertIn("$COMMAND_LINK", text)
        self.assertIn("readlink", text)

    def test_platformio_is_installed_the_way_steamos_allows(self):
        """pip cannot write to a read-only rootfs, and --user is swept away.

        Every script that mentions it, not only the installer: the flashing
        helper told you to run the pip line when it could not find pio, which
        is the one thing the installer goes out of its way not to do. Taking
        that advice gets you a flash that works today and stops working after
        the next system update, for no reason anybody would trace back here.
        """
        self.assertIn("platformio-core-installer", self.text)
        for name in ("install.sh", "flash-esp.sh",
                     os.path.join("scripts", "flash-firmware.sh")):
            with open(os.path.join(HERE, "..", name)) as handle:
                text = handle.read()
            self.assertNotIn("pip install --user platformio", text, name)
            self.assertNotIn("pip install platformio", text, name)

    def test_the_headers_hint_names_a_package_rather_than_a_search(self):
        # It used to print "pacman -Ss headers | grep ..." and leave the
        # reader to pick one, which is exactly where a first install stalls.
        self.assertNotIn("pacman -Ss", self.text)
        self.assertIn("kernel_headers_package", self.text)

    def test_the_keyring_is_prepared_before_pacman_is_used(self):
        # A SteamOS with no earlier package install fails each -S on the
        # signature, and that message reads as an absent package.
        self.assertIn("pacman-key --init", self.text)
        self.assertIn("pacman-key --populate", self.text)

    def test_the_rootfs_is_unlocked_before_anything_is_written(self):
        """The order, and not the presence. The order was the fault.

        An unlock around each write left the rootfs locked again at the write of
        the suspend hook into /usr/lib. Under set -e that ends the install. The
        script now unlocks the rootfs one time, before the first question.
        """
        unlock = self.text.index("\nunlock_rootfs || true")
        for path, what in (('SLEEP_HOOK_PATH"', "the suspend hook"),
                           ("pacman -S --needed", "installing packages"),
                           ('"$dir/install.sh"', "the kernel module")):
            self.assertLess(unlock, self.text.index(path),
                            "%s runs before the rootfs is unlocked" % what)

    def test_the_uninstaller_takes_the_cec_toolkit_with_it(self):
        """Installed from vendor/ by a button on the page, so it is ours.

        Its units, helpers, udev rule and sudoers file were all put on the
        machine by this project and none of them were touched here before -
        an uninstall left the whole toolkit running.
        """
        with open(UNINSTALLER) as handle:
            text = handle.read()
        with open(USER_UNIT) as handle:
            shared = handle.read()
        self.assertIn("remove_cec_toolkit", text)
        # The body moved into the shared file, because a removal of the CEC
        # module takes the same files off. The uninstaller calls it whatever
        # the module state says, so "uninstall" still means every part.
        self.assertIn("remove_cec_toolkit() {", shared)
        self.assertIn("install-cec.sh", shared)

    def test_the_uninstaller_turns_lingering_back_off(self):
        # The installer turns it on. Reported as "left in place", it stayed on
        # every machine this was ever installed on.
        with open(UNINSTALLER) as handle:
            text = handle.read()
        self.assertIn("loginctl disable-linger", text)

    def test_purging_takes_the_panel_s_own_settings_too(self):
        # They live in the desktop user's home rather than in /etc, which is
        # how they were missed.
        with open(UNINSTALLER) as handle:
            text = handle.read()
        self.assertIn('rm -f "$WATCHER_HOME/.config/$PANEL_CONFIG"', text)

    def test_the_uninstaller_unlocks_before_it_removes_anything(self):
        """The order, and a measurement gave this result.

        The suspend hook is under /usr/lib/systemd. `rm -f` on a locked rootfs
        does not return with a success. It fails with "Read-only file system",
        and under set -e that ended the uninstall after three steps. The udev
        rule was gone, and the service files, the command link and the
        configuration were still on disk, with no message. The uninstaller had
        its own unlock, but only before the kernel module, forty lines below.
        """
        with open(UNINSTALLER) as handle:
            text = handle.read()
        unlock = text.index("\nunlock_rootfs || true")
        for path, what in (('rm -f "$SLEEP_HOOK_PATH"', "the suspend hook"),
                           ("\n    remove_stale_shims", "the kernel module")):
            self.assertLess(unlock, text.index(path),
                            "%s is removed before the rootfs is unlocked"
                            % what)
        # And its own later copy is gone, or the two go on drifting.
        self.assertNotIn("ROOTFS_WAS_READONLY", text)

    def test_both_scripts_unlock_it_the_same_way(self):
        # That is the purpose of the shared file. See RootfsTest.
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

        The firmware question has the default "no". A user who presses Enter
        through the installer therefore never reads about PlatformIO. At the
        first flash, that user must download it first.
        """
        body = self._body("install_led_firmware")
        self.assertLess(body.index("ensure_platformio || true"),
                        body.index('[[ -n "$FLASH_ENV" ]] || return 0'),
                        "the offer has to come before the flashing step")
        self.assertNotIn("install_platformio", self._body("flash_firmware"),
                         "flash_firmware should not ask a second time")

    def test_the_path_line_reaches_the_profile_unexpanded(self):
        # Written into .bashrc, so it has to survive as $HOME rather than as
        # whatever $HOME was on the machine that ran the installer.
        with open(USER_UNIT) as handle:
            shared = handle.read()
        self.assertIn(
            """PLATFORMIO_PATH_LINE='export PATH="$HOME/.platformio/penv/bin:$PATH"'""",
            shared)

    def test_the_path_line_is_not_stacked_on_every_run(self):
        self.assertIn('grep -qF "$PLATFORMIO_PATH_MARK" "$profile"', self.text)

    def test_the_two_scripts_spell_that_line_the_same_way(self):
        """Written by one and deleted by the other, both by exact match.

        One spelling in the write step and another spelling in the search step
        leaves the line on disk. This line also names the installer that wrote
        it, so it stays in the .bashrc of the user and names a project that is
        no longer on the machine.
        """
        with open(USER_UNIT) as handle:
            shared = handle.read()
        for name in ("PLATFORMIO_PATH_NOTE=", "PLATFORMIO_PATH_LINE=",
                     "PLATFORMIO_PATH_MARK="):
            self.assertIn(name, shared)
        with open(UNINSTALLER) as handle:
            text = handle.read()
        self.assertIn('grep -vxF "$2" "$profile" | grep -vxF "$3"', text)
        # Neither carries a copy of the line itself to drift from. Where pio
        # lives is a different question and stays where it is asked.
        for path in (INSTALLER, UNINSTALLER):
            with open(path) as handle:
                self.assertNotIn('export PATH="$HOME/.platformio',
                                 handle.read(), path)

    # Paths the installer names but does not create, so the uninstaller has
    # nothing to take back. Listed rather than guessed at, so that a new one
    # is a decision somebody makes here rather than a file left behind.
    NOT_INSTALLED = {
        "SHIM_DEVICE",      # the kernel makes it when the module loads
        "SOURCE_DIR",       # the clone, which is the user's
    }

    def test_every_path_the_installer_writes_is_one_the_uninstaller_knows(self):
        """Which is the whole question: does it all come back off again.

        This uses the constants and not the text, because both scripts already
        use the same constants. A path in one script, and not in the other, is a
        file that stays on the machine and that no user finds. The menu entry
        stayed in the launcher of a user in that way, for a project that was no
        longer on the machine.
        """
        with open(UNINSTALLER) as handle:
            uninstaller = handle.read()
        with open(USER_UNIT) as handle:
            shared = handle.read()

        named = set(re.findall(r"^([A-Z][A-Z0-9_]*)=[\"']?/", self.text, re.M))
        named |= set(re.findall(r"^([A-Z][A-Z0-9_]*)=[\"']?\.?[a-z]", shared,
                                re.M))
        missing = sorted(
            name for name in named - self.NOT_INSTALLED
            if name not in uninstaller and name not in shared)
        self.assertEqual(missing, [], "the uninstaller never mentions these")

    def test_the_cpu_applier_is_installed_and_removed(self):
        """The second program this project installs, and its unit.

        The path check above reads the constants only. The installer copies the
        applier by its own name, beside the service. So no check reads it. An
        installed program that no script removes is the file that the check must
        find.
        """
        with open(UNINSTALLER) as handle:
            uninstaller = handle.read()
        self.assertIn("server/steamos-utility-center-power", self.text)
        self.assertIn("steamos-utility-center-power.service", self.text)
        # The uninstaller removes the complete INSTALL_DIR, and this file goes
        # with it. So the list must name the unit outside that directory.
        self.assertIn("POWER_UNIT_PATH", uninstaller)
        self.assertIn('systemctl disable "$NAME-power.service"',
                      uninstaller)

    def test_the_cpu_unit_is_installed_but_not_enabled(self):
        """Installed so it is there; enabled only once something is set.

        With nothing in its config the unit would run at every boot to do
        nothing, and a service somebody did not ask for is one they have to
        wonder about. scripts/apply-power.sh enables it the first time a
        setting is applied.
        """
        self.assertNotIn("systemctl enable steamos-utility-center-power", self.text)
        with open(os.path.join(HERE, "..", "scripts",
                               "apply-power.sh")) as handle:
            self.assertIn('systemctl enable "$SERVICE"', handle.read())

    def test_the_things_that_live_in_a_home_are_taken_back(self):
        # These are under no root path, so no rm -f line reaches them. See
        # UninstallHomeTest for their behaviour.
        with open(UNINSTALLER) as handle:
            text = handle.read()
        for call in ("remove_menu_entry", "remove_platformio_path"):
            self.assertIn("\n%s\n" % call, text,
                          "%s is defined but never called" % call)

    def test_what_it_leaves_behind_it_says_so(self):
        """A thing left on purpose and a thing forgotten look identical.

        So the script reports the files of the user at the end, and it does not
        leave them for the user to find. Those files are the configuration, the
        module, the clone, the lingering switch and PlatformIO.
        """
        with open(UNINSTALLER) as handle:
            text = handle.read()
        self.assertIn('echo "Left in place:"', text)
        for subject in ("$CONFIG_PATH", "--remove-module", "$SOURCE_DIR",
                        "disable-linger", ".platformio"):
            self.assertIn(subject, text, subject)

    def test_a_partial_upgrade_is_used_on_purpose(self):
        # -Syu would pull a newer kernel than the one now running, and the
        # headers would then match nothing.
        self.assertNotIn("pacman -Syu", self.text)


class RetiredUserFilesTest(unittest.TestCase):
    """What an older release put in the session and this one does not.

    Two things, both about HDMI CEC, both now fixed inside the CEC module
    instead:

        steamos-utility-center-cec.service    put the adapter on the bus
        steamos-cec-boot-wake.service.d/      Type=simple over the toolkit's
          10-steamos-utility-center.conf      own boot wake

    Neither is inert if it is left. The unit is enabled, so systemd goes on
    starting it at every login with the program it names already gone; the
    drop-in goes on changing somebody else's service after this project has
    stopped writing it. So both the installer, upgrading, and the uninstaller,
    leaving, have to take them away.
    """

    def setUp(self):
        with open(INSTALLER) as handle:
            self.installer = handle.read()
        with open(os.path.join(HERE, "..", "uninstall.sh")) as handle:
            self.uninstaller = handle.read()
        with open(os.path.join(HERE, "..", "scripts", "user-unit.sh")) as one:
            self.shared = one.read()

    def test_they_are_named_once_where_both_scripts_can_see_them(self):
        for name in ('RETIRED_USER_UNITS=("$NAME-cec.service")',
                     'RETIRED_USER_DROPINS='):
            self.assertIn(name, self.shared)

    def test_the_removal_lives_with_the_names(self):
        self.assertIn("remove_retired_user_files()", self.shared)

    def test_the_unit_is_stopped_before_its_file_goes(self):
        """A running unit whose file is deleted goes on running.

        Checked by reading rather than by running: the shell harness gives
        these functions no session bus, so user_systemctl returns before it
        reaches systemctl and there is no call to record.
        """
        body = self.shared[self.shared.index("remove_retired_user_files()"):]
        body = body[:body.index("\n}\n")]
        self.assertLess(body.index('user_systemctl stop "$unit"'),
                        body.index('rm -f "$WATCHER_DIR/$unit"'))

    def test_both_scripts_call_it(self):
        # The installer as part of its migration, the uninstaller through
        # remove_user_units, which takes the current units away.
        self.assertIn("remove_retired_user_files", self.installer + self.shared)
        self.assertIn("remove_retired_user_files",
                      self.shared[self.shared.index("remove_user_units() {"):])
        self.assertIn("remove_user_units", self.uninstaller)

    def test_it_does_not_take_a_directory_that_is_not_empty(self):
        """The drop-in directory belongs to somebody else's unit.

        That unit can have its own drop-in files there, so this removes the
        directory only when it is empty. rmdir does exactly that, and for that
        reason the script does not report its failure.
        """
        self.assertIn('rmdir "$WATCHER_DIR/$(dirname "$dropin")" '
                      '2>/dev/null || true', self.shared)

    def test_nothing_installs_them_any_more(self):
        for gone in ("install_cec_wake_dropin", "CEC_WAKE_SOURCE",
                     "$NAME-cec.service"):
            self.assertNotIn(gone, self.installer, gone)

    def test_the_unit_and_the_drop_in_are_gone_from_the_repository(self):
        for gone in ("steamos-utility-center-cec.service",
                     "steamos-cec-boot-wake-override.conf"):
            self.assertFalse(
                os.path.exists(os.path.join(HERE, "..", "server", gone)),
                "%s is still here - it is the CEC module's job now" % gone)


class InstalledStampTest(unittest.TestCase):
    """The installer records what it installed, and update.sh says it must run.

    These are two halves of one fault. A pull changes the clone, and an
    install changes the running code. The window showed the first half only,
    and update.sh stopped with no message about the second half. A machine with
    a new correction in its clone therefore ran the old copy, and each user
    read its log and asked why the correction had no result.
    """

    def setUp(self):
        with open(INSTALLER) as handle:
            self.installer = handle.read()
        with open(os.path.join(HERE, "..", "scripts", "user-unit.sh")) as one:
            self.shared = one.read()
        with open(os.path.join(HERE, "..", "scripts", "update.sh")) as two:
            self.updater = two.read()

    def test_the_path_is_named_where_both_scripts_can_see_it(self):
        """Inside INSTALL_DIR, which the uninstaller's rm -rf covers."""
        self.assertIn('STAMP_PATH="$INSTALL_DIR/installed-from"', self.shared)

    def test_the_installer_writes_the_commit_it_installed_from(self):
        self.assertIn("$STAMP_PATH", self.installer)
        self.assertIn("rev-parse HEAD", self.installer)

    def test_it_reads_the_clone_as_a_directory_git_will_talk_about(self):
        """This runs as root over somebody else's clone.

        Without safe.directory, git gives no answer. Each install then writes no
        record, and no message reports that. The panel reads "not recorded" for
        each install, and the comparison has no value.
        """
        self.assertIn("safe.directory=$SOURCE_DIR", self.installer)

    def test_a_clone_it_cannot_read_leaves_no_stamp_not_a_stale_one(self):
        """Worse than no answer is last install's answer, kept.

        The old file would then claim the running files came from a commit
        they did not, which is the one output nobody could catch.
        """
        self.assertIn('rm -f "$STAMP_PATH"', self.installer)

    def test_the_stamp_is_written_after_the_files_it_describes(self):
        stamped = self.installer.index("$STAMP_PATH")
        binary = 'install -m 0755 "$SOURCE_DIR/server/steamos-utility-center"'
        for step in ('cp -r "$SOURCE_DIR/server/steamos_utility_center"',
                     binary):
            self.assertLess(self.installer.index(step), stamped,
                            "%s runs after the stamp that vouches for it"
                            % step)

    def test_the_updater_says_the_installer_still_has_to_run(self):
        """It changes the clone and nothing else, and used to end there."""
        self.assertIn("install.sh", self.updater.split("Updated $BRANCH")[-1])


class PanelIconTest(unittest.TestCase):
    """The name the installer files the icon under, and the one the
    uninstaller globs for.

    The change of the name gave this fault: the installer wrote
    steamos-utility-center-panel.png, and the glob searched for
    steamos-utility-center.png. The uninstaller therefore never removed the
    icon. Both now read PANEL_ICON from scripts/user-unit.sh, and this test
    proves that. Two names that a person must keep equal become different.
    """

    def test_the_installer_files_it_where_the_uninstaller_looks(self):
        icon = shell_value("PANEL_ICON")
        glob = shell_value("PANEL_ICON_GLOB")
        self.assertTrue(glob.endswith("/%s.png" % icon), glob)
        with open(INSTALLER) as handle:
            installer = handle.read()
        self.assertIn('"$icon_dir/$PANEL_ICON.png"', installer,
                      "the installer names the icon itself again")

    def test_the_migration_moves_the_panels_settings_where_it_reads_them(self):
        """Two files in two languages hold one name, and no test compared them.

        The migration moves ~/.config/steamos-led-panel.conf to a name in the
        shell file. The panel reads a name in the Python file. The two names were
        different for a short time. The symptom is a theme back at dark after an
        update, and the file one name away.
        """
        sys.path.insert(0, os.path.join(HERE, "..", "gui"))
        import appsettings
        self.assertEqual(shell_value("PANEL_CONFIG"), appsettings.CONFIG_FILE)
        with open(USER_UNIT) as handle:
            shared = handle.read()
        # And the file it moves is the one the panel falls back to reading.
        self.assertIn(appsettings.OLD_CONFIG_FILE, shared)

    def test_the_entry_and_the_icon_are_both_shipped(self):
        # The template the installer substitutes, and the picture it copies.
        # Renamed together with everything else, and a missing one is a menu
        # entry with a stock icon or none at all.
        here = os.path.join(HERE, "..", "gui")
        for name in ("steamos-utility-center-panel.desktop",
                     "steamos-utility-center-panel.png",
                     "steamos-utility-center-panel"):
            self.assertTrue(os.path.exists(os.path.join(here, name)), name)


class UninstallDefaultsTest(unittest.TestCase):
    """Uninstall means uninstall.

    The script kept the settings and the kernel module before, and a user
    needed two flags for a complete removal. The normal case is a user who wants
    this project off the machine, and that case needed two flags that no user
    knew. The run also ended with a list of the files that it kept. The flags
    are still there, and they now keep those files.

    Run with --help after the flag under test: the arguments are read before
    anything is done, so this exercises the parsing without uninstalling
    anything.
    """

    def _help(self, *flags):
        return subprocess.run(["bash", UNINSTALLER] + list(flags) + ["--help"],
                              capture_output=True, text=True)

    def test_the_usage_names_the_two_that_keep(self):
        done = self._help()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("--keep-conf", done.stdout)
        self.assertIn("--keep-module", done.stdout)

    def test_both_keeping_flags_are_taken(self):
        for flag in ("--keep-conf", "--keep-module"):
            done = self._help(flag)
            self.assertEqual(done.returncode, 0, "%s: %s" % (flag, done.stderr))

    def test_the_old_flags_are_still_taken(self):
        # They asked for what happens anyway now, and they are in the README
        # of every clone made before this changed.
        for flag in ("--purge", "--remove-module"):
            done = self._help(flag)
            self.assertEqual(done.returncode, 0, "%s: %s" % (flag, done.stderr))

    def test_something_else_is_still_refused(self):
        done = self._help("--delete-everything-twice")
        self.assertEqual(done.returncode, 1)
        self.assertIn("unknown option", done.stderr)

    def test_removing_is_what_happens_without_a_flag(self):
        """The inversion itself, read off the script.

        Both switches come from the keep flag, so the body of the script still
        asks about the removal. Neither switch can keep its default and keep a
        file.
        """
        with open(UNINSTALLER) as handle:
            text = handle.read()
        self.assertIn("PURGE=$(( 1 - KEEP_CONF ))", text)
        self.assertIn("REMOVE_MODULE=$(( 1 - KEEP_MODULE ))", text)
        self.assertIn("KEEP_CONF=0", text)
        self.assertIn("KEEP_MODULE=0", text)


class StaleShimTest(unittest.TestCase):
    """The shim copies a kernel update leaves behind.

    The module is installed into the running kernel's own updates/ directory.
    A SteamOS update brings a new kernel and leaves the old one's modules
    exactly where they were, so the copy built for the kernel before last sits
    there for ever: nothing loads it, and an uninstaller that only ever looked
    at `uname -r` never saw it.
    """

    KERNELS = ("6.11.11-valve1-1-neptune-611",
               "6.16.4-valve2-1-neptune-616")

    def _root(self, kernels=None):
        room = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, room, True)
        root = os.path.join(room, "root")
        for release in (self.KERNELS if kernels is None else kernels):
            where = os.path.join(root, "usr/lib/modules", release, "updates")
            os.makedirs(where)
            with open(os.path.join(where, "leds-valve-shim.ko"), "w") as handle:
                handle.write("ELF")
        return room, root

    def _run(self, call, root, room):
        stubs = os.path.join(room, "stubs")
        os.makedirs(stubs, exist_ok=True)
        log = os.path.join(room, "calls")
        with open(os.path.join(stubs, "depmod"), "w") as handle:
            handle.write('#!/usr/bin/env bash\necho "depmod $*" >> "%s"\n' % log)
        os.chmod(os.path.join(stubs, "depmod"), 0o755)
        done = subprocess.run(
            ["bash", "-c", 'set -euo pipefail\nsource "%s"\n%s\n'
             % (USER_UNIT, call)],
            capture_output=True, text=True,
            env={"PATH": stubs + ":" + os.environ.get("PATH", ""),
                 "ROOT": root})
        said = ""
        if os.path.exists(log):
            with open(log) as handle:
                said = handle.read()
        return done, said

    def _left(self, root):
        return sorted(
            path.split(os.sep)[-3]
            for path in glob.glob(os.path.join(
                root, "usr/lib/modules/*/updates/leds-valve-shim.ko")))

    def test_every_copy_is_found(self):
        room, root = self._root()
        done, _said = self._run("shim_copies", root, room)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(len(done.stdout.split()), 2, done.stdout)

    def test_the_running_kernel_keeps_its_own(self):
        """The one case that must not go wrong.

        The installer calls this after a build, with the kernel of that build.
        The new copy must stay, and each other copy must go.
        """
        room, root = self._root()
        done, _said = self._run('remove_stale_shims "%s"' % self.KERNELS[1],
                                root, room)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self._left(root), [self.KERNELS[1]])

    def test_naming_no_kernel_takes_all_of_them(self):
        # That is the uninstaller, and it keeps nothing.
        room, root = self._root()
        done, _said = self._run("remove_stale_shims", root, room)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self._left(root), [])

    def test_each_kernel_it_touched_is_told(self):
        # Without a depmod the kernel goes on listing a module that is not
        # there, and modprobe says so at the next boot.
        room, root = self._root()
        _done, said = self._run("remove_stale_shims", root, room)
        for release in self.KERNELS:
            self.assertIn("depmod %s" % release, said)

    def test_a_machine_with_none_is_not_an_error(self):
        room, root = self._root(kernels=())
        os.makedirs(os.path.join(root, "usr/lib/modules"), exist_ok=True)
        done, _said = self._run("remove_stale_shims", root, room)
        self.assertEqual(done.returncode, 0, done.stderr)


class MigrationTest(unittest.TestCase):
    """The install that is already on the machine under its old name.

    This project changed its name from "SteamOS LED bar" to the SteamOS
    Utility Centre, and each unit, configuration file and command changed with
    it. A change of the names in the tree is the simple half. The second half
    breaks the machine of a user: an install under the new names removes no file
    under the old names.

    An update without this step leaves the old steamos-led-serial.service
    enabled and running beside the new unit. Two processes then hold one serial
    port. It also leaves the settings in the old configuration file, and no
    program reads that file. Each setting therefore returns to its default with
    no message.

    These tests use a directory of their own and not /etc. ROOT in
    scripts/user-unit.sh exists for that.
    """

    OLD_CONFIG = ("LED_COUNT=17\nSERIAL_PORT=/dev/steamos-led-esp\n"
                  "NOTIFY_FIFO=/run/steamos-led-serial/notify\n")

    def _root(self, old=True, home=True):
        """A machine with an old install on it, or a clean one."""
        room = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, room, True)
        root = os.path.join(room, "root")
        where = os.path.join(room, "home")

        for directory in ("etc/systemd/system", "etc/udev/rules.d",
                          "etc/modules-load.d", "usr/local/bin",
                          "usr/lib/systemd/system-sleep", "var/lib"):
            os.makedirs(os.path.join(root, directory))
        for directory in (".config/systemd/user/default.target.wants",
                          ".local/share/applications",
                          ".local/share/icons/hicolor/512x512/apps"):
            os.makedirs(os.path.join(where, directory))

        if old:
            self._put(root, "etc/steamos-led-serial.conf", self.OLD_CONFIG)
            self._put(root, "etc/steamos-led-power.conf", "CPU_GOVERNOR=powersave\n")
            self._put(root, "etc/systemd/system/steamos-led-serial.service", "[Unit]\n")
            self._put(root, "etc/systemd/system/steamos-led-power.service", "[Unit]\n")
            self._put(root, "etc/udev/rules.d/99-steamos-led-serial.rules", "#\n")
            self._put(root, "usr/lib/systemd/system-sleep/steamos-led-serial", "#\n")
            self._put(root, "usr/local/bin/steamos-led-serial", "#\n")
            self._put(root, "usr/local/bin/steamos-led-power", "#\n")
            self._put(root, "etc/modules-load.d/steamos-led-bar.conf", "leds-valve-shim\n")
            os.makedirs(os.path.join(root, "var/lib/steamos-led-serial/steamos_utility_center"))
            self._put(root, "var/lib/steamos-led-serial/steamos-led-serial", "#\n")
            if home:
                for unit in ("steamos-led-achievements.service",
                             "steamos-led-phone.service"):
                    self._put(where, ".config/systemd/user/" + unit, "[Unit]\n")
                    self._put(where, ".config/systemd/user/default.target.wants/"
                              + unit, "[Unit]\n")
                self._put(where, ".local/share/applications/"
                          "steamos-led-panel.desktop", "[Desktop Entry]\n")
                self._put(where, ".local/share/icons/hicolor/512x512/apps/"
                          "steamos-led-panel.png", "PNG\n")
                self._put(where, ".config/steamos-led-panel.conf", "THEME=light\n")
        return room, root, where

    def _put(self, base, relative, text):
        path = os.path.join(base, relative)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as handle:
            handle.write(text)
        return path

    def _run(self, call, root, home, room):
        """Source user-unit.sh against that root and make the call."""
        stubs = os.path.join(room, "stubs")
        os.makedirs(stubs, exist_ok=True)
        log = os.path.join(room, "calls")
        # Every command the migration reaches for that would touch the real
        # machine. Recorded rather than swallowed: whether the old service was
        # stopped before its file went is half of what this has to check, and
        # a stub that only returned success could not say.
        for name in ("systemctl", "udevadm", "loginctl",
                     "update-desktop-database", "kbuildsycoca6", "chown"):
            self._stub(stubs, name,
                       'echo "%s $*" >> "%s"\nexit 0\n' % (name, log))
        self._stub(stubs, "id", 'case "$1" in\n'
                                '  -nu) [[ "$2" == "1000" ]] && echo deck ;;\n'
                                '  -u)  echo 1000 ;;\n'
                                'esac\n')
        self._stub(stubs, "getent",
                   'echo "deck:x:1000:1000::%s:/bin/bash"\n' % home)
        self._stub(stubs, "runuser",
                   'while [[ $# -gt 0 ]]; do\n'
                   '  case "$1" in\n'
                   '    -u) shift 2 ;;\n'
                   '    --) shift; break ;;\n'
                   '    *) break ;;\n'
                   '  esac\n'
                   'done\n'
                   'exec "$@"\n')

        done = subprocess.run(
            ["bash", "-c", 'set -euo pipefail\nsource "%s"\n%s\n'
             % (USER_UNIT, call)],
            capture_output=True, text=True,
            env={"PATH": stubs + ":" + os.environ.get("PATH", ""),
                 "ROOT": root, "PKEXEC_UID": "1000", "HOME": home,
                 # No live session, so user_systemctl returns early rather
                 # than reaching for a bus that is not there.
                 "XDG_RUNTIME_DIR": "/nonexistent"})
        said = ""
        if os.path.exists(log):
            with open(log) as handle:
                said = handle.read()
        return done, said

    def _stub(self, directory, name, body):
        path = os.path.join(directory, name)
        with open(path, "w") as handle:
            handle.write("#!/usr/bin/env bash\n" + body)
        os.chmod(path, 0o755)

    def _migrate(self, **kwargs):
        room, root, home = self._root(**kwargs)
        done, calls = self._run("migrate_old_install", root, home, room)
        self.assertEqual(done.returncode, 0, done.stderr)
        return root, home, done, calls

    def _retire(self, put=True, home=True):
        """The CEC unit and drop-in an older release left in the session."""
        room, root, where = self._root(old=False, home=home)
        unit = ".config/systemd/user/steamos-utility-center-cec.service"
        link = (".config/systemd/user/default.target.wants"
                "/steamos-utility-center-cec.service")
        dropin = (".config/systemd/user/steamos-cec-boot-wake.service.d"
                  "/10-steamos-utility-center.conf")
        if put:
            for each in (unit, link, dropin):
                self._put(where, each, "[Service]\n")
        done, calls = self._run("remove_retired_user_files", root, where, room)
        return where, done, calls, (unit, link, dropin)

    def test_the_retired_cec_unit_and_its_symlink_are_taken_away(self):
        """A unit file removed on its own leaves the enable symlink behind.

        systemd then goes on trying to start something that is not there, at
        every login, for as long as the account lasts.
        """
        where, done, _calls, (unit, link, dropin) = self._retire()
        self.assertEqual(done.returncode, 0, done.stderr)
        for gone in (unit, link, dropin):
            self.assertFalse(os.path.exists(os.path.join(where, gone)), gone)

    def test_the_drop_in_directory_goes_only_when_it_is_empty(self):
        """It belongs to another unit, and that unit can have drop-in files."""
        room, root, where = self._root(old=False)
        directory = ".config/systemd/user/steamos-cec-boot-wake.service.d"
        self._put(where, directory + "/10-steamos-utility-center.conf", "x\n")
        self._put(where, directory + "/50-theirs.conf", "x\n")
        done, _calls = self._run("remove_retired_user_files", root, where, room)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(os.path.exists(os.path.join(where, directory,
                                                    "50-theirs.conf")))
        self.assertTrue(os.path.isdir(os.path.join(where, directory)))

    def test_a_session_that_never_had_them_reports_nothing_removed(self):
        """Both callers say so out loud, so "nothing to do" must not read as
        "removed them"."""
        _where, done, _calls, _paths = self._retire(put=False)
        self.assertEqual(done.returncode, 1)

    def test_the_old_service_is_stopped_before_its_file_is_taken_away(self):
        """The one ordering that matters on a running machine.

        A delete of a unit file alone leaves the service running, because
        systemd already loaded it. That service keeps the serial port. The new
        service then starts, finds the port in use, and reports a bar with no
        connection. A delete also leaves the enable symlink, so systemd tries
        to start a unit that is gone.
        """
        root, _home, _done, calls = self._migrate()
        self.assertIn("systemctl stop steamos-led-serial.service", calls)
        self.assertIn("systemctl disable steamos-led-serial.service", calls)
        self.assertIn("systemctl stop steamos-led-power.service", calls)
        for unit in ("steamos-led-serial.service", "steamos-led-power.service"):
            self.assertFalse(
                os.path.exists(os.path.join(root, "etc/systemd/system", unit)),
                unit)
        stopped = calls.index("systemctl stop steamos-led-serial.service")
        reloaded = calls.index("systemctl daemon-reload")
        self.assertLess(stopped, reloaded, calls)

    def test_the_settings_are_carried_across_rather_than_lost(self):
        """The quiet half, and the one nobody would notice until later.

        Without this step, the new install writes a configuration file with the
        defaults. The old file stays beside it, and no program reads it. Each
        setting of the user therefore returns to its default: the LED count, the
        serial port, the effects, and each other value. No step fails, and the
        strip shows a different effect.
        """
        root, _home, _done, _calls = self._migrate()
        new = os.path.join(root, "etc/steamos-utility-center.conf")
        self.assertTrue(os.path.exists(new), "the settings were not carried")
        with open(new) as handle:
            said = handle.read()
        self.assertIn("LED_COUNT=17", said)
        # The board of the connection, and its name does not change. See the
        # note in user-unit.sh. A migration that changes this value removes the
        # connection to the bar.
        self.assertIn("SERIAL_PORT=/dev/steamos-led-esp", said)
        self.assertFalse(
            os.path.exists(os.path.join(root, "etc/steamos-led-serial.conf")))
        power = os.path.join(root, "etc/steamos-utility-center-power.conf")
        self.assertTrue(os.path.exists(power))

    def test_the_notification_pipe_is_pointed_at_the_new_directory(self):
        """NOTIFY_FIFO is a live setting naming a directory the unit creates.

        RuntimeDirectory= in the unit makes /run/<name>, so a new unit name gives
        a new directory. A configuration file with the old value points the pipe
        at /run/steamos-led-serial, and no program makes that directory now. The
        notifications then stop, and no log gives the reason.
        """
        root, _home, _done, _calls = self._migrate()
        with open(os.path.join(root, "etc/steamos-utility-center.conf")) as f:
            said = f.read()
        self.assertIn("NOTIFY_FIFO=/run/steamos-utility-center/notify", said)
        self.assertNotIn("/run/steamos-led-serial", said)

    def test_a_pipe_somebody_moved_themselves_is_left_where_they_put_it(self):
        # Only the old default is rewritten. Anything else was somebody's
        # decision, and repairing a setting is not the same as overruling one.
        room, root, home = self._root()
        self._put(root, "etc/steamos-led-serial.conf",
                  "NOTIFY_FIFO=/tmp/mine\n")
        done, _calls = self._run("migrate_old_install", root, home, room)
        self.assertEqual(done.returncode, 0, done.stderr)
        with open(os.path.join(root, "etc/steamos-utility-center.conf")) as f:
            self.assertIn("NOTIFY_FIFO=/tmp/mine", f.read())

    def test_everything_else_of_the_old_install_goes(self):
        root, _home, _done, _calls = self._migrate()
        for left in ("etc/udev/rules.d/99-steamos-led-serial.rules",
                     "usr/lib/systemd/system-sleep/steamos-led-serial",
                     "usr/local/bin/steamos-led-serial",
                     "usr/local/bin/steamos-led-power",
                     "var/lib/steamos-led-serial"):
            self.assertFalse(os.path.exists(os.path.join(root, left)), left)

    def test_the_file_that_loads_the_kernel_module_is_left_alone(self):
        """/etc/modules-load.d/steamos-led-bar.conf is not ours to take.

        The name looks like an old name of this project. But the leds-valve-shim
        installer in this repository writes it under that name today. See
        leds-valve-shim/PROVENANCE.md, which gives the reason this project keeps
        that script unchanged. A removal of the file with the other steamos-led-*
        names stops the module at the next boot. The strip then goes dark one
        restart after an update, and no message connects the two.
        """
        root, _home, _done, _calls = self._migrate()
        self.assertTrue(
            os.path.exists(os.path.join(
                root, "etc/modules-load.d/steamos-led-bar.conf")),
            "the module would no longer load at boot")

    def test_the_user_half_goes_too(self):
        """The units, the menu entry and the icon in somebody's home.

        Not under any root path, so none of the removals above reach them. A
        stale menu entry is the visible one: a second Utility Centre in the
        launcher that starts a program which is no longer there.
        """
        _root, home, _done, _calls = self._migrate()
        for left in (".config/systemd/user/steamos-led-achievements.service",
                     ".config/systemd/user/default.target.wants/"
                     "steamos-led-phone.service",
                     ".local/share/applications/steamos-led-panel.desktop",
                     ".local/share/icons/hicolor/512x512/apps/"
                     "steamos-led-panel.png"):
            self.assertFalse(os.path.exists(os.path.join(home, left)), left)

    def test_the_panels_own_settings_come_across_as_well(self):
        _root, home, _done, _calls = self._migrate()
        moved = os.path.join(home, ".config/steamos-utility-center-panel.conf")
        self.assertTrue(os.path.exists(moved))
        with open(moved) as handle:
            self.assertIn("THEME=light", handle.read())

    def test_a_machine_that_never_had_the_old_one_is_not_touched(self):
        """The common case by far, and it has to be silent.

        Every machine installing this from now on takes this path, and a
        migration that announced itself on a machine with nothing to migrate
        would be a line in every install saying something happened that did
        not.
        """
        room, root, home = self._root(old=False)
        before = sorted(os.walk(root))
        done, calls = self._run("migrate_old_install", root, home, room)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, "", done.stdout)
        self.assertEqual(calls, "", calls)
        self.assertEqual(sorted(os.walk(root)), before)

    def test_running_it_twice_is_the_same_as_running_it_once(self):
        """An install that fails halfway is one somebody runs again.

        The second run finds a machine with a partial migration, and it must
        not reverse the first run. In particular it must not move the
        configuration file again, and it must not write an old copy over the
        new file.
        """
        room, root, home = self._root()
        first, _calls = self._run("migrate_old_install", root, home, room)
        self.assertEqual(first.returncode, 0, first.stderr)
        after = sorted(os.walk(root))
        second, _calls = self._run("migrate_old_install", root, home, room)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(sorted(os.walk(root)), after)
        with open(os.path.join(root, "etc/steamos-utility-center.conf")) as f:
            self.assertIn("LED_COUNT=17", f.read())

    def test_a_new_config_beside_an_old_one_wins(self):
        """Two files come from a machine with a manual migration.

        The panel reads the new file, so the new file wins. The script keeps the
        old file and does not delete it. That file still holds the settings of a
        user, and this script must not remove them.
        """
        room, root, home = self._root()
        self._put(root, "etc/steamos-utility-center.conf", "LED_COUNT=99\n")
        done, _calls = self._run("migrate_old_install", root, home, room)
        self.assertEqual(done.returncode, 0, done.stderr)
        with open(os.path.join(root, "etc/steamos-utility-center.conf")) as f:
            self.assertIn("LED_COUNT=99", f.read())
        self.assertTrue(
            os.path.exists(os.path.join(root, "etc/steamos-led-serial.conf")),
            "somebody's old settings were thrown away")

    def test_the_uninstaller_keeps_nothing_and_purges_on_request(self):
        """Its half of the same list: remove, do not carry across.

        A user can run the uninstaller with no earlier run of the new
        installer. That user pulls the new version and then removes the
        project, and each name on the machine is an old name. So the
        uninstaller also reads this list. Without it, the uninstaller is the one
        script with no knowledge of the old names.
        """
        room, root, home = self._root()
        done, calls = self._run("remove_old_install 0", root, home, room)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("systemctl disable --now steamos-led-serial.service",
                      calls)
        self.assertFalse(os.path.exists(
            os.path.join(root, "var/lib/steamos-led-serial")))
        # Without --purge the settings stay, exactly as they do for the
        # current names.
        self.assertTrue(os.path.exists(
            os.path.join(root, "etc/steamos-led-serial.conf")))

        room, root, home = self._root()
        done, _calls = self._run("remove_old_install 1", root, home, room)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertFalse(os.path.exists(
            os.path.join(root, "etc/steamos-led-serial.conf")))

    def test_the_installer_migrates_before_it_writes_anything(self):
        """Order again, this time in the installer rather than in a function.

        Called after the first file is written, the migration would delete the
        install directory it had just filled, and move a config over the fresh
        one. Checked by reading the script, because running it installs.
        """
        with open(INSTALLER) as handle:
            text = handle.read()
        # The call, on a line of its own. This does not match the name, because
        # the comment above the call also holds it. A match on the name found
        # that comment. The comment is above the first install step for each
        # state of the call, so a delete of the call passed this test.
        called = re.search(r"^migrate_old_install$", text, re.M)
        self.assertIsNotNone(called, "the installer never migrates")
        self.assertLess(called.start(),
                        text.index('install -d -m 0755 "$INSTALL_DIR"'),
                        "the migration runs after the install has begun")


if __name__ == "__main__":
    unittest.main()
