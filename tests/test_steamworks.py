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

from steamos_led import elf, service, steamworks  # noqa: E402


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

    def test_a_late_stats_load_does_not_flash_the_back_catalogue(self):
        # RequestCurrentStats is asynchronous: every achievement reads as
        # locked until Steam answers. Without this guard, a player with 40
        # earned achievements would get 40 flashes when the answer landed.
        stats = FakeStats({name: False for name in "ABCDEFGHIJ"})
        watcher = steamworks.AchievementWatcher(stats)
        watcher.poll()
        stats.state.update({name: True for name in "ABCDEFGH"})
        self.assertEqual(watcher.poll(), [])

    def test_a_real_unlock_after_a_flood_still_reports(self):
        stats = FakeStats({name: False for name in "ABCDEFGHIJ"})
        watcher = steamworks.AchievementWatcher(stats)
        watcher.poll()
        stats.state.update({name: True for name in "ABCDEFGH"})
        watcher.poll()
        stats.state["I"] = True
        self.assertEqual(watcher.poll(), ["I"])

    def test_a_believable_burst_is_still_reported(self):
        # Games do hand out two or three at once; only a flood is suspicious.
        stats = FakeStats({name: False for name in "ABCDEFGHIJ"})
        watcher = steamworks.AchievementWatcher(stats)
        watcher.poll()
        stats.state.update({"A": True, "B": True})
        self.assertEqual(sorted(watcher.poll()), ["A", "B"])

    def test_flood_threshold_is_configurable(self):
        stats = FakeStats({name: False for name in "ABCDEFGHIJ"})
        watcher = steamworks.AchievementWatcher(stats, flood_threshold=100)
        watcher.poll()
        stats.state.update({name: True for name in "ABCDEFGH"})
        self.assertEqual(len(watcher.poll()), 8)

    def test_pumps_callbacks(self):
        stats = FakeStats({"A": False})
        watcher = steamworks.AchievementWatcher(stats)
        watcher.poll()
        watcher.poll()
        self.assertEqual(stats.callbacks_run, 2)


class PointerNormalisationTest(unittest.TestCase):
    """The interface pointer arrives in two shapes depending on the route."""

    def test_raw_address_is_wrapped(self):
        import ctypes
        pointer = steamworks._as_pointer(0x7FFF1234)
        self.assertIsInstance(pointer, ctypes.c_void_p)
        self.assertEqual(pointer.value, 0x7FFF1234)

    def test_existing_pointer_is_passed_through(self):
        # Wrapping a c_void_p in c_void_p() raises "cannot be converted to
        # pointer" instead of being a no-op, which crashed the check right
        # after a route had successfully resolved.
        import ctypes
        original = ctypes.c_void_p(0x7FFF1234)
        self.assertIs(steamworks._as_pointer(original), original)

    def test_null_stays_null(self):
        self.assertFalse(steamworks._as_pointer(0).value)


class SteamDiscoveryTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(self._teardown)

    def _teardown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _fake_steam_root(self):
        """Point steam_root() at the temp directory for one test."""
        original = steamworks.STEAM_ROOTS
        steamworks.STEAM_ROOTS = (self.tmpdir,)
        self.addCleanup(setattr, steamworks, "STEAM_ROOTS", original)
        # Also move HOME, or the last candidate path (~/.steam/registry.vdf)
        # would read the real Steam of whoever runs the suite.
        original_home = os.environ.get("HOME")

        def restore_home():
            if original_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = original_home

        os.environ["HOME"] = self.tmpdir
        self.addCleanup(restore_home)
        return self.tmpdir

    def test_registry_parsing(self):
        root = self._fake_steam_root()
        with open(os.path.join(root, "registry.vdf"), "w") as handle:
            handle.write('"Registry"\n{\n\t"HKCU"\n\t{\n'
                         '\t\t"RunningAppID"\t\t"570"\n\t}\n}\n')
        self.assertEqual(steamworks._app_id_from_registry(), 570)

    def test_registry_with_no_running_app_reports_nothing(self):
        root = self._fake_steam_root()
        with open(os.path.join(root, "registry.vdf"), "w") as handle:
            handle.write('"Registry"\n{\n\t"HKCU"\n\t{\n'
                         '\t\t"RunningAppID"\t\t"0"\n\t}\n}\n')
        self.assertIsNone(steamworks._app_id_from_registry())

    def test_the_registry_alone_can_answer(self):
        # The process scan reads every process's environment, so the watcher
        # asks for it only every few ticks. When the registry knows the
        # answer, skipping the scan must not change it.
        root = self._fake_steam_root()
        with open(os.path.join(root, "registry.vdf"), "w") as handle:
            handle.write('"Registry"\n{\n\t"HKCU"\n\t{\n'
                         '\t\t"RunningAppID"\t\t"570"\n\t}\n}\n')
        self.assertEqual(steamworks.running_app_id(scan_processes=False), 570)
        self.assertEqual(steamworks.running_app_id(), 570)

    def test_skipping_the_scan_does_not_invent_an_app(self):
        self._fake_steam_root()          # no registry.vdf in it
        self.assertIsNone(steamworks.running_app_id(scan_processes=False))

    def test_the_scan_is_still_the_fallback(self):
        # Losing the process scan entirely would break detection on machines
        # whose registry does not report the running app.
        self._fake_steam_root()
        calls = []
        original = steamworks._app_id_from_processes
        steamworks._app_id_from_processes = lambda: calls.append(1) or 42
        self.addCleanup(setattr, steamworks,
                        "_app_id_from_processes", original)
        self.assertEqual(steamworks.running_app_id(), 42)
        self.assertEqual(len(calls), 1)

    @staticmethod
    def _fake_library(path, elf_class):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(b"\x7fELF" + bytes([elf_class]) + b"\x00" * 32)
        return path

    def test_elf_class_is_read_from_the_header(self):
        thirty_two = self._fake_library(
            os.path.join(self.tmpdir, "a", "libsteam_api.so"),
            elf.ELFCLASS32)
        sixty_four = self._fake_library(
            os.path.join(self.tmpdir, "b", "libsteam_api.so"),
            elf.ELFCLASS64)
        self.assertEqual(elf.elf_class(thirty_two), elf.ELFCLASS32)
        self.assertEqual(elf.elf_class(sixty_four), elf.ELFCLASS64)

    def test_elf_class_of_a_non_elf_file_is_none(self):
        plain = os.path.join(self.tmpdir, "notes.txt")
        with open(plain, "w") as handle:
            handle.write("not a library")
        self.assertIsNone(elf.elf_class(plain))
        self.assertIsNone(elf.elf_class(os.path.join(self.tmpdir, "gone")))

    def test_explicit_library_of_the_wrong_arch_is_refused(self):
        # Proton ships files/lib (32-bit) beside files/lib64, and the 32-bit
        # one sorts first - loading it fails with a bare "wrong ELF class".
        wrong = elf.ELFCLASS32 \
            if steamworks.WANTED_ELF_CLASS == elf.ELFCLASS64 \
            else elf.ELFCLASS64
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




class ProcessScanRhythmTest(unittest.TestCase):
    """When the watcher pays for the full process scan.

    Regression guard. Skipping the scan while a game was attached made the
    watcher read "no game" on a machine whose registry.vdf never names the
    running app: it detached and reattached every five seconds, and because
    every attach opens a Steamworks session as that app, Steam then sat on
    "Stopping" forever when the game was quit.
    """

    def test_an_attached_game_is_confirmed_on_every_tick(self):
        for tick in range(25):
            self.assertTrue(
                service._should_scan_processes(tick, attached=True),
                "tick %d skipped the only source that finds the game" % tick)

    def test_searching_for_a_game_is_throttled(self):
        scans = [tick for tick in range(25)
                 if service._should_scan_processes(tick, attached=False)]
        self.assertEqual(scans, [0, 5, 10, 15, 20])

    def test_the_first_tick_looks_straight_away(self):
        # Otherwise starting the watcher with a game already running would
        # wait out the throttle before noticing it.
        self.assertTrue(service._should_scan_processes(0, attached=False))


