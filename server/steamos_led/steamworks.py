"""Realtime achievement detection through Valve's local Steamworks API.

No API key, no network: the Steam client already knows the achievement state
and `libsteam_api.so` is the local door to it. The catch is that Steamworks is
a *game-side* API - it must be initialised as a specific app and cannot say
which game is running, so we find the running app ID ourselves first. Only the
flat C API is used, so ctypes is enough and no callback structs are marshalled.
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

# The accessor's version suffix depends on the SDK a library was built from, so
# symbols are read rather than guessed. Fallback for unreadable symbol tables.
USER_STATS_ACCESSORS = tuple(
    "SteamAPI_SteamUserStats_v%03d" % version for version in (13, 12, 11, 10)
)

# Interface version strings for the older routes, which take them as text.
USER_STATS_INTERFACES = tuple(
    "STEAMUSERSTATS_INTERFACE_VERSION%03d" % version
    for version in (13, 12, 11, 10, 9, 8, 7)
)

# -- friend messages --------------------------------------------------------
#
# ISteamFriends reports incoming chat only as callbacks, and the flat C API's
# only way to hand those to a non-C++ binding is manual dispatch (SDK 1.51+).
# Proton ships older copies on some machines, so whether this works at all is a
# property of the borrowed library - message_support() reports it.

FRIENDS_INTERFACES = tuple(
    "SteamFriends%03d" % version for version in (17, 16, 15, 14, 13, 12)
)

# GameConnectedFriendChatMsg_t: k_iSteamFriendsCallbacks (300) + 43, read off a
# live machine. Payload is 8 bytes of CSteamID plus a message counter. Identify
# it by this number, never by size: PersonaStateChange (304) is also 12 bytes
# and also starts with the same friend's SteamID.
FRIEND_CHAT_MESSAGE = 343
FRIEND_CHAT_MESSAGE_BYTES = 12

# EChatEntryType. Steam announces "they are typing" through the same callback as
# the message, so without the entry type the bar flashes twice per message.
CHAT_ENTRY_CHAT_MSG = 1
CHAT_ENTRY_TYPING = 2

MANUAL_DISPATCH_INIT = "SteamAPI_ManualDispatch_Init"
LISTEN_FOR_MESSAGES = "SteamAPI_ISteamFriends_SetListenForFriendsMessages"
GET_FRIEND_MESSAGE = "SteamAPI_ISteamFriends_GetFriendMessage"


class CallbackMsg(ctypes.Structure):
    """CallbackMsg_t, as manual dispatch fills it in."""

    _fields_ = [("user", ctypes.c_int32),
                ("callback", ctypes.c_int32),
                ("param", ctypes.POINTER(ctypes.c_uint8)),
                ("param_size", ctypes.c_int32)]


def message_support(library):
    """What a library offers for friend messages. Reads symbols, loads nothing.

    Returns a dict of findings, or {"error": ...} if the symbols are unreadable.
    """
    try:
        symbols = elf.exported_symbols(library)
    except (OSError, elf.ElfError) as exc:
        return {"error": str(exc)}
    return {
        "manual_dispatch": MANUAL_DISPATCH_INIT in symbols,
        "listen": LISTEN_FOR_MESSAGES in symbols,
        "read_message": GET_FRIEND_MESSAGE in symbols,
        "accessors": versioned_accessors(symbols, "Friends"),
        "find_or_create": "SteamInternal_FindOrCreateUserInterface" in symbols,
        "via_client": "SteamAPI_ISteamClient_GetISteamFriends" in symbols,
    }


def usable_for_messages(support):
    """Whether these findings add up to a library we could listen with."""
    if support.get("error"):
        return False
    reachable = bool(support["accessors"] or support["find_or_create"]
                     or support["via_client"])
    return bool(support["manual_dispatch"] and support["listen"] and reachable)


_ACCESSOR_RE = re.compile(r"^SteamAPI_Steam(\w+)_v(\d+)$")


def versioned_accessors(symbols, interface):
    """Versioned flat accessors for one interface, newest version first."""
    found = []
    for symbol in symbols:
        match = _ACCESSOR_RE.match(symbol)
        if match and match.group(1) == interface:
            found.append((int(match.group(2)), symbol))
    return [name for _version, name in sorted(found, reverse=True)]


def user_stats_accessors(symbols):
    """Versioned ISteamUserStats accessors a library exports, newest first."""
    return versioned_accessors(symbols, "UserStats")


def interesting_symbols(symbols):
    """The subset worth showing when a library will not cooperate."""
    return sorted(
        symbol for symbol in symbols
        if ("UserStats" in symbol or "SteamClient" in symbol
            or "FindOrCreate" in symbol or "GetHSteam" in symbol
            or symbol.startswith("SteamAPI_Init"))
    )

# Where a libsteam_api.so turns up, relative to a Steam library folder.
# steamrt32/64 belong to the Steam client itself, so they exist on every machine
# with Steam; game copies depend on what happens to be installed, and newer
# Proton ships none at all.
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

# Games ship both a 32- and a 64-bit copy in sibling directories, so the first
# alphabetical match is routinely the wrong architecture.
WANTED_ELF_CLASS = (elf.ELFCLASS64 if sys.maxsize > 2 ** 32
                    else elf.ELFCLASS32)


def _as_pointer(value):
    """Accept a raw address or an already-wrapped pointer.

    Re-wrapping a c_void_p raises instead of doing nothing, and the resolver
    routes return both forms, so normalise rather than assume.
    """
    if isinstance(value, ctypes.c_void_p):
        return value
    return ctypes.c_void_p(value)


def _optional(lib, name):
    """A bound symbol, or None if the library does not have it."""
    return getattr(lib, name, None)


def _call_accessor(lib, name):
    """Call a zero-argument accessor that hands back an interface pointer."""
    if not hasattr(lib, name):
        return None
    accessor = getattr(lib, name)
    accessor.restype = ctypes.c_void_p
    accessor.argtypes = []
    return accessor() or None


def library_roots():
    """Every Steam library folder: the install itself, plus extra drives.

    Games on an SD card or second drive live in folders Steam records in
    libraryfolders.vdf, not under the install directory.
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
    """Every libsteam_api.so across all Steam library folders.

    The client's own copies come first, then games, alphabetically within each
    group: the client's copy is always present and kept current, while the ones
    inside games can be years old (Proton 9's predates SDK 1.51 and cannot
    receive friend messages at all).
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
    """Raised when Steam, the library or the app ID cannot be reached."""


def steam_root():
    for candidate in STEAM_ROOTS:
        path = os.path.expanduser(candidate)
        if os.path.isdir(path):
            return path
    return None


def find_library(explicit=None):
    """Locate a libsteam_api.so.

    Part of the Steamworks SDK, so never redistributed here - Steam or one of
    the installed games lends us its copy.
    """
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


# -- which game is running -------------------------------------------------


def _app_id_from_registry():
    """Steam records the running app in its own registry file."""
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
    """Steam launches games with SteamAppId in their environment.

    Reads every process the user owns, so far more expensive than the registry.
    """
    for entry in glob.glob("/proc/[0-9]*/environ"):
        try:
            with open(entry, "rb") as handle:
                environ = handle.read()
        except OSError:
            continue        # not ours, or gone again
        # Cheaper than splitting a Proton-sized environment block first.
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
    """[(label, app id or None)] - what every source says, for diagnostics.

    Unlike running_app_id() this always asks all of them: --steam-check should
    show where the answer did and did not come from.
    """
    return [(label, lookup()) for label, lookup in APP_ID_SOURCES]


def running_app_id(scan_processes=True):
    """The app ID of the game currently running, or None.

    The registry is one small file read; the process scan is much more
    expensive, so a caller polling once a second may skip it. Careful: on some
    machines the registry never names the running app and the scan is the only
    source that finds it, so None with scan_processes=False means "did not
    look", not "no game".
    """
    app_id = _app_id_from_registry()
    if app_id or not scan_processes:
        return app_id
    return _app_id_from_processes()


# -- the API itself ---------------------------------------------------------


class UserStats:
    """A thin ctypes wrapper around ISteamUserStats for one app."""

    def __init__(self, app_id, library, route, manual_dispatch=False):
        self.app_id = int(app_id)
        self.library_path = find_library(library)
        # "accessor:NAME", "userinterface:VERSION" or "client:VERSION", and
        # mandatory: the wrong interface version segfaults instead of failing,
        # so the choice belongs to select_route(), which probes in children.
        self.route = route
        # Steam fixes the callback dispatch mode the first time either is used,
        # so it must be decided before open() pumps anything. Manual dispatch is
        # the only way a ctypes binding sees callbacks; achievement polling
        # alone is happy with the cheaper standard dispatch.
        self.manual_dispatch = manual_dispatch
        self._lib = None
        self._iface = None
        self._pipe = None
        self._pending = []
        self._symbol_cache = None

    def open(self):
        # Steamworks reads the app ID from the environment at init time.
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
        # Nothing past here needs the symbol table, which would otherwise keep
        # a few hundred KiB alive for as long as the game runs.
        self._symbol_cache = None

        if self._request_stats is None:
            LOG.debug("%s does not export RequestCurrentStats - this SDK has "
                      "the stats ready after init", self.library_path)
        elif not self._request_stats(self._iface):
            LOG.warning("RequestCurrentStats returned false; stats may be stale")
        self.wait_for_stats()

    @property
    def library(self):
        """The loaded CDLL, for anything else that wants this same session."""
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
        """Take this instance's route, and only that one - no fallbacks.

        Trying others in-process is what select_route() exists to avoid: the
        wrong interface version does not raise, it segfaults.
        """
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
        # These two came and went with the SDK, so absent is normal: bind them
        # if present and carry on otherwise. Refusing a library over a call it
        # no longer needs would rule out the Steam client's own copy.
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
        """Give RequestCurrentStats time to answer.

        It is asynchronous: the schema is there at once, but every unlock state
        reads as false until Steam replies. A baseline taken too early makes the
        whole back catalogue look like it unlocked a moment later.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.run_callbacks()
            if any(self.achievements().values()):
                return True
            time.sleep(0.1)
        # A game with genuinely nothing unlocked lands here too - hence the
        # watcher's own guard against a late flood.
        LOG.debug("no unlocked achievement seen within %.1fs", timeout)
        return False

    MAX_PENDING_CALLBACKS = 256

    def _start_manual_dispatch(self, lib):
        """Switch this session to manual callback dispatch.

        Must happen before anything calls SteamAPI_RunCallbacks: Steam keeps
        whichever mode is used first and refuses to change afterwards.
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
        """Pump the connection, whichever way this session dispatches."""
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

        # Manual dispatch delivers every callback, not just the ones anyone
        # asked for, so an unread queue must not grow without bound.
        if len(self._pending) > self.MAX_PENDING_CALLBACKS:
            self._pending = self._pending[-self.MAX_PENDING_CALLBACKS:]

    def take_callbacks(self):
        """Everything dispatched since the last call, as (id, payload)."""
        pending, self._pending = self._pending, []
        return pending

    def achievements(self):
        """{api name: unlocked} for every achievement the app defines."""
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


class FriendMessageListener:
    """Receives Steam friend chat messages while a game is running.

    Borrows an already-open UserStats session - same client connection, same
    app - so there is nothing extra to register with Steam. Callbacks arrive
    through manual dispatch, so that session must have been opened with
    manual_dispatch=True; Steam fixes the mode on first use, which is why the
    choice belongs to the session and not to this.
    """

    def __init__(self, stats):
        self.stats = stats
        self.lib = stats.library
        self.route = None
        self._friends = None
        self._get_message = None

    def open(self):
        if self.stats._pipe is None:
            raise SteamworksError(
                "this session dispatches callbacks the standard way, which "
                "hands them to nobody - open it with manual_dispatch=True")

        # Read the symbols here: the session's cache is dropped by open() on
        # purpose, so a game-long session does not sit on the symbol table.
        try:
            symbols = elf.exported_symbols(self.stats.library_path)
        except (OSError, elf.ElfError) as exc:
            raise SteamworksError("cannot read symbols from %s: %s"
                                  % (self.stats.library_path, exc))

        if LISTEN_FOR_MESSAGES not in symbols:
            raise SteamworksError("%s exports no %s"
                                  % (self.stats.library_path,
                                     LISTEN_FOR_MESSAGES))

        self._friends = self._resolve_friends(symbols)
        if self._friends is None:
            raise SteamworksError("%s offers no way to reach ISteamFriends"
                                  % self.stats.library_path)

        listen = getattr(self.lib, LISTEN_FOR_MESSAGES)
        listen.restype = ctypes.c_bool
        listen.argtypes = [ctypes.c_void_p, ctypes.c_bool]
        if not listen(self._friends, True):
            raise SteamworksError(
                "SetListenForFriendsMessages was refused - the Steam client "
                "declined to forward chat to this app")

        self._bind_reader(symbols)
        LOG.info("listening for friend messages via %s", self.route)

    def _bind_reader(self, symbols):
        """Bind GetFriendMessage, which is how a typing notice is told apart."""
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

    # Big enough for any chat message; the text itself is read and dropped.
    MESSAGE_BUFFER = 4096

    def entry_type(self, steam_id, message_id):
        """Whether this entry is a message, someone typing, or something else.

        None when the library cannot tell us, leaving the caller to take every
        entry at face value. Reading the type also reads the message text,
        which is then dropped on purpose - it has no place in a system log.
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
        """An ISteamFriends pointer, by whichever door this library has."""
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

    def messages(self):
        """Friend chat messages received since the last call.

        Returns (steam id, message id) pairs. Only *received* messages produce
        this callback, so the bar does not flash while you type - but a friend
        typing does, which is why the entry type is checked.
        """
        found = []
        for number, payload in self.callbacks():
            if number != FRIEND_CHAT_MESSAGE:
                continue
            if len(payload) < FRIEND_CHAT_MESSAGE_BYTES:
                LOG.debug("short chat callback: %d bytes", len(payload))
                continue
            steam_id, message_id = struct.unpack(
                "<QI", payload[:FRIEND_CHAT_MESSAGE_BYTES])

            entry = self.entry_type(steam_id, message_id)
            if entry is not None and entry != CHAT_ENTRY_CHAT_MSG:
                LOG.debug("chat entry type %d is not a message", entry)
                continue
            found.append((steam_id, message_id))
        return found

    def callbacks(self):
        """Every callback dispatched since the last call, as (id, payload).

        Unfiltered on purpose: which number carries a chat message differs
        between SDK generations, so --probe-messages can read it off a live
        machine. Pumping belongs to the session, which owns the dispatcher.
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
        """Return the api names unlocked since the previous call."""
        self.stats.run_callbacks()
        current = self.stats.achievements()
        if not current:
            return []

        if self.previous is None:
            # First look: everything already earned is old news.
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
# interface version. Fetching a different one hands back a pointer with a
# mismatched vtable, and calling through it segfaults rather than failing.
# Which version a library expects is not discoverable from the outside, so each
# candidate is tried in a child process: a crash costs one fork, and the
# survivor is the answer.


def candidate_routes(library):
    """Every way into ISteamUserStats worth trying, most likely first."""
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
    """Crashing is the expected outcome here, so do not save the wreck.

    Otherwise every wrong-version probe hands systemd-coredump a full dump of a
    Python process with steamclient.so mapped, once per game start.
    """
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except (ImportError, ValueError, OSError) as exc:
        LOG.debug("cannot disable core dumps in the probe child: %s", exc)


def _run_in_child(target, timeout=15.0):
    """Run target(write_fd) in a forked child and report how it ended.

    Returns (status, message) with status "ok" or "crashed" - the one place
    the fork, the timeout and the exit-status handling live.
    """
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
            # _exit, not exit: no atexit handlers, and no flushing the copies
            # of the parent's buffers this process inherited.
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
    """Try one route in a child process.

    Returns (status, detail) where status is "ok", "failed" or "crashed".
    """
    def attempt(write_fd):
        try:
            stats = UserStats(app_id, library, route=route)
            stats.open()
            os.write(write_fd, b"OK %d" % len(stats.achievements()))
        except BaseException as exc:                # noqa: BLE001 - report all
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
    """Find a route that survives being used. Returns (route, achievements).

    Which route works depends on the library, not the app, so a known-good one
    goes first: later game starts cost one fork instead of up to fifteen.
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
