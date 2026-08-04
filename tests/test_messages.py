"""Tests for friend-message detection.

The Steam side needs a running client, so what is checked here is the part
that can be got wrong on its own: the callback struct layout, the constants,
which library versions can do it at all, and that the whole feature stays
optional - achievements work everywhere, messages need a newer library.
"""

import ctypes
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "server"))

from steamos_led import steamworks  # noqa: E402


class CallbackLayoutTest(unittest.TestCase):
    def test_callback_message_matches_the_sdk_struct(self):
        # CallbackMsg_t is { HSteamUser, int, uint8*, int } under pack(8):
        # 4 + 4 + 8 + 4 + 4 padding.
        self.assertEqual(ctypes.sizeof(steamworks.CallbackMsg), 24)
        self.assertEqual(steamworks.CallbackMsg.m_hSteamUser.offset, 0)
        self.assertEqual(steamworks.CallbackMsg.m_iCallback.offset, 4)
        self.assertEqual(steamworks.CallbackMsg.m_pubParam.offset, 8)
        self.assertEqual(steamworks.CallbackMsg.m_cubParam.offset, 16)

    def test_chat_callback_id(self):
        # k_iSteamFriendsCallbacks(300) + 43
        self.assertEqual(steamworks.CALLBACK_FRIEND_CHAT_MSG, 343)

    def test_only_real_messages_count(self):
        # The same callback carries typing indicators and emotes; only
        # k_EChatEntryTypeChatMsg is worth a flash.
        self.assertEqual(steamworks.CHAT_ENTRY_TYPE_CHAT_MSG, 1)

    def test_friend_interface_names_use_steams_own_spelling(self):
        # Friends spells these differently from user stats - "SteamFriends017",
        # not "STEAMFRIENDS_INTERFACE_VERSION017". Getting it wrong just means
        # a null interface and no messages, with nothing to explain why.
        self.assertIn("SteamFriends017", steamworks.FRIENDS_INTERFACES)
        for name in steamworks.FRIENDS_INTERFACES:
            self.assertRegex(name, r"^SteamFriends\d{3}$")

    def test_interfaces_are_tried_newest_first(self):
        versions = [int(name[-3:]) for name in steamworks.FRIENDS_INTERFACES]
        self.assertEqual(versions, sorted(versions, reverse=True))


class MessageSupportTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(self._teardown)

    def _teardown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _library(self, symbols):
        """Build a tiny ELF whose dynamic symbols are the given names."""
        # Easier and more honest than hand-rolling an ELF: reuse a real one and
        # stub the reader, since the reader itself is tested elsewhere.
        path = os.path.join(self.tmpdir, "libsteam_api.so")
        with open(path, "wb") as handle:
            handle.write(b"\x7fELF" + bytes([steamworks.ELFCLASS64]) + b"\x00" * 32)
        original = steamworks.elf.exported_symbols
        steamworks.elf.exported_symbols = lambda _p, _s=symbols: set(_s)
        self.addCleanup(setattr, steamworks.elf, "exported_symbols", original)
        return path

    def test_modern_library_is_supported(self):
        path = self._library(set(steamworks.MANUAL_DISPATCH_SYMBOLS)
                             | {"SteamAPI_ISteamClient_GetISteamFriends"})
        supported, reason = steamworks.supports_messages(path)
        self.assertTrue(supported, reason)

    def test_library_without_manual_dispatch_is_refused(self):
        # This is the Proton copy the achievement side already runs on: it
        # predates SDK 1.47, so messages are simply not possible there.
        path = self._library({"SteamAPI_Init",
                              "SteamAPI_ISteamClient_GetISteamUserStats",
                              "SteamAPI_ISteamClient_GetISteamFriends"})
        supported, reason = steamworks.supports_messages(path)
        self.assertFalse(supported)
        self.assertIn("manual dispatch", reason)

    def test_library_without_friends_access_is_refused(self):
        path = self._library(set(steamworks.MANUAL_DISPATCH_SYMBOLS))
        supported, reason = steamworks.supports_messages(path)
        self.assertFalse(supported)
        self.assertIn("ISteamFriends", reason)

    def test_unreadable_library_is_refused_not_crashed(self):
        path = os.path.join(self.tmpdir, "nonsense.so")
        with open(path, "w") as handle:
            handle.write("not an ELF file")
        supported, reason = steamworks.supports_messages(path)
        self.assertFalse(supported)
        self.assertIn("unreadable", reason)


class OptionalityTest(unittest.TestCase):
    """Messages must never be able to break achievements."""

    def test_watcher_reports_zero_when_never_opened(self):
        stats = type("S", (), {"_lib": None, "library_path": "x"})()
        watcher = steamworks.MessageWatcher(stats)
        self.assertEqual(watcher.poll(), 0)

    def test_opening_without_steam_raises_rather_than_crashing(self):
        stats = type("S", (), {"_lib": None, "library_path": "x"})()
        with self.assertRaises(steamworks.SteamworksError):
            steamworks.MessageWatcher(stats).open()

    def test_close_is_safe_before_open(self):
        stats = type("S", (), {"_lib": None, "library_path": "x"})()
        watcher = steamworks.MessageWatcher(stats)
        watcher.close()
        self.assertEqual(watcher.poll(), 0)


if __name__ == "__main__":
    unittest.main()
