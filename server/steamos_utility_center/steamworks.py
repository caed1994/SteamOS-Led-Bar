# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""Achievement detection through Valve's local Steamworks API.

This needs no API key and no network. The Steam client already knows the
achievement state, and `libsteam_api.so` is the local interface to it.

Steamworks is a *game-side* API: a program starts it as one specific app, and
it cannot report which game runs. This module thus finds the app ID first.

It uses the flat C API only, so ctypes is sufficient and it marshals no
callback structures.
"""

from __future__ import annotations

import ctypes
import glob
import logging
import os
import re
import struct
import sys
import time

from . import elf

LOG = logging.getLogger(__name__)

STEAM_ROOTS = (
    "~/.local/share/Steam",
    "~/.steam/steam",
    "~/.steam/root",
    "/home/deck/.local/share/Steam",
)

# The version suffix of the accessor depends on the SDK that built the library.
# This module thus reads the symbols and does not guess them. This list is for
# a symbol table that this module cannot read.
USER_STATS_ACCESSORS = tuple(
    "SteamAPI_SteamUserStats_v%03d" % version for version in (13, 12, 11, 10)
)

# The interface version strings of the older routes, which take them as text.
USER_STATS_INTERFACES = tuple(
    "STEAMUSERSTATS_INTERFACE_VERSION%03d" % version
    for version in (13, 12, 11, 10, 9, 8, 7)
)

# -- friend messages --------------------------------------------------------
#
# ISteamFriends reports an incoming message as a callback only. The flat C API
# gives a callback to a binding that is not C++ through manual dispatch, and
# that needs SDK 1.51 or newer.
#
# Proton has older copies, so message_support() reports this for each library.

FRIENDS_INTERFACES = tuple(
    "SteamFriends%03d" % version for version in (17, 16, 15, 14, 13, 12)
)

# GameConnectedFriendChatMsg_t: k_iSteamFriendsCallbacks (300) + 43, read from
# a live machine.
#
# Identify it by its number and never by its size. PersonaStateChange (304) is
# also 12 bytes, and it also starts with the SteamID of the same friend.
FRIEND_CHAT_MESSAGE = 343
FRIEND_CHAT_MESSAGE_BYTES = 12

# EChatEntryType. Steam reports "the friend is at the keyboard" through the
# same callback as the message. Without the entry type, the bar thus flashes
# two times for each message.
CHAT_ENTRY_CHAT_MSG = 1
CHAT_ENTRY_TYPING = 2

# PersonaStateChange_t: k_iSteamFriendsCallbacks (300) + 4.
#
# It has the same shape as a chat message, a SteamID and an int, so the number
# is the only way to separate the two.
#
# The int is a bit field of EPersonaChange. k_EPersonaChangeComeOnline is a
# change from offline to online and nothing else. The other bits are avatars,
# nicknames, rich presence and a dozen more, and each arrives often.
#
# Not k_EPersonaChangeStatus (0x0002), which is away or busy back to online.
# Not k_EPersonaChangeGamePlayed (0x0010), which is a friend that starts a
# game. A person who does both sets both bits in one callback.
PERSONA_STATE_CHANGE = 304
PERSONA_STATE_CHANGE_BYTES = 12
PERSONA_CHANGE_CAME_ONLINE = 0x0004

# EFriendRelationship. Steam sends a persona update for each person that the
# client knows, and that includes the players in the current game. This module
# thus asks whether a person is a friend.
FRIEND_RELATIONSHIP_FRIEND = 3

MANUAL_DISPATCH_INIT = "SteamAPI_ManualDispatch_Init"
LISTEN_FOR_MESSAGES = "SteamAPI_ISteamFriends_SetListenForFriendsMessages"
GET_FRIEND_MESSAGE = "SteamAPI_ISteamFriends_GetFriendMessage"
GET_RELATIONSHIP = "SteamAPI_ISteamFriends_GetFriendRelationship"


class CallbackMsg(ctypes.Structure):
    """CallbackMsg_t, in the form that manual dispatch writes."""

    _fields_ = [("user", ctypes.c_int32),
                ("callback", ctypes.c_int32),
                ("param", ctypes.POINTER(ctypes.c_uint8)),
                ("param_size", ctypes.c_int32)]


def message_support(library):
    """Returns a dictionary of findings, or {"error": ...}. It loads nothing."""
    try:
        symbols = elf.exported_symbols(library)
    except (OSError, elf.ElfError) as exc:
        return {"error": str(exc)}
    return {
        "manual_dispatch": MANUAL_DISPATCH_INIT in symbols,
        "listen": LISTEN_FOR_MESSAGES in symbols,
        "read_message": GET_FRIEND_MESSAGE in symbols,
        "relationship": GET_RELATIONSHIP in symbols,
        "accessors": versioned_accessors(symbols, "Friends"),
        "find_or_create": "SteamInternal_FindOrCreateUserInterface" in symbols,
        "via_client": "SteamAPI_ISteamClient_GetISteamFriends" in symbols,
    }


def usable_for_messages(support):
    """Returns whether these findings make a library that can receive chat."""
    return bool(usable_for_friends(support) and support["listen"])


def usable_for_friends(support):
    """Returns whether the library can receive "a friend came online".

    A persona change arrives without a request. The app does not ask Steam to
    forward one.

    This thus needs neither SetListenForFriendsMessages nor the reader for chat
    entries, and it operates on a library that has neither.
    """
    if support.get("error"):
        return False
    reachable = bool(support["accessors"] or support["find_or_create"]
                     or support["via_client"])
    return bool(support["manual_dispatch"] and reachable)


_ACCESSOR_RE = re.compile(r"^SteamAPI_Steam(\w+)_v(\d+)$")


def versioned_accessors(symbols, interface):
    """Returns the flat accessors of one interface, the newest version first."""
    found = []
    for symbol in symbols:
        match = _ACCESSOR_RE.match(symbol)
        if match and match.group(1) == interface:
            found.append((int(match.group(2)), symbol))
    return [name for _version, name in sorted(found, reverse=True)]


def user_stats_accessors(symbols):
    return versioned_accessors(symbols, "UserStats")


def interesting_symbols(symbols):
    """Returns the values worth a report when a library does not answer."""
    return sorted(
        symbol for symbol in symbols
        if ("UserStats" in symbol or "SteamClient" in symbol
            or "FindOrCreate" in symbol or "GetHSteam" in symbol
            or symbol.startswith("SteamAPI_Init"))
    )

# Where a libsteam_api.so is inside a Steam library directory.
#
# steamrt32 and steamrt64 belong to the Steam client, so they are on each
# machine with Steam. The copies inside games depend on the installed games,
# and a new Proton has none.
CLIENT_GLOBS = (
    "steamrt64/libsteam_api.so",
    "steamrt32/libsteam_api.so",
    "linux64/libsteam_api.so",
)
GAME_GLOBS = (
    "steamapps/common/*/libsteam_api.so",
    "steamapps/common/*/*/libsteam_api.so",
    "steamapps/common/*/*/*/libsteam_api.so",
    "steamapps/common/*/*/*/*/libsteam_api.so",
    "steamapps/common/*/*/*/*/*/libsteam_api.so",
)
LIBRARY_GLOBS = CLIENT_GLOBS + GAME_GLOBS

# A game has a 32-bit copy and a 64-bit copy in two directories beside each
# other. The first match in alphabetical order is thus often the wrong
# architecture.
WANTED_ELF_CLASS = (elf.ELFCLASS64 if sys.maxsize > 2 ** 32
                    else elf.ELFCLASS32)


def _as_pointer(value):
    """Accepts an address or a pointer that is already in a c_void_p.

    A second c_void_p around a c_void_p raises an exception.
    """
    if isinstance(value, ctypes.c_void_p):
        return value
    return ctypes.c_void_p(value)


def _optional(lib, name):
    return getattr(lib, name, None)


def _call_accessor(lib, name):
    if not hasattr(lib, name):
        return None
    accessor = getattr(lib, name)
    accessor.restype = ctypes.c_void_p
    accessor.argtypes = []
    return accessor() or None


def library_roots():
    """Returns each Steam library directory: the installation and the drives.

    A game on an SD card or on a second drive is in a directory that Steam
    records in libraryfolders.vdf. It is not under the installation directory.
    """
    root = steam_root()
    if root is None:
        return []

    roots = [root]
    try:
        with open(os.path.join(root, "steamapps", "libraryfolders.vdf"),
                  "r", errors="replace") as handle:
            text = handle.read()
    except OSError:
        return roots

    for match in re.finditer(r'"path"\s+"([^"]+)"', text):
        path = match.group(1).replace("\\\\", "/")
        if os.path.isdir(path) and path not in roots:
            roots.append(path)
    return roots


def find_libraries():
    """Returns each libsteam_api.so in each Steam library directory.

    The copies of the client come first, then the copies in games. Each group is
    in alphabetical order.

    The copy of the client is always present and always current. A copy inside a
    game can be years old. The copy in Proton 9 is older than SDK 1.51 and thus
    cannot receive a friend message.
    """
    found = {}
    for root in library_roots():
        for pattern in LIBRARY_GLOBS:
            rank = 0 if pattern in CLIENT_GLOBS else 1
            for match in glob.glob(os.path.join(root, pattern)):
                found.setdefault(match, rank)
    return [path for _rank, path in
            sorted((rank, path) for path, rank in found.items())]


class SteamworksError(RuntimeError):
    """Steam, the library or the app ID is not available."""


def steam_root():
    for candidate in STEAM_ROOTS:
        path = os.path.expanduser(candidate)
        if os.path.isdir(path):
            return path
    return None


def find_library(explicit=None):
    """Finds a libsteam_api.so, from Steam or from an installed game."""
    if explicit and explicit != "auto":
        if not os.path.isfile(explicit):
            raise SteamworksError("no library at %s" % explicit)
        found = elf.elf_class(explicit)
        if found is not None and found != WANTED_ELF_CLASS:
            raise SteamworksError(
                "%s is %s, this Python needs %s"
                % (explicit, elf.class_name(found),
                   elf.class_name(WANTED_ELF_CLASS)))
        return explicit

    root = steam_root()
    if root is None:
        raise SteamworksError("no Steam directory found")

    candidates = find_libraries()
    if not candidates:
        raise SteamworksError(
            "no libsteam_api.so under %s - install a game that ships it, or "
            "set STEAM_LIBRARY to one" % root)

    usable = [path for path in candidates
              if elf.elf_class(path) == WANTED_ELF_CLASS]
    if not usable:
        raise SteamworksError(
            "found %d libsteam_api.so, none of them %s like this Python "
            "(e.g. %s)" % (len(candidates), elf.class_name(WANTED_ELF_CLASS),
                           candidates[0]))
    return usable[0]


# -- the game that runs now ------------------------------------------------


def _app_id_from_registry():
    """Reads the running app from the registry file of Steam."""
    root = steam_root()
    candidates = []
    if root:
        candidates.append(os.path.join(os.path.dirname(root), "registry.vdf"))
        candidates.append(os.path.join(root, "registry.vdf"))
    candidates.append(os.path.expanduser("~/.steam/registry.vdf"))

    for path in candidates:
        try:
            with open(path, "r", errors="replace") as handle:
                text = handle.read()
        except OSError:
            continue
        match = re.search(r'"RunningAppID"\s+"(\d+)"', text)
        if match:
            app_id = int(match.group(1))
            if app_id:
                return app_id
    return None


def _app_id_from_processes():
    """Reads SteamAppId from the environment of a game. It costs more."""
    for entry in glob.glob("/proc/[0-9]*/environ"):
        try:
            with open(entry, "rb") as handle:
                environ = handle.read()
        except OSError:
            continue        # not ours, or gone again
        # This costs less than a split of a Proton environment block first.
        if b"SteamAppId=" not in environ:
            continue
        for variable in environ.split(b"\0"):
            if variable.startswith(b"SteamAppId="):
                value = variable.split(b"=", 1)[1].decode("ascii", "replace")
                if value.isdigit() and int(value):
                    return int(value)
    return None


APP_ID_SOURCES = (
    ("registry.vdf", _app_id_from_registry),
    ("process env", _app_id_from_processes),
)


def app_id_sources():
    """Returns [(label, app id or None)] from each source, for --steam-check.

    It always asks each source. running_app_id() does not.
    """
    return [(label, lookup()) for label, lookup in APP_ID_SOURCES]


def running_app_id(scan_processes=True):
    """Returns the app ID of the game that runs now, or None.

    The registry is one read of a small file. The scan of the processes costs
    much more, so a caller that asks once each second can omit it.

    On some machines the registry never names the running app, and the scan is
    the only source that finds it. None with scan_processes=False thus means
    "this did not look" and not "no game runs".
    """
    app_id = _app_id_from_registry()
    if app_id or not scan_processes:
        return app_id
    return _app_id_from_processes()


# -- the API itself ---------------------------------------------------------


class UserStats:
    """A ctypes wrapper around ISteamUserStats for one app."""

    def __init__(self, app_id, library, route, manual_dispatch=False):
        self.app_id = int(app_id)
        self.library_path = find_library(library)
        # "accessor:NAME", "userinterface:VERSION" or "client:VERSION". It is
        # necessary. See "how to select a route safely" at the end of this file.
        self.route = route
        # Only a friend message needs manual dispatch. The achievement poll
        # uses the standard dispatch, which costs less. This is fixed for the
        # full session.
        self.manual_dispatch = manual_dispatch
        self._lib = None
        self._iface = None
        self._pipe = None
        self._pending = []
        self._symbol_cache = None

    def open(self):
        # Steamworks reads the app ID from the environment at its start.
        os.environ["SteamAppId"] = str(self.app_id)
        os.environ["SteamGameId"] = str(self.app_id)

        try:
            lib = ctypes.CDLL(self.library_path)
        except OSError as exc:
            raise SteamworksError("cannot load %s: %s" % (self.library_path, exc))

        init, init_name = None, None
        for name in ("SteamAPI_InitFlat", "SteamAPI_Init"):
            if hasattr(lib, name):
                init, init_name = getattr(lib, name), name
                break
        if init is None:
            raise SteamworksError("%s exports no SteamAPI_Init"
                                  % self.library_path)

        if init_name == "SteamAPI_InitFlat":
            init.restype = ctypes.c_int
            init.argtypes = [ctypes.c_char_p]
            message = ctypes.create_string_buffer(1024)
            ok = init(message) == 0
            detail = message.value.decode("utf-8", "replace")
        else:
            init.restype = ctypes.c_bool
            init.argtypes = []
            ok = bool(init())
            detail = ""

        if not ok:
            raise SteamworksError(
                "SteamAPI_Init failed for app %d%s - is Steam running, and do "
                "you own this app?" % (self.app_id, ": " + detail if detail else ""))

        try:
            iface = self._resolve_user_stats(lib)
        except SteamworksError:
            self._shutdown(lib)
            raise

        self._bind(lib)
        if self.manual_dispatch:
            self._start_manual_dispatch(lib)
        self._lib = lib
        self._iface = _as_pointer(iface)
        # No step after this needs the symbol table. Without this, the table
        # uses some hundred KiB for the full run of the game.
        self._symbol_cache = None

        if self._request_stats is None:
            LOG.debug("%s does not export RequestCurrentStats - this SDK has "
                      "the stats ready after init", self.library_path)
        elif not self._request_stats(self._iface):
            LOG.warning("RequestCurrentStats returned false; stats may be stale")
        self.wait_for_stats()

    @property
    def library(self):
        return self._lib

    def _symbols(self):
        if self._symbol_cache is None:
            try:
                self._symbol_cache = elf.exported_symbols(self.library_path)
            except (OSError, elf.ElfError) as exc:
                LOG.debug("cannot read symbols from %s: %s",
                          self.library_path, exc)
                self._symbol_cache = set()
        return self._symbol_cache

    def _resolve_user_stats(self, lib):
        """Uses the route of this instance, and no other route."""
        kind, _, detail = self.route.partition(":")
        if kind == "accessor":
            iface = _call_accessor(lib, detail)
        elif kind == "userinterface":
            iface = self._via_user_interface(lib, detail)
        elif kind == "client":
            iface = self._via_steam_client(lib, detail)
        else:
            raise SteamworksError("unknown route %r" % self.route)

        if not iface:
            raise SteamworksError(
                "route %s did not resolve ISteamUserStats in %s. Exported "
                "symbols that looked relevant: %s"
                % (self.route, self.library_path,
                   ", ".join(interesting_symbols(self._symbols())) or "none"))
        return _as_pointer(iface)

    def _via_user_interface(self, lib, version):
        name = "SteamInternal_FindOrCreateUserInterface"
        if not hasattr(lib, name) or not hasattr(lib, "SteamAPI_GetHSteamUser"):
            return None

        get_user = lib.SteamAPI_GetHSteamUser
        get_user.restype = ctypes.c_int32
        get_user.argtypes = []

        create = getattr(lib, name)
        create.restype = ctypes.c_void_p
        create.argtypes = [ctypes.c_int32, ctypes.c_char_p]
        return create(get_user(), version.encode("ascii")) or None

    def _via_steam_client(self, lib, version):
        getter = "SteamAPI_ISteamClient_GetISteamUserStats"
        if not hasattr(lib, getter):
            return None

        client = None
        accessors = versioned_accessors(self._symbols(), "Client")
        for candidate in accessors + ["SteamClient"]:
            client = _call_accessor(lib, candidate)
            if client:
                break
        if not client:
            return None

        for name, restype in (("SteamAPI_GetHSteamUser", ctypes.c_int32),
                              ("SteamAPI_GetHSteamPipe", ctypes.c_int32)):
            if not hasattr(lib, name):
                return None
            function = getattr(lib, name)
            function.restype = restype
            function.argtypes = []

        get_stats = getattr(lib, getter)
        get_stats.restype = ctypes.c_void_p
        get_stats.argtypes = [ctypes.c_void_p, ctypes.c_int32, ctypes.c_int32,
                              ctypes.c_char_p]
        return get_stats(ctypes.c_void_p(client),
                         lib.SteamAPI_GetHSteamUser(),
                         lib.SteamAPI_GetHSteamPipe(),
                         version.encode("ascii")) or None

    @staticmethod
    def _shutdown(lib):
        if hasattr(lib, "SteamAPI_Shutdown"):
            lib.SteamAPI_Shutdown()

    def _bind(self, lib):
        # Different SDK versions have and do not have these two, so an absent
        # one is normal. Bind each one that is present and continue.
        #
        # To refuse a library because of a call that it no longer needs
        # excludes the copy of the Steam client itself.
        self._run_callbacks = _optional(lib, "SteamAPI_RunCallbacks")
        if self._run_callbacks is not None:
            self._run_callbacks.restype = None
            self._run_callbacks.argtypes = []

        self._request_stats = _optional(
            lib, "SteamAPI_ISteamUserStats_RequestCurrentStats")
        if self._request_stats is not None:
            self._request_stats.restype = ctypes.c_bool
            self._request_stats.argtypes = [ctypes.c_void_p]

        self._num = lib.SteamAPI_ISteamUserStats_GetNumAchievements
        self._num.restype = ctypes.c_uint32
        self._num.argtypes = [ctypes.c_void_p]

        self._name = lib.SteamAPI_ISteamUserStats_GetAchievementName
        self._name.restype = ctypes.c_char_p
        self._name.argtypes = [ctypes.c_void_p, ctypes.c_uint32]

        self._get = lib.SteamAPI_ISteamUserStats_GetAchievement
        self._get.restype = ctypes.c_bool
        self._get.argtypes = [ctypes.c_void_p, ctypes.c_char_p,
                              ctypes.POINTER(ctypes.c_bool)]

        self._display = lib.SteamAPI_ISteamUserStats_GetAchievementDisplayAttribute
        self._display.restype = ctypes.c_char_p
        self._display.argtypes = [ctypes.c_void_p, ctypes.c_char_p,
                                  ctypes.c_char_p]

    def wait_for_stats(self, timeout=3.0):
        """Waits for the answer of RequestCurrentStats.

        The call is asynchronous. The schema is available immediately, but each
        unlock state reads as false until Steam answers.

        A baseline that this module takes too early thus makes each old achievement
        look like a new unlock.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.run_callbacks()
            if any(self.achievements().values()):
                return True
            time.sleep(0.1)
        # A game with no unlocked achievement also reaches this point. The
        # watcher thus has its own protection against a late group.
        LOG.debug("no unlocked achievement seen within %.1fs", timeout)
        return False

    MAX_PENDING_CALLBACKS = 256

    def _start_manual_dispatch(self, lib):
        """Changes this session to manual callback dispatch.

        This must occur before a call to SteamAPI_RunCallbacks. Steam keeps the
        first mode and refuses a change after that.
        """
        init = _optional(lib, MANUAL_DISPATCH_INIT)
        if init is None:
            raise SteamworksError(
                "%s predates manual callback dispatch (SDK 1.51), so callbacks "
                "cannot be delivered to a ctypes binding at all"
                % self.library_path)

        get_pipe = lib.SteamAPI_GetHSteamPipe
        get_pipe.restype = ctypes.c_int32
        get_pipe.argtypes = []
        self._pipe = get_pipe()
        init()

    def run_callbacks(self):
        """Runs the connection, with the dispatch method of this session."""
        if self._lib is None:
            return
        if self._pipe is not None:
            self._pump_manual()
        elif self._run_callbacks is not None:
            self._run_callbacks()

    def _pump_manual(self):
        lib = self._lib
        lib.SteamAPI_ManualDispatch_RunFrame(ctypes.c_int32(self._pipe))

        get_next = lib.SteamAPI_ManualDispatch_GetNextCallback
        get_next.restype = ctypes.c_bool
        get_next.argtypes = [ctypes.c_int32, ctypes.POINTER(CallbackMsg)]

        message = CallbackMsg()
        while get_next(ctypes.c_int32(self._pipe), ctypes.byref(message)):
            size = max(0, message.param_size)
            payload = bytes(bytearray(message.param[:size])) if size else b""
            self._pending.append((message.callback, payload))
            lib.SteamAPI_ManualDispatch_FreeLastCallback(
                ctypes.c_int32(self._pipe))

        # Manual dispatch delivers each callback, and not only the ones that a
        # caller asked for. A queue that nobody reads must thus have a limit.
        if len(self._pending) > self.MAX_PENDING_CALLBACKS:
            self._pending = self._pending[-self.MAX_PENDING_CALLBACKS:]

    def take_callbacks(self):
        """Returns each callback after the last call, as (id, payload)."""
        pending, self._pending = self._pending, []
        return pending

    def achievements(self):
        """Returns {api name: unlocked} for each achievement of the app."""
        if self._iface is None:
            return {}
        result = {}
        for index in range(self._num(self._iface)):
            name = self._name(self._iface, index)
            if not name:
                continue
            unlocked = ctypes.c_bool(False)
            if self._get(self._iface, name, ctypes.byref(unlocked)):
                result[name.decode("utf-8", "replace")] = bool(unlocked.value)
        return result

    def display_name(self, api_name):
        if self._iface is None:
            return api_name
        value = self._display(self._iface, api_name.encode("utf-8"), b"name")
        return value.decode("utf-8", "replace") if value else api_name

    def close(self):
        if self._lib is not None:
            self._shutdown(self._lib)
        self._lib = None
        self._iface = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *_exc):
        self.close()


