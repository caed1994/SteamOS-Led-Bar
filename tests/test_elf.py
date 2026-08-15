# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the ELF symbol reader.

Guessing Steamworks symbol names is what broke first, so the reader that
replaced the guess is checked against real shared objects on this machine, and
against `nm` when binutils happens to be installed.
"""

import glob
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "server"))

from steamos_led import elf, steamworks  # noqa: E402


def _some_libraries(limit=3):
    found = []
    for pattern in ("/usr/lib/x86_64-linux-gnu/lib*.so.*",
                    "/lib/x86_64-linux-gnu/lib*.so.*",
                    "/usr/lib*/lib*.so.*"):
        for path in sorted(glob.glob(pattern)):
            if os.path.isfile(path) and not os.path.islink(path):
                found.append(path)
                if len(found) >= limit:
                    return found
    return found


class ElfSymbolTest(unittest.TestCase):
    def setUp(self):
        self.libraries = _some_libraries()
        if not self.libraries:
            self.skipTest("no shared libraries found to read")

    def test_reads_symbols_from_real_libraries(self):
        for path in self.libraries:
            symbols = elf.exported_symbols(path)
            self.assertTrue(symbols, "no symbols read from %s" % path)
            self.assertTrue(all(isinstance(name, str) for name in symbols))

    def test_matches_nm(self):
        if not shutil.which("nm"):
            self.skipTest("binutils not installed")
        path = self.libraries[0]
        output = subprocess.run(["nm", "-D", "--defined-only", path],
                                capture_output=True, text=True).stdout
        # nm decorates names with @@VERSION; compare the bare names.
        expected = {line.split()[-1].split("@")[0]
                    for line in output.splitlines() if line.strip()}
        actual = {name.split("@")[0] for name in elf.exported_symbols(path)}
        self.assertTrue(expected)
        self.assertTrue(expected.issubset(actual),
                        "missed: %s" % sorted(expected - actual)[:5])

    def test_rejects_a_file_that_is_not_elf(self):
        with tempfile.NamedTemporaryFile(suffix=".so", delete=False) as handle:
            handle.write(b"definitely not an ELF object")
            path = handle.name
        self.addCleanup(os.unlink, path)
        with self.assertRaises(elf.ElfError):
            elf.exported_symbols(path)

    def test_rejects_a_truncated_file(self):
        with tempfile.NamedTemporaryFile(suffix=".so", delete=False) as handle:
            handle.write(b"\x7fELF\x02\x01" + b"\x00" * 8)
            path = handle.name
        self.addCleanup(os.unlink, path)
        with self.assertRaises(elf.ElfError):
            elf.exported_symbols(path)


class AccessorPickingTest(unittest.TestCase):
    def test_picks_the_newest_versioned_accessor(self):
        symbols = {"SteamAPI_SteamUserStats_v011",
                   "SteamAPI_SteamUserStats_v012",
                   "SteamAPI_SteamUserStats_v009",
                   "SteamAPI_Init"}
        self.assertEqual(steamworks.user_stats_accessors(symbols),
                         ["SteamAPI_SteamUserStats_v012",
                          "SteamAPI_SteamUserStats_v011",
                          "SteamAPI_SteamUserStats_v009"])

    def test_no_accessor_in_an_older_library(self):
        # Proton's copy has none of these, which is what sent the first
        # attempt into "exports no ISteamUserStats accessor".
        symbols = {"SteamAPI_Init", "SteamAPI_RunCallbacks",
                   "SteamAPI_ISteamClient_GetISteamUserStats"}
        self.assertEqual(steamworks.user_stats_accessors(symbols), [])

    def test_relevant_symbols_are_surfaced_for_diagnosis(self):
        symbols = {"SteamAPI_Init", "SteamAPI_ISteamClient_GetISteamUserStats",
                   "SteamInternal_FindOrCreateUserInterface",
                   "SteamAPI_GetHSteamUser", "some_unrelated_symbol"}
        relevant = steamworks.interesting_symbols(symbols)
        self.assertIn("SteamAPI_ISteamClient_GetISteamUserStats", relevant)
        self.assertIn("SteamInternal_FindOrCreateUserInterface", relevant)
        self.assertIn("SteamAPI_GetHSteamUser", relevant)
        self.assertNotIn("some_unrelated_symbol", relevant)

    def test_interface_versions_are_tried_newest_first(self):
        versions = [int(name[-3:]) for name in steamworks.USER_STATS_INTERFACES]
        self.assertEqual(versions, sorted(versions, reverse=True))


if __name__ == "__main__":
    unittest.main()
