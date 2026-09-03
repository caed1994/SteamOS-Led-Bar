# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""Every file we wrote says which licence it is under.

A header in each file is necessary because a file moves. A user copies one
into a gist, a forum message, or another project, and the copy carries no
other context. The headers do that work only while each file has one. A new
file with no header stops that, and a test finds such a file.
"""

import os
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, ".."))

LICENCE = "GPL-3.0-or-later"

# The files of this project. These are the suffixes, and also the programs
# with no language in their name. A script with the name
# steamos-utility-center is source code.
SUFFIXES = (".py", ".sh", ".cpp", ".h")
SCRIPTS = ("server/steamos-utility-center", "gui/steamos-utility-center-panel",
           "systemd-sleep/steamos-utility-center")

# Code under the licence of another project. This project cannot change that
# licence. The kernel shim carries its own SPDX line with GPL-2.0+. The
# directory cec-toolkit/ is a fork of an MIT project. It stays MIT, with its
# own LICENSE file beside its own ORIGIN. The tests below give the checks for
# those two directories.
#
# A fork is still somebody else's copyright. Ours is a second line in that
# LICENSE, not a licence change, and not a GPL-3 header on every file in it.
OTHERS = ("leds-valve-shim", "cec-toolkit")


def tracked():
    """Every file git knows about, so nothing untracked or ignored counts."""
    listing = subprocess.run(["git", "-C", REPO, "ls-files", "-z"],
                             capture_output=True, text=True, check=True)
    return [name for name in listing.stdout.split("\0") if name]


def ours():
    for name in tracked():
        if name.split("/")[0] in OTHERS:
            continue
        if name.endswith(SUFFIXES) or name in SCRIPTS:
            yield name


class SpdxHeaderTest(unittest.TestCase):
    def setUp(self):
        self.files = sorted(ours())
        self.assertGreater(len(self.files), 30,
                           "the file list looks wrong, not the headers")

    def _head(self, name):
        """The first few lines, which is where an SPDX header has to be.

        Not the whole file: a header further down is not one, and the string
        turning up in the middle of a test is not a licence statement.
        """
        with open(os.path.join(REPO, name)) as handle:
            return "".join(next(handle, "") for _ in range(4))

    def test_every_source_file_names_the_licence(self):
        missing = [name for name in self.files
                   if "SPDX-License-Identifier: " + LICENCE
                   not in self._head(name)]
        self.assertEqual(missing, [], "no SPDX header near the top")

    def test_every_source_file_names_a_copyright_holder(self):
        missing = [name for name in self.files
                   if "SPDX-FileCopyrightText:" not in self._head(name)]
        self.assertEqual(missing, [])

    def test_the_shebang_still_comes_first(self):
        # A header above #! makes the file unrunnable, and only the ones that
        # are executable would have shown it.
        for name in self.files:
            with open(os.path.join(REPO, name)) as handle:
                first = handle.readline()
            if "#!" in self._head(name):
                self.assertTrue(first.startswith("#!"),
                                "%s: shebang is not on line 1" % name)

    def test_the_vendored_module_keeps_its_own_licence(self):
        # GPL-2.0+, and this project cannot change it. See
        # leds-valve-shim/PROVENANCE.md.
        path = os.path.join(REPO, "leds-valve-shim", "leds-valve-shim.c")
        with open(path) as handle:
            self.assertIn("SPDX-License-Identifier: GPL-2.0+",
                          handle.readline())

    def test_the_cec_module_carries_its_own_licence_and_its_provenance(self):
        """Left out of the header sweep, so checked for the other thing.

        A fork of somebody else's project is not ours to put a GPL-3 header
        on, and the reason that is safe rather than sloppy is that the tree
        says what it is under and where it came from. Without both, an
        excluded directory is just code with no licence anybody can find.
        """
        where = os.path.join(REPO, "cec-toolkit")
        for needed in ("LICENSE", "ORIGIN"):
            self.assertTrue(os.path.exists(os.path.join(where, needed)),
                            "cec-toolkit has no %s" % needed)

    def test_the_cec_module_is_not_quietly_relabelled(self):
        # The failure this is about is somebody running a formatter or a
        # header-adder across the repository: our line appearing in a file
        # that is not only ours is a licence claim on work we did not do.
        # Fixing five bugs in a fork does not make its copyright ours.
        for name in tracked():
            if name.split("/")[0] != "cec-toolkit":
                continue
            with open(os.path.join(REPO, name), "rb") as handle:
                text = handle.read().decode("utf-8", "replace")
            self.assertNotIn("SPDX-License-Identifier: " + LICENCE, text,
                             "%s has been given this project's licence" % name)

    def test_the_licence_text_is_actually_there(self):
        # A header pointing at a licence the repository does not carry is
        # worse than no header: it names terms nobody can read.
        with open(os.path.join(REPO, "LICENSE")) as handle:
            text = handle.read()
        self.assertIn("GNU GENERAL PUBLIC LICENSE", text)
        self.assertIn("Version 3, 29 June 2007", text)
        self.assertIn("END OF TERMS AND CONDITIONS", text)


if __name__ == "__main__":
    unittest.main()