# At the start of a game, Steam delivers the persona state of each person
# that it knows. An online friend arrives with the "came online" bit set.
#
# Without these two values, the bar thus reports the full friends list at each
# start of a game.
#
# A group larger than the threshold is that delivery and not a group of people.
# The settling time covers a delivery that arrives in small groups.
FRIEND_ONLINE_SETTLE = 20.0     # seconds after attaching
FRIEND_ONLINE_FLOOD = 3         # friends in one poll


class FriendListener:
    """Receives friend activity during a game: messages and arrivals.

    It uses a UserStats session that is already open. That is the same client
    connection and the same app, so it registers nothing more with Steam.

    The caller must open that session with manual_dispatch=True.
    """

    def __init__(self, stats, want_messages=True, now=None):
        self.stats = stats
        self.lib = stats.library
        self.want_messages = want_messages
        self.route = None
        self._friends = None
        self._get_message = None
        self._relationship = None
        self._settled_at = (time.monotonic() if now is None else now) \
            + FRIEND_ONLINE_SETTLE

    def open(self):
        if self.stats._pipe is None:
            raise SteamworksError(
                "this session dispatches callbacks the standard way, which "
                "hands them to nobody - open it with manual_dispatch=True")

        # Read the symbols here. open() deliberately discards the cache of
        # the session, so that a session of a full game does not hold the
        # symbol table.
        try:
            symbols = elf.exported_symbols(self.stats.library_path)
        except (OSError, elf.ElfError) as exc:
            raise SteamworksError("cannot read symbols from %s: %s"
                                  % (self.stats.library_path, exc))

        if self.want_messages and LISTEN_FOR_MESSAGES not in symbols:
            raise SteamworksError("%s exports no %s"
                                  % (self.stats.library_path,
                                     LISTEN_FOR_MESSAGES))

        self._friends = self._resolve_friends(symbols)
        if self._friends is None:
            raise SteamworksError("%s offers no way to reach ISteamFriends"
                                  % self.stats.library_path)

        if self.want_messages:
            listen = getattr(self.lib, LISTEN_FOR_MESSAGES)
            listen.restype = ctypes.c_bool
            listen.argtypes = [ctypes.c_void_p, ctypes.c_bool]
            if not listen(self._friends, True):
                raise SteamworksError(
                    "SetListenForFriendsMessages was refused - the Steam "
                    "client declined to forward chat to this app")
            self._bind_reader(symbols)

        self._bind_relationship(symbols)
        LOG.info("listening for friend activity via %s", self.route)

    def _bind_relationship(self, symbols):
        """Binds GetFriendRelationship, which reports who is a friend."""
        if GET_RELATIONSHIP not in symbols:
            LOG.warning("%s cannot tell friends from strangers, so anyone "
                        "coming online may flash the bar",
                        self.stats.library_path)
            return
        reader = getattr(self.lib, GET_RELATIONSHIP)
        reader.restype = ctypes.c_int32
        reader.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
        self._relationship = reader

    def is_friend(self, steam_id):
        """Returns whether Steam calls this person a friend. True with no answer."""
        if self._relationship is None:
            return True
        return self._relationship(self._friends,
                                  ctypes.c_uint64(steam_id)) == \
            FRIEND_RELATIONSHIP_FRIEND

    def _bind_reader(self, symbols):
        """Binds GetFriendMessage, which separates a message from a typing notice."""
        if GET_FRIEND_MESSAGE not in symbols:
            LOG.warning("%s cannot read chat entries, so the bar will also "
                        "flash while a friend is still typing",
                        self.stats.library_path)
            return
        reader = getattr(self.lib, GET_FRIEND_MESSAGE)
        reader.restype = ctypes.c_int32
        reader.argtypes = [ctypes.c_void_p, ctypes.c_uint64, ctypes.c_int32,
                           ctypes.c_void_p, ctypes.c_int32,
                           ctypes.POINTER(ctypes.c_int32)]
        self._get_message = reader

    MESSAGE_BUFFER = 4096

    def entry_type(self, steam_id, message_id):
        """Returns whether this entry is a message, a typing notice, or other.

        It returns None when the library cannot answer. The caller then accepts
        each entry.

        The read of the type also reads the text of the message. This function
        then discards that text deliberately: it has no place in a system log.
        """
        if self._get_message is None:
            return None
        buffer = ctypes.create_string_buffer(self.MESSAGE_BUFFER)
        entry = ctypes.c_int32(0)
        self._get_message(self._friends, ctypes.c_uint64(steam_id),
                          ctypes.c_int32(message_id), buffer,
                          ctypes.c_int32(self.MESSAGE_BUFFER),
                          ctypes.byref(entry))
        return entry.value

    def _resolve_friends(self, symbols):
        """Returns an ISteamFriends pointer, through the route of this library."""
        for name in versioned_accessors(symbols, "Friends"):
            iface = _call_accessor(self.lib, name)
            if iface:
                self.route = "accessor:%s" % name
                return _as_pointer(iface)

        name = "SteamInternal_FindOrCreateUserInterface"
        if hasattr(self.lib, name) and hasattr(self.lib,
                                               "SteamAPI_GetHSteamUser"):
            get_user = self.lib.SteamAPI_GetHSteamUser
            get_user.restype = ctypes.c_int32
            get_user.argtypes = []
            create = getattr(self.lib, name)
            create.restype = ctypes.c_void_p
            create.argtypes = [ctypes.c_int32, ctypes.c_char_p]
            user = get_user()
            for version in FRIENDS_INTERFACES:
                iface = create(user, version.encode("ascii"))
                if iface:
                    self.route = "userinterface:%s" % version
                    return _as_pointer(iface)
        return None

    def poll(self, now=None):
        """Returns the events after the last call: (messages, came online).

        Both in one pass: the first caller removes the callbacks, so two
        readers would each get half of them.

        A message is a (steam id, message id) pair. Only a *received* message
        makes that callback, so the bar does not flash while a person types.
        """
        now = time.monotonic() if now is None else now
        messages, online = [], []

        for number, payload in self.callbacks():
            if number == FRIEND_CHAT_MESSAGE:
                if len(payload) < FRIEND_CHAT_MESSAGE_BYTES:
                    LOG.debug("short chat callback: %d bytes", len(payload))
                    continue
                steam_id, message_id = struct.unpack(
                    "<QI", payload[:FRIEND_CHAT_MESSAGE_BYTES])
                entry = self.entry_type(steam_id, message_id)
                if entry is not None and entry != CHAT_ENTRY_CHAT_MSG:
                    LOG.debug("chat entry type %d is not a message", entry)
                    continue
                messages.append((steam_id, message_id))

            elif number == PERSONA_STATE_CHANGE:
                if len(payload) < PERSONA_STATE_CHANGE_BYTES:
                    LOG.debug("short persona callback: %d bytes", len(payload))
                    continue
                steam_id, flags = struct.unpack(
                    "<QI", payload[:PERSONA_STATE_CHANGE_BYTES])
                if not flags & PERSONA_CHANGE_CAME_ONLINE:
                    continue
                if not self.is_friend(steam_id):
                    continue
                online.append(steam_id)

        return messages, self._settled(online, now)

    def _settled(self, online, now):
        """Drop the burst Steam sends when it first tells us who is around."""
        if not online:
            return online
        if now < self._settled_at:
            LOG.debug("ignoring %d friend(s) online while still settling",
                      len(online))
            return []
        if len(online) > FRIEND_ONLINE_FLOOD:
            LOG.info("%d friends appeared at once - taking that as Steam "
                     "catching up rather than people arriving", len(online))
            return []
        return online

    def callbacks(self):
        """Returns each callback after the last call, as (id, payload).

        It filters nothing, and that is deliberate. The number of a chat message
        is different between SDK generations, so --probe-messages can read it
        from a live machine.

        The session runs the connection, because the session owns the
        dispatcher.
        """
        self.stats.run_callbacks()
        return self.stats.take_callbacks()

    def close(self):
        if self._friends is not None and hasattr(self.lib,
                                                 LISTEN_FOR_MESSAGES):
            try:
                getattr(self.lib, LISTEN_FOR_MESSAGES)(self._friends, False)
            except Exception as exc:                # noqa: BLE001
                LOG.debug("could not stop listening: %s", exc)
        self._friends = None


