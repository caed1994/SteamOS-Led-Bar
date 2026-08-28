# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""Somebody else's tree, kept in ours.

The risk with vendored code is not that it is wrong - upstream tested it -
but that our copy quietly stops being a copy. Two ways that happens: files
get left out when it is taken, and the record of where it came from goes
stale while the tree moves on. Neither shows up until somebody's install
fails on a machine we cannot see, so both are checked here.

Nothing in this file needs the network. What upstream says today is not
knowable offline; what our own tree says about itself is, and that is the
half that goes wrong.
"""

import os
import re
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
VENDOR = os.path.join(HERE, "..", "vendor")
CEC = os.path.join(VENDOR, "steamos-cec-toolkit")


def record(where=CEC):
    """The UPSTREAM file, read the way a shell would read it."""
    values = {}
    with open(os.path.join(where, "UPSTREAM")) as handle:
        for line in handle:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key] = value
    return values


class ProvenanceTest(unittest.TestCase):
    """The three values that make a re-sync a diff instead of a guess."""

    def setUp(self):
        self.record = record()

    def test_it_names_where_it_came_from(self):
        self.assertTrue(self.record["UPSTREAM_URL"].startswith("https://"))
        self.assertIn("steamos-cec-toolkit", self.record["UPSTREAM_URL"])

    def test_it_names_a_commit_and_not_a_branch(self):
        # A branch name moves, so a tree recorded against one has no fixed
        # thing to be diffed from. Tags can be moved too, which is why the
        # commit is recorded beside the tag rather than instead of it.
        self.assertRegex(self.record["UPSTREAM_COMMIT"], r"^[0-9a-f]{40}$")

    def test_the_tag_and_the_version_file_agree(self):
        # VERSION is upstream's own, copied with the rest of the tree. If it
        # disagrees with the tag we recorded, one of the two was edited by
        # hand and neither can be trusted to say what this tree is.
        with open(os.path.join(CEC, "VERSION")) as handle:
            self.assertEqual(handle.read().strip(),
                             self.record["UPSTREAM_TAG"])

    def test_upstream_keeps_its_own_licence(self):
        # MIT, and it stays MIT. This project is GPL-3.0-or-later, which can
        # carry MIT code - it cannot relicense somebody else's copyright.
        with open(os.path.join(CEC, "LICENSE")) as handle:
            self.assertIn("MIT License", handle.read())

    def test_the_vendor_directory_says_what_it_is_for(self):
        with open(os.path.join(VENDOR, "README.md")) as handle:
            said = handle.read()
        self.assertIn("steamos-cec-toolkit", said)
        self.assertIn("MIT", said)


class CompleteTest(unittest.TestCase):
    """Taking a subtree is where files get lost."""

    def _installers(self):
        for name in ("install.sh", "uninstall.sh"):
            with open(os.path.join(CEC, name)) as handle:
                yield name, handle.read()

    def test_every_file_the_installer_reaches_for_is_here(self):
        """The failure this is about happens on somebody else's machine.

        decky/ and assets/ were deliberately left out - the plugin is a second
        front end for the same helper and the assets are screenshots of it.
        Leaving out something the installer actually installs looks identical
        at vendoring time and only shows up as a broken install later.
        """
        wanted = set()
        for _name, text in self._installers():
            wanted.update(re.findall(r"\$PROJECT_DIR/([A-Za-z0-9_./-]+)", text))
        self.assertTrue(wanted, "found no files to check - has the "
                                "installer stopped using $PROJECT_DIR?")
        for each in sorted(wanted):
            self.assertTrue(os.path.exists(os.path.join(CEC, each)),
                            "the installer installs %s and we did not take it"
                            % each)

    def test_nothing_left_here_refers_to_what_was_left_out(self):
        for where, _dirs, files in os.walk(CEC):
            for name in files:
                if name in ("UPSTREAM", "README.upstream.md"):
                    continue            # the two files whose job is to say so
                path = os.path.join(where, name)
                with open(path, "rb") as handle:
                    text = handle.read().decode("utf-8", "replace")
                for gone in ("$PROJECT_DIR/decky", "$PROJECT_DIR/assets"):
                    self.assertNotIn(gone, text, "%s wants %s" % (path, gone))

    def test_the_programs_it_installs_can_be_run(self):
        # install.sh copies these with `install -m 0755`, so the mode here is
        # not what lands on disk - but a file that is not executable in the
        # tree cannot be tried out where it is either, and trying things out
        # is why this tree is in the repository rather than fetched.
        for name in sorted(os.listdir(os.path.join(CEC, "bin"))):
            self.assertTrue(os.access(os.path.join(CEC, "bin", name), os.X_OK),
                            "%s is not executable" % name)

    def test_the_shell_in_it_parses(self):
        for name, _text in self._installers():
            done = subprocess.run(["bash", "-n", os.path.join(CEC, name)],
                                  capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stderr)


if __name__ == "__main__":                                  # pragma: no cover
    unittest.main()
