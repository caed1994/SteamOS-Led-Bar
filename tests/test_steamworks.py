"""Tests for the Steamworks achievement watcher.

The API itself needs a running Steam and a game, so what is verified here is
everything around it: finding the library, working out which game is running,
and turning polled state into "this one just unlocked".
"""

import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "server"))

from steamos_led import steamworks  # noqa: E402


class FakeStats:
    """Stands in for ISteamUserStats."""

    def __init__(self, achievements):
        self.state = dict(achievements)
        self.callbacks_run = 0

    def run_callbacks(self):
        self.callbacks_run += 1

    def achievements(self):
        return dict(self.state)

    def display_name(self, api_name):
        return api_name.replace("_", " ").title()


class AchievementWatcherTest(unittest.TestCase):
    def test_first_poll_adopts_state_without_firing(self):
        # Otherwise every restart would flash for the whole back catalogue.
        stats = FakeStats({"ACH_ONE": True, "ACH_TWO": False})
        watcher = steamworks.AchievementWatcher(stats)
        self.assertEqual(watcher.poll(), [])

    def test_reports_a_new_unlock(self):
        stats = FakeStats({"ACH_ONE": True, "ACH_TWO": False})
        watcher = steamworks.AchievementWatcher(stats)
        watcher.poll()
        stats.state["ACH_TWO"] = True
        self.assertEqual(watcher.poll(), ["ACH_TWO"])

    def test_reports_each_unlock_once(self):
        stats = FakeStats({"ACH_ONE": False})
        watcher = steamworks.AchievementWatcher(stats)
        watcher.poll()
        stats.state["ACH_ONE"] = True
        self.assertEqual(watcher.poll(), ["ACH_ONE"])
        self.assertEqual(watcher.poll(), [])

    def test_reports_several_at_once(self):
        stats = FakeStats({"A": False, "B": False, "C": False})
        watcher = steamworks.AchievementWatcher(stats)
        watcher.poll()
        stats.state.update({"A": True, "C": True})
        self.assertEqual(sorted(watcher.poll()), ["A", "C"])

    def test_relocking_does_not_fire(self):
        # Steam Achievement Manager and friends can clear achievements again.
        stats = FakeStats({"A": True})
        watcher = steamworks.AchievementWatcher(stats)
        watcher.poll()
        stats.state["A"] = False
        self.assertEqual(watcher.poll(), [])

    def test_empty_state_is_not_mistaken_for_a_reset(self):
        # Before RequestCurrentStats lands, the list can come back empty; that
        # must not be read as "everything got locked again".
        stats = FakeStats({"A": True})
        watcher = steamworks.AchievementWatcher(stats)
        watcher.poll()
        stats.state = {}
        self.assertEqual(watcher.poll(), [])
        stats.state = {"A": True, "B": True}
        self.assertEqual(watcher.poll(), ["B"])

    def test_pumps_callbacks(self):
        stats = FakeStats({"A": False})
        watcher = steamworks.AchievementWatcher(stats)
        watcher.poll()
        watcher.poll()
        self.assertEqual(stats.callbacks_run, 2)


class SteamDiscoveryTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(self._teardown)

    def _teardown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_registry_parsing(self):
        path = os.path.join(self.tmpdir, "registry.vdf")
        with open(path, "w") as handle:
            handle.write('"Registry"\n{\n\t"HKCU"\n\t{\n'
                         '\t\t"RunningAppID"\t\t"570"\n\t}\n}\n')
        with open(path) as handle:
            import re
            match = re.search(r'"RunningAppID"\s+"(\d+)"', handle.read())
        self.assertIsNotNone(match)
        self.assertEqual(int(match.group(1)), 570)

    @staticmethod
    def _fake_library(path, elf_class):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(b"\x7fELF" + bytes([elf_class]) + b"\x00" * 32)
        return path

    def test_elf_class_is_read_from_the_header(self):
        thirty_two = self._fake_library(
            os.path.join(self.tmpdir, "a", "libsteam_api.so"),
            steamworks.ELFCLASS32)
        sixty_four = self._fake_library(
            os.path.join(self.tmpdir, "b", "libsteam_api.so"),
            steamworks.ELFCLASS64)
        self.assertEqual(steamworks.elf_class(thirty_two), steamworks.ELFCLASS32)
        self.assertEqual(steamworks.elf_class(sixty_four), steamworks.ELFCLASS64)

    def test_elf_class_of_a_non_elf_file_is_none(self):
        plain = os.path.join(self.tmpdir, "notes.txt")
        with open(plain, "w") as handle:
            handle.write("not a library")
        self.assertIsNone(steamworks.elf_class(plain))
        self.assertIsNone(steamworks.elf_class(os.path.join(self.tmpdir, "gone")))

    def test_explicit_library_of_the_wrong_arch_is_refused(self):
        # Proton ships files/lib (32-bit) beside files/lib64, and the 32-bit
        # one sorts first - loading it fails with a bare "wrong ELF class".
        wrong = steamworks.ELFCLASS32 \
            if steamworks.WANTED_ELF_CLASS == steamworks.ELFCLASS64 \
            else steamworks.ELFCLASS64
        path = self._fake_library(
            os.path.join(self.tmpdir, "lib", "libsteam_api.so"), wrong)
        with self.assertRaises(steamworks.SteamworksError) as caught:
            steamworks.find_library(path)
        self.assertIn("bit", str(caught.exception))

    def test_explicit_library_of_the_right_arch_is_accepted(self):
        path = self._fake_library(
            os.path.join(self.tmpdir, "lib64", "libsteam_api.so"),
            steamworks.WANTED_ELF_CLASS)
        self.assertEqual(steamworks.find_library(path), path)

    def test_auto_means_search(self):
        # "auto" is the config default and must not be taken for a path.
        with self.assertRaises(steamworks.SteamworksError) as caught:
            steamworks.find_library("auto")
        self.assertNotIn("no library at auto", str(caught.exception))

    def test_globs_reach_protons_lib64(self):
        relative = "steamapps/common/Proton 9.0 (Beta)/files/lib64/libsteam_api.so"
        import fnmatch
        self.assertTrue(
            any(fnmatch.fnmatch(relative, pattern)
                for pattern in steamworks.LIBRARY_GLOBS),
            "the search must reach the path layout Proton actually uses")

    def test_library_lookup_reports_a_missing_file(self):
        with self.assertRaises(steamworks.SteamworksError):
            steamworks.find_library("/nonexistent/libsteam_api.so")

    def test_file_that_is_not_an_elf_object_is_taken_at_face_value(self):
        # An unreadable header is not proof of the wrong architecture, so the
        # explicit path is still honoured and the loader gets to complain.
        path = os.path.join(self.tmpdir, "libsteam_api.so")
        with open(path, "wb") as handle:
            handle.write(b"not an ELF file at all")
        self.assertEqual(steamworks.find_library(path), path)

    def test_library_globs_cover_the_usual_install_layouts(self):
        # A game's copy is what gets borrowed, so the search has to reach into
        # steamapps/common and a couple of levels below it.
        self.assertIn("steamapps/common/*/libsteam_api.so",
                      steamworks.LIBRARY_GLOBS)
        self.assertTrue(any(pattern.count("*") >= 3
                            for pattern in steamworks.LIBRARY_GLOBS))

    def test_accessor_versions_are_tried_newest_first(self):
        versions = [int(name.rsplit("_v", 1)[1])
                    for name in steamworks.USER_STATS_ACCESSORS]
        self.assertEqual(versions, sorted(versions, reverse=True))

    def test_process_scan_survives_unreadable_entries(self):
        # /proc is full of processes we may not read; that must not raise.
        self.assertIsNone(steamworks._app_id_from_processes() or None)


if __name__ == "__main__":
    unittest.main()