class AchievementWatcher:
    """Reports achievements that flip from locked to unlocked."""

    # More than this between two polls is Steam delivering state, not the
    # player earning them a tenth of a second apart.
    FLOOD_THRESHOLD = 3

    def __init__(self, stats, flood_threshold=FLOOD_THRESHOLD):
        self.stats = stats
        self.flood_threshold = flood_threshold
        self.previous = None

    def poll(self):
        """Returns the api names that unlocked after the previous call."""
        self.stats.run_callbacks()
        current = self.stats.achievements()
        if not current:
            return []

        if self.previous is None:
            # The first read: each unlocked achievement is already old.
            self.previous = current
            LOG.info("tracking %d achievements, %d already unlocked",
                     len(current), sum(1 for value in current.values() if value))
            return []

        fresh = [name for name, unlocked in current.items()
                 if unlocked and not self.previous.get(name, False)]
        self.previous = current

        if len(fresh) > self.flood_threshold:
            LOG.info("%d achievements appeared at once - taking that as a late "
                     "stats load rather than a burst of unlocks", len(fresh))
            return []
        return fresh


# -- picking a route safely -------------------------------------------------
#
# The flat SteamAPI_ISteamUserStats_* wrappers are compiled against one
# interface version. A request for a different version returns a pointer whose
# vtable does not match, and a call through it stops the process. It does not
# return an error.
#
# This module thus tries each route in a child process. The route that survives
# is the answer.