class WatcherSessionLifetimeTest(unittest.TestCase):
    """The watcher handles one game session and then ends its process.

    SteamAPI_Init registers the process with Steam as an instance of that
    game. Measured on hardware: Steam keeps a game on "Stopping" while such a
    registration exists, and SteamAPI_Shutdown does not clear it - only the
    process ending does. So the loop must not just detach and carry on.
    """

    def setUp(self):
        self.opened, self.closed, self.flashes = [], [], []
        self._patch(steamworks, "find_library", lambda _explicit: "libsteam.so")
        self._patch(steamworks, "select_route",
                    lambda app, lib, reporter=None: ("accessor:x", 1))
        self._patch(steamworks, "UserStats", self._fake_stats)
        self._patch(steamworks, "AchievementWatcher", _FakeWatcher)
        self._patch(service.notify, "send",
                    lambda fifo, kind: self.flashes.append(kind))

    def _patch(self, module, name, value):
        original = getattr(module, name)
        setattr(module, name, value)
        self.addCleanup(setattr, module, name, original)

    def _fake_stats(self, app_id, library, route):
        return _FakeStats(app_id, self.opened, self.closed)

    def _run(self, app_ids):
        """Play back these running_app_id() answers, then stop the loop."""
        answers = iter(app_ids)

        def next_answer(scan_processes=True):
            try:
                return next(answers)
            except StopIteration:
                raise KeyboardInterrupt      # same exit the user's Ctrl-C takes

        self._patch(steamworks, "running_app_id", next_answer)
        return service.run_watch_achievements(
            {"NOTIFY_FIFO": "/dev/null", "STEAM_ROUTE": "auto",
             "STEAM_LIBRARY": "auto"}, interval=0)

    def test_it_exits_once_the_game_is_gone(self):
        # The None is the game ending; anything after it must not be reached.
        self.assertEqual(self._run([1942280, 1942280, None, 1942280]), 0)
        self.assertEqual(self.opened, [1942280])
        self.assertEqual(self.closed, [1942280],
                         "the session has to be closed before the process goes")

    def test_it_stays_for_as_long_as_the_game_does(self):
        # One session for one game. Reattaching mid-game is what made Steam
        # hang in the first place, so opening exactly once is the property.
        self._run([1942280] * 6)
        self.assertEqual(self.opened, [1942280])

    def test_waiting_for_a_game_does_not_end_the_process(self):
        # Exiting with nothing attached would put systemd in a restart loop.
        self._run([None, None, None])
        self.assertEqual(self.opened, [])
        self.assertEqual(self.closed, [])

    def test_the_flash_still_fires_before_it_exits(self):
        self._run([1942280, 1942280, None])
        self.assertEqual(self.flashes, ["achievement"])


class _FakeStats:
    def __init__(self, app_id, opened, closed):
        self.app_id, self._opened, self._closed = app_id, opened, closed
        self._open = False

    def open(self):
        self._open = True
        self._opened.append(self.app_id)

    def close(self):
        # Idempotent, like the real one - the loop closes on the way out and
        # the shutdown handler closes again.
        if self._open:
            self._open = False
            self._closed.append(self.app_id)

    def display_name(self, name):
        return name


class _FakeWatcher:
    """Reports one unlock on its second poll, then nothing."""

    def __init__(self, stats):
        self.stats = stats
        self.polls = 0

    def poll(self):
        self.polls += 1
        return ["FIRST_BLOOD"] if self.polls == 2 else []




class MessageSupportTest(unittest.TestCase):
    """Reading off what a library can do for friend messages, before loading it.

    Whether this is possible at all is a property of the borrowed
    libsteam_api.so: manual callback dispatch arrived in SDK 1.51, and Proton
    ships older copies. Answering that from the symbol table costs nothing and
    saves loading a library that cannot work.
    """

    FULL = {"manual_dispatch": True, "listen": True, "read_message": True,
            "accessors": ["SteamAPI_SteamFriends_v017"],
            "find_or_create": True, "via_client": True}

    def _without(self, **changes):
        support = dict(self.FULL)
        support.update(changes)
        return support

    def test_a_complete_library_is_usable(self):
        self.assertTrue(steamworks.usable_for_messages(self.FULL))

    def test_no_manual_dispatch_means_no_callbacks_at_all(self):
        self.assertFalse(steamworks.usable_for_messages(
            self._without(manual_dispatch=False)))

    def test_no_listen_call_means_nothing_to_switch_on(self):
        self.assertFalse(steamworks.usable_for_messages(
            self._without(listen=False)))

    def test_isteamfriends_has_to_be_reachable_somehow(self):
        self.assertFalse(steamworks.usable_for_messages(
            self._without(accessors=[], find_or_create=False,
                          via_client=False)))
        # Any one of the three doors is enough on its own.
        closed = {"accessors": [], "find_or_create": False, "via_client": False}
        for door, value in (("accessors", ["SteamAPI_SteamFriends_v017"]),
                            ("find_or_create", True), ("via_client", True)):
            opened = dict(closed)
            opened[door] = value
            self.assertTrue(
                steamworks.usable_for_messages(self._without(**opened)), door)

    def test_an_unreadable_library_is_not_usable(self):
        self.assertFalse(steamworks.usable_for_messages({"error": "truncated"}))

    def test_support_of_a_non_library_reports_an_error(self):
        import shutil, tempfile
        tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmpdir, ignore_errors=True)
        path = os.path.join(tmpdir, "notelf.so")
        with open(path, "w") as handle:
            handle.write("not a library")
        self.assertIn("error", steamworks.message_support(path))


