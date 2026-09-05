# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""The firmware flasher's refusals.

A flash needs a board at the other end of a USB cable. So this file tests
each step before the board. Two conditions must cost nothing: a name that does
not exist, and a machine with no PlatformIO. In particular, neither condition
must stop the service.
"""

import os
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "scripts", "flash-firmware.sh")
INI = os.path.join(HERE, "..", "firmware", "led-client", "platformio.ini")


ESP_SCRIPT = os.path.join(HERE, "..", "flash-esp.sh")


class BuildDependencyTest(unittest.TestCase):
    """PlatformIO's own virtualenv, and the module that its esptool imports.

    The one line that installed it needed a pip inside that virtualenv, and a
    virtualenv made with --without-pip has none. It printed "No module named
    pip" and stopped, with the service already down.

    Nothing here reaches the network. The Python of that virtualenv is a shell
    script that answers as a real one would, and it records what it was asked.
    """

    # What the fake penv Python answers. Each line is one way the script tries,
    # in the order that it tries them.
    PYTHON = """#!/usr/bin/env bash
echo "$@" >> "$RECORD"
case "$*" in
    *"import intelhex"*)
        [[ -e "$MARKER" ]] && exit 0
        echo "ModuleNotFoundError: No module named 'intelhex'" >&2
        exit 1 ;;
    *ensurepip*)
        [[ "$ENSUREPIP" == "1" ]] && { touch "$PIP_MARKER"; exit 0; }
        echo "No module named ensurepip" >&2
        exit 1 ;;
    *"-m pip install"*)
        [[ -e "$PIP_MARKER" ]] && { touch "$MARKER"; exit 0; }
        echo "No module named pip" >&2
        exit 1 ;;
    *purelib*)
        echo "$SITE" ;;
    *)
        exit 0 ;;
esac
"""

    # A pip somewhere else, which writes into that virtualenv.
    OTHER = """#!/usr/bin/env bash
echo "other $@" >> "$RECORD"
case "$*" in
    *"--target"*intelhex*)
        [[ "$OTHER_PIP" == "1" ]] || { echo "no pip here" >&2; exit 1; }
        touch "$MARKER"
        exit 0 ;;
    *)
        exit 1 ;;
