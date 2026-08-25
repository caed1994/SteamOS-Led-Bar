# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""The firmware flasher's refusals.

Flashing itself needs a board on the other end of a USB cable, so what is
tested here is everything that happens before one is touched: being told a
name that does not exist, or asked to flash on a machine with no PlatformIO,
has to cost nothing at all - in particular it must not stop the service.
"""

import os
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "scripts", "flash-firmware.sh")
INI = os.path.join(HERE, "..", "firmware", "led-client", "platformio.ini")


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

    pkexec starts the script in root's home, and the flashing itself runs as
    the caller, who cannot get back into /root. PlatformIO restores the
    directory it started in on the way out, so a flash that had already
    written and verified the board ended with "PermissionError: [Errno 13]
    Permission denied: '/root'" and an exit code claiming it had failed.
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

        The script looks for pio in the *invoking user's* home, which it asks
        getent for - so a machine without PlatformIO could not reach the line
        this is about, and the test skipped itself there. Which meant a check
        on a reported bug ran only where somebody happened to have flashed
        firmware before, and silently nowhere else.
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
