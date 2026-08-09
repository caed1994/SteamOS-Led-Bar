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

    def test_it_flashes_from_the_clone_whatever_it_was_started_in(self):
        if not shutil.which("pio") and not any(
                os.path.exists(os.path.expanduser(path)) for path in
                ("~/.platformio/penv/bin/pio", "~/.local/bin/pio")):
            self.skipTest("no PlatformIO to get past the check with")
        result = subprocess.run(
            ["bash", os.path.join(self.repo, "scripts", "flash-firmware.sh"),
             "fake"],
            cwd="/", capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("flashing from %s" % self.repo, result.stdout)


if __name__ == "__main__":
    unittest.main()
