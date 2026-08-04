"""Realtime achievement detection through Valve's local Steamworks API.

No API key, no network, no public profile: the Steam client already knows the
achievement state, and `libsteam_api.so` is the local door to it. The catch is
that Steamworks is a *game-side* API - it has to be initialised as a specific
app, and it offers no way to ask which game is running. So we find the running
app ID ourselves first, then talk to Steam as that app.

Only the flat C API is used, so ctypes is enough and no callback structs have
to be marshalled: achievements are polled locally, which is cheap because
nothing leaves the machine.
"""

from __future__ import annotations

import ctypes
import glob
import logging
import os
import re
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

# The versioned accessor changes with the SDK a library was built from, so the
# exported symbols are read rather than guessed. These are only the fallback
# order for libraries whose symbol table cannot be parsed.
USER_STATS_ACCESSORS = tuple(
    "SteamAPI_SteamUserStats_v%03d" % version for version in (13, 12, 11, 10)
)

# Interface version strings for the older routes, which take them as text.
USER_STATS_INTERFACES = tuple(
    "STEAMUSERSTATS_INTERFACE_VERSION%03d" % version
    for version in (13, 12, 11, 10, 9, 8, 7)
)

_ACCESSOR_RE = re.compile(r"^SteamAPI_SteamUserStats_v(\d+)$")


def user_stats_accessors(symbols):
    """Versioned ISteamUserStats accessors a library exports, newest first."""
    found = []
    for symbol in symbols:
        match = _ACCESSOR_RE.match(symbol)
        if match:
            found.append((int(match.group(1)), symbol))
    return [name for _version, name in sorted(found, reverse=True)]


def interesting_symbols(symbols):
    """The subset worth showing when a library will not cooperate."""
    return sorted(
        symbol for symbol in symbols
        if ("UserStats" in symbol or "SteamClient" in symbol
            or "FindOrCreate" in symbol or "GetHSteam" in symbol
            or symbol.startswith("SteamAPI_Init"))
    )

LIBRARY_GLOBS = (
    "steamapps/common/*/libsteam_api.so",
    "steamapps/common/*/*/libsteam_api.so",
    "steamapps/common/*/*/*/libsteam_api.so",
    "steamapps/common/*/*/*/*/libsteam_api.so",
    "linux64/libsteam_api.so",
)

# ELFCLASS64 is byte 4 of the ELF header, ELFCLASS32 is 1. Games ship both, and
# Proton keeps them in sibling files/lib and files/lib64 directories, so the
# first match alphabetically is routinely the wrong architecture.
ELFCLASS32, ELFCLASS64 = 1, 2
WANTED_ELF_CLASS = ELFCLASS64 if sys.maxsize > 2 ** 32 else ELFCLASS32


def _as_pointer(value):
    """Accept a raw address or an already-wrapped pointer.

    The resolver routes return c_void_p, and wrapping one of those in
    c_void_p() again raises "cannot be converted to pointer" rather than doing
    nothing - so normalise instead of assuming which form arrived.
    """
    if isinstance(value, ctypes.c_void_p):
        return value
    return ctypes.c_void_p(value)


def _class_name(value):
    return {ELFCLASS32: "32-bit", ELFCLASS64: "64-bit"}.get(
        value, "an unknown ELF class (%s)" % value)


def elf_class(path):
    """1 for a 32-bit object, 2 for 64-bit, None if it is not an ELF file."""
    try:
        with open(path, "rb") as handle:
            header = handle.read(5)
    except OSError:
        return None
    if len(header) < 5 or header[:4] != b"\x7fELF":
        return None
    return header[4]