def candidate_routes(library):
    """Returns each route into ISteamUserStats, the most probable first."""
    try:
        symbols = elf.exported_symbols(library)
    except (OSError, elf.ElfError):
        symbols = set()

    routes = ["accessor:%s" % name for name in user_stats_accessors(symbols)]
    if "SteamInternal_FindOrCreateUserInterface" in symbols:
        routes += ["userinterface:%s" % version
                   for version in USER_STATS_INTERFACES]
    if "SteamAPI_ISteamClient_GetISteamUserStats" in symbols:
        routes += ["client:%s" % version for version in USER_STATS_INTERFACES]
    if not routes:
        routes = ["accessor:%s" % name for name in USER_STATS_ACCESSORS]
    return routes


def _suppress_core_dumps():
    """Stops the core dump, because a stop of the process is expected here.

    Without this, each probe of a wrong version gives systemd-coredump a full
    dump of a Python process with steamclient.so mapped. That occurs one time
    at each start of a game.
    """
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except (ImportError, ValueError, OSError) as exc:
        LOG.debug("cannot disable core dumps in the probe child: %s", exc)


def _run_in_child(target, timeout=15.0):
    """Forks and runs target(write_fd). Returns ("ok"|"crashed", message)."""
    import select as _select
    import signal as _signal

    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(read_fd)
        _suppress_core_dumps()
        try:
            target(write_fd)
        finally:
            # _exit and not exit: it runs no atexit handler, and it does not
            # write the copies of the buffers of the parent process.
            os._exit(0)

    os.close(write_fd)
    message = b""
    deadline = time.monotonic() + timeout
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                os.kill(pid, _signal.SIGKILL)
                break
            readable, _, _ = _select.select([read_fd], [], [], remaining)
            if not readable:
                continue
            chunk = os.read(read_fd, 4096)
            if not chunk:
                break
            message += chunk
    finally:
        os.close(read_fd)

    _pid, status = os.waitpid(pid, 0)

    if os.WIFSIGNALED(status):
        number = os.WTERMSIG(status)
        name = _signal.Signals(number).name if number in iter(_signal.Signals) \
            else str(number)
        return "crashed", name
    return "ok", message.decode("utf-8", "replace")