esac
"""

    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.home, True)
        self.bin = os.path.join(self.home, "bin")
        penv = os.path.join(self.home, "core", "penv", "bin")
        for where in (self.bin, penv, os.path.join(self.home, "site")):
            os.makedirs(where)
        self.record = os.path.join(self.home, "record")
        self.marker = os.path.join(self.home, "intelhex-is-there")
        self.pip_marker = os.path.join(self.home, "pip-is-there")

        self._write(os.path.join(penv, "python"), self.PYTHON)
        self._write(os.path.join(self.bin, "python3"), self.OTHER)
        # flash-esp.sh gives up before any of this without one.
        self._write(os.path.join(self.bin, "pio"), "#!/bin/sh\nexit 0\n")

    def _write(self, path, text):
        with open(path, "w") as handle:
            handle.write(text)
        os.chmod(path, 0o755)

    def _run(self, ensurepip="0", other_pip="0"):
        """Runs the step that the panel asks for before it stops the service."""
        environment = dict(os.environ)
        environment.update(
            PATH="%s:%s" % (self.bin, os.environ["PATH"]),
            PLATFORMIO_CORE_DIR=os.path.join(self.home, "core"),
            RECORD=self.record, MARKER=self.marker,
            PIP_MARKER=self.pip_marker,
            SITE=os.path.join(self.home, "site"),
            ENSUREPIP=ensurepip, OTHER_PIP=other_pip,
            SUC_PREPARE_ONLY="1")
        return subprocess.run(["bash", ESP_SCRIPT, "esp8266_gpio14"],
                              env=environment, capture_output=True, text=True)

    def _tried(self):
        with open(self.record) as handle:
            return handle.read()

    def test_a_virtualenv_with_no_pip_is_not_the_end_of_it(self):
        """The failure this comes from. One way was tried, and it was the one
        way that a virtualenv made with --without-pip cannot answer.
        """
        result = self._run(ensurepip="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ensurepip", self._tried())

    def test_a_pip_from_somewhere_else_writes_into_it(self):
        """The last way. intelhex is pure Python, so the interpreter that
        installed it does not have to be the one that imports it.
        """
        result = self._run(other_pip="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--target", self._tried())

    def test_it_tries_them_in_order_and_stops_at_the_first(self):
        result = self._run(ensurepip="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("--target", self._tried(),
                         "it went on after the module was there")

    def test_nothing_that_works_names_each_way_it_tried(self):
        result = self._run()
        self.assertEqual(result.returncode, 1)
        for named in ("-m pip install intelhex", "ensurepip", "--target"):
            self.assertIn(named, result.stderr, named)
        self.assertIn("No module named pip", result.stderr,
                      "it hides what the machine said")

    def test_a_module_that_is_there_costs_nothing(self):
        open(self.marker, "w").close()
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("installing", result.stdout)

    def test_the_check_alone_flashes_nothing(self):
        """The panel runs this before it stops the service, so it must stop
        before the part that needs the port.
        """
        open(self.marker, "w").close()
        result = self._run()
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("Building and flashing", result.stdout)


class FlashScriptTest(unittest.TestCase):
    def _run(self, *args, uid=None):
        # PKEXEC_UID is how the script learns who asked, and pointing it at a
        # user with no PlatformIO is how the "not installed" path is reached
        # on a machine that does have it. Without that, this test would flash
        # for real on the machine running it.
        environment = dict(os.environ)
        if uid is not None:
            environment["PKEXEC_UID"] = str(uid)
            environment.pop("SUDO_UID", None)
        return subprocess.run(["bash", SCRIPT] + list(args), env=environment,
                              capture_output=True, text=True)

    NOBODY = 65534

    def test_the_check_of_the_build_comes_before_the_service_stops(self):
        """Which Python modules a build needs is a question with no serial
        port and no root in it. It cost a stopped service and a started one
        before this: the bar went out and came back for a failure that no port
        was needed to find.
        """
        with open(SCRIPT) as handle:
            body = handle.read()
        self.assertLess(body.index("SUC_PREPARE_ONLY=1"),
                        body.index('systemctl stop "$SERVICE"'))

    def test_it_wants_to_be_told_what_to_flash(self):
        result = self._run()
        self.assertEqual(result.returncode, 2)
        self.assertIn("usage", result.stderr)

    def test_an_unknown_build_is_refused_before_anything_happens(self):
        result = self._run("nonesuch")
        self.assertEqual(result.returncode, 2)
        self.assertIn("no firmware environment called 'nonesuch'",
                      result.stderr)
        self.assertNotIn("Stopping", result.stdout,
                         "the service must not step aside for a typo")

    def test_it_says_which_builds_there_are(self):
        # Straight out of platformio.ini, so the list cannot go stale.
        with open(INI) as handle:
            expected = [line[5:-2] for line in handle.read().splitlines()
                        if line.startswith("[env:")]
        listed = self._run("nonesuch").stderr
        for environment in expected:
            self.assertIn(environment, listed)

    def test_no_platformio_is_a_message_and_not_a_half_flashed_board(self):
        # Asked to flash for a user who has none: it has to stop here, before
        # the service steps aside, and say the board still has what it had.
        if subprocess.run(["getent", "passwd", str(self.NOBODY)],
                          capture_output=True).returncode != 0:
            self.skipTest("no unprivileged user to stand in for one")
        result = self._run("nodemcuv2", uid=self.NOBODY)
        self.assertEqual(result.returncode, 1)
        self.assertIn("PlatformIO", result.stderr)
        self.assertIn("Nothing was changed", result.stderr)
        self.assertNotIn("Stopping", result.stdout)


class WorkingDirectoryTest(unittest.TestCase):
    """Where the flasher stands while it runs.

    pkexec starts the script in the home directory of root. The flash runs as
    the caller, and the caller cannot enter /root. PlatformIO returns to its
    start directory at the end. A flash that wrote the board and verified it
    therefore ended with "PermissionError: [Errno 13] Permission denied:
    '/root'", and with an exit code for a failure.
    """

    def setUp(self):
        self.repo = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)
        os.makedirs(os.path.join(self.repo, "scripts"))
        os.makedirs(os.path.join(self.repo, "firmware", "led-client"))
        shutil.copy(SCRIPT, os.path.join(self.repo, "scripts",
                                         "flash-firmware.sh"))
        with open(os.path.join(self.repo, "firmware", "led-client",
                               "platformio.ini"), "w") as handle:
            handle.write("[env:fake]\nplatform = native\n")
        # Stands in for the real flasher, which wants a board on a cable.
        with open(os.path.join(self.repo, "flash-esp.sh"), "w") as handle:
            handle.write('#!/usr/bin/env bash\necho "flashing from $PWD"\n')

    def _pretend_home(self):
        """A home with a PlatformIO in it, and a getent that points there.

        The script looks for pio in the home directory of the *calling user*, and
        it asks getent for that directory. A machine without PlatformIO therefore
        did not reach the line of this test, and the test skipped itself there. A
        test for a reported fault then ran only on a machine with an earlier
        firmware flash, and it ran nowhere else with no message.
        """
        home = os.path.join(self.repo, "home")
        binaries = os.path.join(home, ".local", "bin")
        os.makedirs(binaries)
        pio = os.path.join(binaries, "pio")
        with open(pio, "w") as handle:
            handle.write("#!/usr/bin/env bash\nexit 0\n")
        os.chmod(pio, 0o755)

        # The script wants field six of the passwd line, and nothing else.
        stubs = os.path.join(self.repo, "stubs")
        os.makedirs(stubs)
        getent = os.path.join(stubs, "getent")
        with open(getent, "w") as handle:
            handle.write("#!/usr/bin/env bash\n"
                         'echo "$2:x:0:0::%s:/bin/bash"\n' % home)
        os.chmod(getent, 0o755)
        return dict(os.environ,
                    PATH=stubs + os.pathsep + os.environ.get("PATH", ""))

    def test_it_flashes_from_the_clone_whatever_it_was_started_in(self):
        result = subprocess.run(
            ["bash", os.path.join(self.repo, "scripts", "flash-firmware.sh"),
             "fake"],
            cwd="/", capture_output=True, text=True, env=self._pretend_home())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("flashing from %s" % self.repo, result.stdout)


if __name__ == "__main__":
    unittest.main()
