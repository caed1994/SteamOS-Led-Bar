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

import glob
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
    """One shell function lifted out, by source, wherever it lives.

    Sourcing the whole installer is not an option: it installs things. Lifting
    the function keeps the test honest - it runs the same text that ships. Both
    files are searched because a function shared with the uninstaller moves
    into scripts/user-unit.sh, and a test that only knew one of the two would
    fail on the move rather than on the behaviour.
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
SLEEP_HOOK = os.path.join(HERE, "..", "systemd-sleep", "steamos-utility-center")


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


class UninstallHomeTest(unittest.TestCase):
    """What the installer wrote into the desktop user's home, taken back.

    None of it is under a root path, so none of the uninstaller's rm -f lines
    reached it: the menu entry stayed in the launcher pointing at a project
    that was no longer installed, its icon stayed in the icon theme, and the
    PATH line the installer had appended stayed in .bashrc under a comment
    naming an installer that was gone.
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
        # It is filed under the width read out of the PNG, so an older install
        # may have put one somewhere this one would never look.
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
        """Under either comment - the one written now, and the one before it.

        These two lines are deleted from somebody's .bashrc by exact match,
        which is what makes it safe to touch a file that is theirs. It also
        means the wording is an interface: the rename changed the comment, and
        without keeping the old spelling every .bashrc an older install wrote
        would carry a line naming an installer that no longer exists, with
        nothing left that would take it back out.
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

        Somebody who put PlatformIO on their own PATH before ever meeting this
        project should still have it afterwards.
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

        Both are held to the constants in the shared file, but a test that
        only checked that would pass on two functions that agreed about a
        string and disagreed about anything else - a trailing space, a
        different comment. So the writer runs, then the remover, and what is
        left has to be exactly what was there before.
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
        self.assertIn('DEFAULT_FIFO="/run/steamos-utility-center/notify"', hook)
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
        # This project's own commands only - the page is full of git, pacman
        # and systemctl, and none of those are ours to install. An allow-list
        # of other people's tools would need extending every time one is
        # mentioned, which is a test that eventually gets edited rather than
        # read.
        typed = set(re.findall(r"^\s*(?:sudo )?(steamos-utility-center[a-z-]*)\b",
                               readme, re.M))
        self.assertIn(name, typed, "the README names no such command")
        # Every command this project puts on the PATH, by constant. A second
        # program was added and the README told people to run it before it was
        # linked anywhere - which is exactly the fault this test is about,
        # caught the second time round rather than the first.
        linked = {os.path.basename(shell_value(each))
                  for each in ("COMMAND_LINK", "POWER_COMMAND_LINK")}
        self.assertEqual(typed - linked - {"steamos-utility-center.conf"}, set(),
                         "the README names a command nothing installs")

    def test_the_user_units_are_installed_before_anything_that_can_fail(self):
        """Order, not presence - and this one was reported, not imagined.

        They used to be installed at the very end, after pacman, the kernel
        module and the firmware flash. Under set -e any of those ends the run,
        and then the units are simply not there: "Unit steamos-utility-center-phone.
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
        self.assertLess(self.text.index("systemctl restart steamos-utility-center"),
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

    def test_the_uninstaller_takes_the_cec_toolkit_with_it(self):
        """Installed from vendor/ by a button on the page, so it is ours.

        Its units, helpers, udev rule and sudoers file were all put on the
        machine by this project and none of them were touched here before -
        an uninstall left the whole toolkit running.
        """
        with open(UNINSTALLER) as handle:
            text = handle.read()
        self.assertIn("remove_cec_toolkit", text)
        self.assertIn("install-cec.sh", text)

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
                           ("\n    remove_stale_shims", "the kernel module")):
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
        with open(USER_UNIT) as handle:
            shared = handle.read()
        self.assertIn(
            """PLATFORMIO_PATH_LINE='export PATH="$HOME/.platformio/penv/bin:$PATH"'""",
            shared)

    def test_the_path_line_is_not_stacked_on_every_run(self):
        self.assertIn('grep -qF "$PLATFORMIO_PATH_MARK" "$profile"', self.text)

    def test_the_two_scripts_spell_that_line_the_same_way(self):
        """Written by one and deleted by the other, both by exact match.

        A line appended in one spelling and looked for in another is a line
        nobody removes - and this one names the installer that put it there,
        so it would sit in somebody's .bashrc pointing at a project that is
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

        By constant rather than by literal, because both scripts already name
        them the same way, and a path added to one and missed in the other is
        exactly the kind of leftover nobody notices - the menu entry sat in
        somebody's launcher that way, pointing at a project that was no longer
        installed.
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

        The path check above only sees things named by a constant, and the
        applier is copied by its literal name alongside the service - so it
        would go uncovered, and an installed program nobody removes is the
        leftover that check exists to prevent.
        """
        with open(UNINSTALLER) as handle:
            uninstaller = handle.read()
        self.assertIn("server/steamos-utility-center-power", self.text)
        self.assertIn("steamos-utility-center-power.service", self.text)
        # Removed with the rest of INSTALL_DIR, which the uninstaller wipes
        # whole - so what has to be named there is the unit outside it.
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
        # Under no root path, so none of the rm -f lines reach them - see
        # UninstallHomeTest for what these actually do.
        with open(UNINSTALLER) as handle:
            text = handle.read()
        for call in ("remove_menu_entry", "remove_platformio_path"):
            self.assertIn("\n%s\n" % call, text,
                          "%s is defined but never called" % call)

    def test_what_it_leaves_behind_it_says_so(self):
        """A thing left on purpose and a thing forgotten look identical.

        So the ones that are somebody else's - the config, the module, the
        clone, lingering, PlatformIO itself - are reported at the end rather
        than left to be found.
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


class PanelIconTest(unittest.TestCase):
    """The name the installer files the icon under, and the one the
    uninstaller globs for.

    Found during the rename: the installer wrote steamos-utility-center-panel.png
    and the glob looked for steamos-utility-center.png, so the icon would have
    been installed and never removed. Both now read PANEL_ICON out of
    scripts/user-unit.sh, and this is what says they still do - a pair of
    names that have to match by hand is a pair that drifts.
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
        """Two files, two languages, one name - and nothing enforced it.

        The migration moves ~/.config/steamos-led-panel.conf to a name it
        spells in shell; the panel reads a name it spells in Python. They were
        briefly different, and the whole of the symptom would have been
        somebody's theme back to dark after an update, with the file sitting
        one name away.
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

        Called after a build, with the kernel just built for - so the copy
        that was made a moment ago has to survive and every other one has to
        go.
        """
        room, root = self._root()
        done, _said = self._run('remove_stale_shims "%s"' % self.KERNELS[1],
                                root, room)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self._left(root), [self.KERNELS[1]])

    def test_naming_no_kernel_takes_all_of_them(self):
        # Which is the uninstaller: nothing is being kept.
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

    The project was renamed from "SteamOS LED bar" to the SteamOS Utility
    Centre, and with it every unit, config and command it installs. Renaming
    them in the tree is the easy half; the half that breaks somebody's machine
    is that nothing about installing under new names removes what is already
    there under the old ones. An update without this leaves the old
    steamos-led-serial.service enabled and running beside the new unit - two
    processes on one serial port - and leaves the settings in the old config
    file, which nothing reads any more, so every one of them silently goes
    back to its default.

    Run against a directory built here rather than against /etc, which is what
    ROOT in scripts/user-unit.sh is for.
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

    def test_the_old_service_is_stopped_before_its_file_is_taken_away(self):
        """The one ordering that matters on a running machine.

        A unit file that is merely deleted leaves the service running - it is
        already loaded - and it goes on holding the serial port. The new one
        then starts, finds the port busy, and reports a bar that is not
        plugged in. Removing the file also leaves the enable symlink behind,
        so systemd keeps trying to start something that is gone.
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

        Without this the new install writes a fresh config full of defaults,
        the old file sits unread beside it, and every setting somebody ever
        changed is back where it started - LED count, serial port, effects,
        the lot. Nothing fails; the strip just does something else.
        """
        root, _home, _done, _calls = self._migrate()
        new = os.path.join(root, "etc/steamos-utility-center.conf")
        self.assertTrue(os.path.exists(new), "the settings were not carried")
        with open(new) as handle:
            said = handle.read()
        self.assertIn("LED_COUNT=17", said)
        # The board it is plugged into, which is not renamed - see the note
        # in user-unit.sh. A migration that rewrote this would unplug the bar.
        self.assertIn("SERIAL_PORT=/dev/steamos-led-esp", said)
        self.assertFalse(
            os.path.exists(os.path.join(root, "etc/steamos-led-serial.conf")))
        power = os.path.join(root, "etc/steamos-utility-center-power.conf")
        self.assertTrue(os.path.exists(power))

    def test_the_notification_pipe_is_pointed_at_the_new_directory(self):
        """NOTIFY_FIFO is a live setting naming a directory the unit creates.

        RuntimeDirectory= in the unit is what makes /run/<name> exist, so
        renaming the unit renames that directory. A config carried over
        verbatim would point the pipe at /run/steamos-led-serial, which
        nothing creates any more - and notifications would simply stop
        arriving, with nothing in any log to say why.
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

        It carries an old-looking name and is written by the vendored
        leds-valve-shim installer, under that name, today - see
        leds-valve-shim/PROVENANCE.md, which is why that script is kept
        unmodified. Sweeping it up with the rest of the steamos-led-* names
        would stop the module loading at the next boot, which is the strip
        going dark a reboot after an update, with nothing to connect the two.
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

        The second run meets a machine that is already half migrated, and it
        must not undo the first - in particular it must not move the config
        it already moved, or overwrite the new one with a stale copy.
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
        """Both present is a machine somebody has already migrated by hand.

        The new file is the one being read, so it is the one that stands. The
        old one is left where it is rather than deleted: it is still somebody's
        settings, and this is not the script that gets to throw them away.
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

        Somebody uninstalling may never have run the new installer - they
        pull, they uninstall, and every name on the machine is an old one. So
        the uninstaller walks this list too, or it is the one script that does
        not know the old names exist.
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
        # The call, on a line of its own - not the name, which also appears in
        # the comment above it explaining why the call is there. Matching the
        # name found the comment, which sits earlier in the file than the
        # first install step whether the call is there at all or not: deleting
        # the call outright still passed.
        called = re.search(r"^migrate_old_install$", text, re.M)
        self.assertIsNotNone(called, "the installer never migrates")
        self.assertLess(called.start(),
                        text.index('install -d -m 0755 "$INSTALL_DIR"'),
                        "the migration runs after the install has begun")


if __name__ == "__main__":
    unittest.main()
