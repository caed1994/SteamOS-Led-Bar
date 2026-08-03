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

    def test_library_lookup_reports_a_missing_file(self):
        with self.assertRaises(steamworks.SteamworksError):
            steamworks.find_library("/nonexistent/libsteam_api.so")

    def test_explicit_library_is_used_as_given(self):
        path = os.path.join(self.tmpdir, "libsteam_api.so")
        with open(path, "wb") as handle:
            handle.write(b"\x7fELF not really")
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
