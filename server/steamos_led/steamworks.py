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

LOG = logging.getLogger(__name__)

STEAM_ROOTS = (
    "~/.local/share/Steam",
    "~/.steam/steam",
    "~/.steam/root",
    "/home/deck/.local/share/Steam",
)

# The versioned accessor changes with the SDK the library was built from, so
# try the known ones newest first.
USER_STATS_ACCESSORS = tuple(
    "SteamAPI_SteamUserStats_v%03d" % version for version in (13, 12, 11, 10)
)

LIBRARY_GLOBS = (
    "steamapps/common/*/libsteam_api.so",
    "steamapps/common/*/*/libsteam_api.so",
    "steamapps/common/*/*/*/libsteam_api.so",
    "linux64/libsteam_api.so",
)


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
    if explicit:
        if os.path.isfile(explicit):
            return explicit
        raise SteamworksError("no library at %s" % explicit)

    root = steam_root()
    if root is None:
        raise SteamworksError("no Steam directory found")

    for pattern in LIBRARY_GLOBS:
        matches = sorted(glob.glob(os.path.join(root, pattern)))
        if matches:
            return matches[0]
    raise SteamworksError(
        "no libsteam_api.so under %s - install a game that ships it, or pass "
        "the path explicitly" % root)


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

    def __init__(self, app_id, library=None):
        self.app_id = int(app_id)
        self.library_path = find_library(library)
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

        accessor = None
        for name in USER_STATS_ACCESSORS:
            if hasattr(lib, name):
                accessor = getattr(lib, name)
                break
        if accessor is None:
            self._shutdown(lib)
            raise SteamworksError("%s exports no ISteamUserStats accessor"
                                  % self.library_path)

        accessor.restype = ctypes.c_void_p
        accessor.argtypes = []
        iface = accessor()
        if not iface:
            self._shutdown(lib)
            raise SteamworksError("Steam returned no ISteamUserStats interface")

        self._bind(lib)
        self._lib = lib
        self._iface = ctypes.c_void_p(iface)

        if not self._request_stats(self._iface):
            LOG.warning("RequestCurrentStats returned false; stats may be stale")
        self.run_callbacks()

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

    def __init__(self, stats):
        self.stats = stats
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
        return fresh