def probe_route(app_id, library, route, timeout=15.0):
    """Tries one route in a child. Returns (status, detail)."""
    def attempt(write_fd):
        try:
            stats = UserStats(app_id, library, route=route)
            stats.open()
            os.write(write_fd, b"OK %d" % len(stats.achievements()))
        except BaseException as exc:                # noqa: BLE001 (report all)
            os.write(write_fd,
                     ("ERR %s" % exc).encode("utf-8", "replace")[:400])

    status, text = _run_in_child(attempt, timeout)
    if status == "crashed":
        return status, text
    if text.startswith("OK "):
        return "ok", text[3:].strip()
    return "failed", text[4:].strip() if text.startswith("ERR ") else "no answer"


_ROUTE_CACHE = {}


def select_route(app_id, library, reporter=None):
    """Finds a route that operates. Returns (route, achievements).

    The library and not the app decides which route operates. A route that
    operated before thus goes first. A later start of a game then costs one
    fork and not fifteen.
    """
    routes = candidate_routes(library)
    known = _ROUTE_CACHE.get(library)
    if known in routes:
        routes = [known] + [route for route in routes if route != known]

    for route in routes:
        status, detail = probe_route(app_id, library, route)
        if reporter is not None:
            reporter(route, status, detail)
        if status == "ok":
            _ROUTE_CACHE[library] = route
            return route, int(detail or 0)
    return None, 0