def find_libraries():
    """Every libsteam_api.so under the Steam directory, newest paths first."""
    root = steam_root()
    if root is None:
        return []
    found = []
    for pattern in LIBRARY_GLOBS:
        for match in glob.glob(os.path.join(root, pattern)):
            if match not in found:
                found.append(match)
    return sorted(found)


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

    The library is part of the Steamworks SDK and ships inside games, so it is
    never redistributed here - one of the installed games lends us its copy.
    """
    if explicit and explicit != "auto":
        if not os.path.isfile(explicit):
            raise SteamworksError("no library at %s" % explicit)
        found = elf_class(explicit)
        if found is not None and found != WANTED_ELF_CLASS:
            raise SteamworksError(
                "%s is %s, this Python needs %s"
                % (explicit, _class_name(found), _class_name(WANTED_ELF_CLASS)))
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
              if elf_class(path) == WANTED_ELF_CLASS]
    if not usable:
        raise SteamworksError(
            "found %d libsteam_api.so, none of them %s like this Python "
            "(e.g. %s)" % (len(candidates), _class_name(WANTED_ELF_CLASS),
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
    """Steam launches games with SteamAppId in their environment."""
    for entry in glob.glob("/proc/[0-9]*/environ"):
        try:
            with open(entry, "rb") as handle:
                environ = handle.read()
        except OSError:
            continue        # not ours, or gone again
        for variable in environ.split(b"\0"):
            if variable.startswith(b"SteamAppId="):
                value = variable.split(b"=", 1)[1].decode("ascii", "replace")
                if value.isdigit() and int(value):
                    return int(value)
    return None


def running_app_id():
    """The app ID of the game currently running, or None."""
    return _app_id_from_registry() or _app_id_from_processes()


# -- the API itself ---------------------------------------------------------


class UserStats:
    """A thin ctypes wrapper around ISteamUserStats for one app."""

    def __init__(self, app_id, library=None, route=None):
        self.app_id = int(app_id)
        self.library_path = find_library(library)
        # A route pins one way in: "accessor:NAME", "userinterface:VERSION" or
        # "client:VERSION". None means try them all.
        self.wanted_route = route if route and route != "auto" else None
        self.route = None
        self._lib = None
        self._iface = None

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
        self._lib = lib
        self._iface = _as_pointer(iface)

        if not self._request_stats(self._iface):
            LOG.warning("RequestCurrentStats returned false; stats may be stale")
        self.wait_for_stats()

    def _symbols(self):
        try:
            return elf.exported_symbols(self.library_path)
        except (OSError, elf.ElfError) as exc:
            LOG.debug("cannot read symbols from %s: %s", self.library_path, exc)
            return set()

    def _resolve_user_stats(self, lib):
        """Get an ISteamUserStats pointer, whichever route this library offers.

        Three of them exist across SDK generations: the versioned flat
        accessor, SteamInternal_FindOrCreateUserInterface, and going through
        ISteamClient. Proton ships an older library than a current SDK, so the
        first one is not always there.
        """
        symbols = self._symbols()
        self.route = None

        if self.wanted_route:
            iface = self._try_route(lib, self.wanted_route)
            if iface:
                return iface
            raise SteamworksError("route %s did not resolve ISteamUserStats"
                                  % self.wanted_route)

        names = user_stats_accessors(symbols) or [
            name for name in USER_STATS_ACCESSORS if hasattr(lib, name)]
        for name in names:
            if not hasattr(lib, name):
                continue
            accessor = getattr(lib, name)
            accessor.restype = ctypes.c_void_p
            accessor.argtypes = []
            iface = accessor()
            if iface:
                self.route = name
                return _as_pointer(iface)

        iface = self._via_user_interface(lib, symbols)
        if iface:
            return iface

        iface = self._via_steam_client(lib, symbols)
        if iface:
            return iface

        raise SteamworksError(
            "%s offers no way to reach ISteamUserStats. Exported symbols that "
            "looked relevant: %s"
            % (self.library_path,
               ", ".join(interesting_symbols(symbols)) or "none"))

    def _try_route(self, lib, route):
        """Take exactly one documented way in, no fallbacks."""
        kind, _, detail = route.partition(":")
        if kind == "accessor":
            if not hasattr(lib, detail):
                return None
            accessor = getattr(lib, detail)
            accessor.restype = ctypes.c_void_p
            accessor.argtypes = []
            iface = accessor()
            if iface:
                self.route = route
                return _as_pointer(iface)
            return None
        if kind == "userinterface":
            return self._via_user_interface(lib, self._symbols(),
                                            versions=(detail,), route=route)
        if kind == "client":
            return self._via_steam_client(lib, self._symbols(),
                                          versions=(detail,), route=route)
        raise SteamworksError("unknown route %r" % route)

    def _via_user_interface(self, lib, symbols, versions=None, route=None):
        name = "SteamInternal_FindOrCreateUserInterface"
        if name not in symbols and not hasattr(lib, name):
            return None
        if not hasattr(lib, "SteamAPI_GetHSteamUser"):
            return None

        get_user = lib.SteamAPI_GetHSteamUser
        get_user.restype = ctypes.c_int32
        get_user.argtypes = []

        create = getattr(lib, name)
        create.restype = ctypes.c_void_p
        create.argtypes = [ctypes.c_int32, ctypes.c_char_p]

        user = get_user()
        for version in (versions or USER_STATS_INTERFACES):
            iface = create(user, version.encode("ascii"))
            if iface:
                self.route = route or ("userinterface:%s" % version)
                return _as_pointer(iface)
        return None

    def _via_steam_client(self, lib, symbols, versions=None, route=None):
        getter = "SteamAPI_ISteamClient_GetISteamUserStats"
        if getter not in symbols and not hasattr(lib, getter):
            return None

        client = None
        client_names = sorted(
            (symbol for symbol in symbols
             if re.match(r"^SteamAPI_SteamClient_v\d+$", symbol)),
            reverse=True)
        for candidate in client_names + ["SteamClient"]:
            if not hasattr(lib, candidate):
                continue
            accessor = getattr(lib, candidate)
            accessor.restype = ctypes.c_void_p
            accessor.argtypes = []
            client = accessor()
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

        user = lib.SteamAPI_GetHSteamUser()
        pipe = lib.SteamAPI_GetHSteamPipe()

        get_stats = getattr(lib, getter)
        get_stats.restype = ctypes.c_void_p
        get_stats.argtypes = [ctypes.c_void_p, ctypes.c_int32, ctypes.c_int32,
                              ctypes.c_char_p]
        for version in (versions or USER_STATS_INTERFACES):
            iface = get_stats(ctypes.c_void_p(client), user, pipe,
                              version.encode("ascii"))
            if iface:
                self.route = route or ("client:%s" % version)
                return _as_pointer(iface)
        return None

    @staticmethod
    def _shutdown(lib):
        if hasattr(lib, "SteamAPI_Shutdown"):
            lib.SteamAPI_Shutdown()

    def _bind(self, lib):
        self._run_callbacks = lib.SteamAPI_RunCallbacks
        self._run_callbacks.restype = None
        self._run_callbacks.argtypes = []

        self._request_stats = lib.SteamAPI_ISteamUserStats_RequestCurrentStats
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

        It is asynchronous: the schema (how many achievements exist) is there
        at once, but every unlock state reads as false until Steam replies.
        Taking the baseline too early makes the whole back catalogue look like
        it unlocked a moment later.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.run_callbacks()
            if any(self.achievements().values()):
                return True
            time.sleep(0.1)
        # A game with genuinely nothing unlocked ends up here too, which is why
        # the watcher keeps its own guard against a late flood.
        LOG.debug("no unlocked achievement seen within %.1fs", timeout)
        return False

    def run_callbacks(self):
        if self._lib is not None:
            self._run_callbacks()

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


class AchievementWatcher:
    """Reports achievements that flip from locked to unlocked."""

    # More than this appearing between two polls is Steam delivering state,
    # not the player earning them a tenth of a second apart.
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
            # First look: adopt the state, everything already earned is old news.
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
# The flat SteamAPI_ISteamUserStats_* wrappers in a library are compiled
# against one specific interface version. Fetching a different version through
# the older routes hands back a pointer whose vtable does not match, and
# calling through it does not fail - it segfaults. Which version a given
# libsteam_api.so expects is not discoverable from the outside, so each
# candidate is tried in a child process: a crash costs one fork instead of the
# whole program, and the survivor is the answer.


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


def _probe_in_child(app_id, library, route, write_fd):
    """Runs in the forked child; must never return."""
    try:
        stats = UserStats(app_id, library, route=route)
        stats.open()
        count = len(stats.achievements())
        os.write(write_fd, b"OK %d" % count)
    except BaseException as exc:                    # noqa: BLE001 - report all
        os.write(write_fd, ("ERR %s" % exc).encode("utf-8", "replace")[:400])
    finally:
        # _exit, not exit: no atexit handlers, no flushing the parent's buffers.
        os._exit(0)


def probe_route(app_id, library, route, timeout=15.0):
    """Try one route in a child process.

    Returns (status, detail) where status is "ok", "failed" or "crashed".
    """
    import select as _select
    import signal as _signal

    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(read_fd)
        _probe_in_child(app_id, library, route, write_fd)

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
    text = message.decode("utf-8", "replace")
    if text.startswith("OK "):
        return "ok", text[3:].strip()
    return "failed", text[4:].strip() if text.startswith("ERR ") else "no answer"


def select_route(app_id, library, reporter=None):
    """Find a route that survives being used. Returns (route, achievements)."""
    for route in candidate_routes(library):
        status, detail = probe_route(app_id, library, route)
        if reporter is not None:
            reporter(route, status, detail)
        if status == "ok":
            return route, int(detail or 0)
    return None, 0


# -- friend chat messages ---------------------------------------------------
#
# An achievement is state: poll it and diff. A message is an event, so this
# needs Steam's callback queue. The manual dispatch API is the one way to read
# that queue without the C++ headers - it was added in SDK 1.47, so an older
# libsteam_api.so simply cannot do this, and says so instead of guessing.

# Interface version strings. Note friends spells them differently from stats:
# "SteamFriends017", not "STEAMFRIENDS_INTERFACE_VERSION017".
FRIENDS_INTERFACES = tuple("SteamFriends%03d" % version
                           for version in range(18, 13, -1))

_FRIENDS_ACCESSOR_RE = re.compile(r"^SteamAPI_SteamFriends_v(\d+)$")

# k_iSteamFriendsCallbacks(300) + 43
CALLBACK_FRIEND_CHAT_MSG = 343
# Only a real message should flash; the same callback also carries typing
# indicators and emotes.
CHAT_ENTRY_TYPE_CHAT_MSG = 1

MANUAL_DISPATCH_SYMBOLS = (
    "SteamAPI_ManualDispatch_Init",
    "SteamAPI_ManualDispatch_RunFrame",
    "SteamAPI_ManualDispatch_GetNextCallback",
    "SteamAPI_ManualDispatch_FreeLastCallback",
)


class CallbackMsg(ctypes.Structure):
    """The header Steam hands back for every queued callback."""

    _fields_ = [
        ("m_hSteamUser", ctypes.c_int32),
        ("m_iCallback", ctypes.c_int),
        ("m_pubParam", ctypes.POINTER(ctypes.c_uint8)),
        ("m_cubParam", ctypes.c_int),
    ]


def supports_messages(library):
    """Whether a library is new enough to read the callback queue."""
    try:
        symbols = elf.exported_symbols(library)
    except (OSError, elf.ElfError):
        return False, "symbol table unreadable"

    missing = [name for name in MANUAL_DISPATCH_SYMBOLS if name not in symbols]
    if missing:
        return False, "no manual dispatch (missing %s)" % ", ".join(missing)
    if not any(name in symbols for name in
               ("SteamAPI_ISteamClient_GetISteamFriends",
                "SteamInternal_FindOrCreateUserInterface")) \
            and not any(_FRIENDS_ACCESSOR_RE.match(name) for name in symbols):
        return False, "no way to reach ISteamFriends"
    return True, "manual dispatch available"


class MessageWatcher:
    """Turns Steam's callback queue into 'a friend just wrote' events.

    Attaches to the same library and app as the achievement side, and is
    deliberately optional: every failure disables messages and leaves the
    achievement path untouched.
    """

    def __init__(self, stats):
        # Reuses the open UserStats connection: same process, same SteamAPI_Init.
        self.stats = stats
        self.friends = None
        self.pipe = None
        self._lib = None
        self._get_next = None
        self._free_last = None
        self._run_frame = None
        self._get_message = None

    def open(self):
        lib = self.stats._lib
        if lib is None:
            raise SteamworksError("Steamworks is not open")

        for name in MANUAL_DISPATCH_SYMBOLS:
            if not hasattr(lib, name):
                raise SteamworksError(
                    "%s has no %s - this libsteam_api.so predates manual "
                    "dispatch (SDK 1.47), so messages cannot be read"
                    % (self.stats.library_path, name))

        self.friends = self._resolve_friends(lib)
        if not self.friends:
            raise SteamworksError("could not reach ISteamFriends")

        listen = lib.SteamAPI_ISteamFriends_SetListenForFriendsMessages
        listen.restype = ctypes.c_bool
        listen.argtypes = [ctypes.c_void_p, ctypes.c_bool]
        if not listen(self.friends, True):
            raise SteamworksError("SetListenForFriendsMessages was refused")

        self._get_message = lib.SteamAPI_ISteamFriends_GetFriendMessage
        self._get_message.restype = ctypes.c_int
        self._get_message.argtypes = [ctypes.c_void_p, ctypes.c_uint64,
                                      ctypes.c_int, ctypes.c_void_p,
                                      ctypes.c_int, ctypes.POINTER(ctypes.c_int)]

        get_pipe = lib.SteamAPI_GetHSteamPipe
        get_pipe.restype = ctypes.c_int32
        get_pipe.argtypes = []
        self.pipe = get_pipe()

        lib.SteamAPI_ManualDispatch_Init()
        self._run_frame = lib.SteamAPI_ManualDispatch_RunFrame
        self._run_frame.restype = None
        self._run_frame.argtypes = [ctypes.c_int32]
        self._get_next = lib.SteamAPI_ManualDispatch_GetNextCallback
        self._get_next.restype = ctypes.c_bool
        self._get_next.argtypes = [ctypes.c_int32, ctypes.POINTER(CallbackMsg)]
        self._free_last = lib.SteamAPI_ManualDispatch_FreeLastCallback
        self._free_last.restype = None
        self._free_last.argtypes = [ctypes.c_int32]
        self._lib = lib
        LOG.info("listening for friend messages")

    def _resolve_friends(self, lib):
        symbols = self.stats._symbols()

        accessors = sorted(
            (int(match.group(1)), match.group(0))
            for match in (_FRIENDS_ACCESSOR_RE.match(name) for name in symbols)
            if match)
        for _version, name in sorted(accessors, reverse=True):
            accessor = getattr(lib, name, None)
            if accessor is None:
                continue
            accessor.restype = ctypes.c_void_p
            accessor.argtypes = []
            iface = accessor()
            if iface:
                return _as_pointer(iface)

        if hasattr(lib, "SteamAPI_ISteamClient_GetISteamFriends") \
                and hasattr(lib, "SteamAPI_GetHSteamUser"):
            client = self._steam_client(lib, symbols)
            if client:
                for name, restype in (("SteamAPI_GetHSteamUser", ctypes.c_int32),
                                      ("SteamAPI_GetHSteamPipe", ctypes.c_int32)):
                    function = getattr(lib, name)
                    function.restype = restype
                    function.argtypes = []
                getter = lib.SteamAPI_ISteamClient_GetISteamFriends
                getter.restype = ctypes.c_void_p
                getter.argtypes = [ctypes.c_void_p, ctypes.c_int32,
                                   ctypes.c_int32, ctypes.c_char_p]
                user = lib.SteamAPI_GetHSteamUser()
                pipe = lib.SteamAPI_GetHSteamPipe()
                for version in FRIENDS_INTERFACES:
                    iface = getter(client, user, pipe, version.encode("ascii"))
                    if iface:
                        LOG.debug("ISteamFriends via %s", version)
                        return _as_pointer(iface)
        return None

    @staticmethod
    def _steam_client(lib, symbols):
        names = sorted((name for name in symbols
                        if re.match(r"^SteamAPI_SteamClient_v\d+$", name)),
                       reverse=True)
        for candidate in names + ["SteamClient"]:
            accessor = getattr(lib, candidate, None)
            if accessor is None:
                continue
            accessor.restype = ctypes.c_void_p
            accessor.argtypes = []
            client = accessor()
            if client:
                return ctypes.c_void_p(client)
        return None

    def poll(self):
        """Drain the callback queue; returns how many friend messages arrived."""
        if self._lib is None:
            return 0

        messages = 0
        self._run_frame(self.pipe)
        callback = CallbackMsg()
        while self._get_next(self.pipe, ctypes.byref(callback)):
            try:
                if callback.m_iCallback == CALLBACK_FRIEND_CHAT_MSG:
                    if self._is_real_message(callback):
                        messages += 1
            finally:
                # Must always free, or the queue never advances.
                self._free_last(self.pipe)
        return messages

    def _is_real_message(self, callback):
        """Filter out typing indicators, which share this callback."""
        if callback.m_cubParam < 12 or not callback.m_pubParam:
            return True         # cannot tell; better to flash than to miss one
        payload = ctypes.string_at(callback.m_pubParam, 12)
        steam_id = int.from_bytes(payload[0:8], "little")
        message_id = int.from_bytes(payload[8:12], "little", signed=True)

        buffer = ctypes.create_string_buffer(4096)
        entry_type = ctypes.c_int(0)
        self._get_message(self.friends, steam_id, message_id, buffer,
                          len(buffer), ctypes.byref(entry_type))
        return entry_type.value == CHAT_ENTRY_TYPE_CHAT_MSG

    def close(self):
        self._lib = None
        self.friends = None