class CallbackMsgLayoutTest(unittest.TestCase):
    """CallbackMsg_t has to match what the C side writes into it."""

    def test_fields_are_where_the_sdk_puts_them(self):
        import ctypes
        self.assertEqual(steamworks.CallbackMsg.user.offset, 0)
        self.assertEqual(steamworks.CallbackMsg.callback.offset, 4)
        # The payload pointer is pointer-aligned, so on 64-bit there are four
        # bytes of padding after the two ints. Getting this wrong would read
        # the payload size out of the middle of the pointer.
        self.assertEqual(steamworks.CallbackMsg.param.offset,
                         ctypes.sizeof(ctypes.c_void_p))
        self.assertEqual(steamworks.CallbackMsg.param_size.offset,
                         ctypes.sizeof(ctypes.c_void_p) * 2)




class LibrarySearchTest(unittest.TestCase):
    """Where a libsteam_api.so is looked for.

    Found on a real machine: the only copies our globs saw belonged to one
    Proton version, while the Steam client's own copies under steamrt64/ sat
    right there unnoticed. Those matter most - they come with Steam itself, so
    they are present whatever games or Proton versions are installed, and
    newer Proton no longer ships one at all.
    """

    def setUp(self):
        import shutil, tempfile
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        original = steamworks.STEAM_ROOTS
        steamworks.STEAM_ROOTS = (self.root,)
        self.addCleanup(setattr, steamworks, "STEAM_ROOTS", original)

    def _place(self, relative, root=None):
        path = os.path.join(root or self.root, relative)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(b"\x7fELF" + bytes([elf.ELFCLASS64]) + b"\x00" * 32)
        return path

    def test_the_steam_clients_own_copies_are_found(self):
        wanted = {self._place("steamrt64/libsteam_api.so"),
                  self._place("steamrt32/libsteam_api.so")}
        self.assertTrue(wanted.issubset(set(steamworks.find_libraries())))

    def test_games_are_still_found(self):
        path = self._place(
            "steamapps/common/Proton 9.0 (Beta)/files/lib64/libsteam_api.so")
        self.assertIn(path, steamworks.find_libraries())

    def test_a_deeply_nested_copy_is_found(self):
        # Newer layouts bury it further down than the old globs reached.
        path = self._place(
            "steamapps/common/Runtime/files/lib/steamrt/x86_64/libsteam_api.so")
        self.assertIn(path, steamworks.find_libraries())

    def test_a_second_library_folder_is_searched(self):
        import shutil, tempfile
        card = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, card, ignore_errors=True)
        os.makedirs(os.path.join(self.root, "steamapps"), exist_ok=True)
        with open(os.path.join(self.root, "steamapps",
                               "libraryfolders.vdf"), "w") as handle:
            handle.write('"libraryfolders"\n{\n\t"0"\n\t{\n'
                         '\t\t"path"\t\t"%s"\n\t}\n\t"1"\n\t{\n'
                         '\t\t"path"\t\t"%s"\n\t}\n}\n' % (self.root, card))
        path = self._place("steamapps/common/Game/libsteam_api.so", root=card)
        self.assertIn(card, steamworks.library_roots())
        self.assertIn(path, steamworks.find_libraries())

    def test_a_library_folder_that_is_gone_is_skipped(self):
        os.makedirs(os.path.join(self.root, "steamapps"), exist_ok=True)
        with open(os.path.join(self.root, "steamapps",
                               "libraryfolders.vdf"), "w") as handle:
            handle.write('"libraryfolders"\n{\n\t"0"\n\t{\n'
                         '\t\t"path"\t\t"/run/media/removed-sd-card"\n\t}\n}\n')
        self.assertEqual(steamworks.library_roots(), [self.root])

    def test_no_libraryfolders_file_is_not_an_error(self):
        self.assertEqual(steamworks.library_roots(), [self.root])

    def test_the_order_is_stable(self):
        self._place("steamrt64/libsteam_api.so")
        self._place("steamapps/common/A Game/libsteam_api.so")
        found = steamworks.find_libraries()
        self.assertEqual(found, sorted(found))
        self.assertEqual(len(found), len(set(found)), "no duplicates")


if __name__ == "__main__":
    unittest.main()
