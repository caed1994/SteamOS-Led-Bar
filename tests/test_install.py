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
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
INSTALLER = os.path.join(HERE, "..", "install.sh")


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


class InstallerShapeTest(unittest.TestCase):
    """Properties of install.sh that are easy to break from a distance."""

    def setUp(self):
        with open(INSTALLER) as handle:
            self.text = handle.read()

    def test_the_installer_parses(self):
        done = subprocess.run(["bash", "-n", INSTALLER],
                              capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)

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

    def test_the_rootfs_is_only_locked_again_if_we_unlocked_it(self):
        # Somebody who ran steamos-readonly disable themselves should find it
        # the way they left it.
        self.assertIn("ROOTFS_RELOCK=1", self.text)
        self.assertIn("[[ $ROOTFS_RELOCK -eq 1 ]] || return 0", self.text)

    def test_an_abandoned_run_still_locks_it_again(self):
        self.assertIn("trap relock_rootfs EXIT", self.text)

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
