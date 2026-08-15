# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""Every file we wrote says which licence it is under.

Per-file headers exist because files travel: one gets copied into a gist, a
forum post, somebody else's project, and arrives with no idea what it is. The
headers only do that job while they are complete, and the way they stop being
complete is that somebody adds a file and does not think about it - which is
exactly what a test is for.
"""

import os
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, ".."))

LICENCE = "GPL-3.0-or-later"

# What counts as ours to license. Suffixes plus the executables whose language
# is not in their name - a script called steamos-led-serial is source too.
SUFFIXES = (".py", ".sh", ".cpp", ".h")
SCRIPTS = ("server/steamos-led-serial", "gui/steamos-led-panel",
           "systemd-sleep/steamos-led-serial")

# Somebody else's code, under somebody else's licence. It carries its own SPDX
# line saying GPL-2.0+, and this project does not get to relabel it.
VENDORED = "leds-valve-shim"


def tracked():
    """Every file git knows about, so nothing untracked or ignored counts."""
    listing = subprocess.run(["git", "-C", REPO, "ls-files", "-z"],
                             capture_output=True, text=True, check=True)
    return [name for name in listing.stdout.split("\0") if name]


def ours():
    for name in tracked():
        if name.split("/")[0] == VENDORED:
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
        # GPL-2.0+, not ours to change - see leds-valve-shim/PROVENANCE.md.
        path = os.path.join(REPO, VENDORED, "leds-valve-shim.c")
        with open(path) as handle:
            self.assertIn("SPDX-License-Identifier: GPL-2.0+",
                          handle.readline())

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
