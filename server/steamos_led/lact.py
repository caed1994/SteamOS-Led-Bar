# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""Talking to LACT, if it is there.

LACT is the Linux AMDGPU Controller Tool - somebody else's daemon that owns
the graphics card's power limit, clocks, voltage and fans. None of that work
is ours and none of it is vendored: unlike the CEC toolkit, there is nothing
to install. Either lactd is running on this machine or it is not, and this
module is what asks.

**How it is spoken to.** The daemon listens on a unix socket and speaks
newline-delimited JSON: one request object in, one response object out. That
is the whole protocol, so there is no client library here and none needed -
`socket` and `json` from the standard library are the client.

    {"command": "list_devices"}
    {"status": "ok", "data": [{"id": "...", "name": "..."}]}

**No password.** The daemon chowns its socket to the first of `wheel`, `sudo`
that exists, and on SteamOS the desktop user is in `wheel` - so the panel
talks to it as itself, the way the HDMI CEC switches do. A machine configured
otherwise says so in /etc/lact/config.yaml, and a refused connection is
reported rather than swallowed.

**Two things this module is careful about, both because getting them wrong is
expensive on somebody else's machine.**

A configuration change is *pending* until confirmed. `set_gpu_config` answers
with a number of seconds, and unless `confirm_pending_config` arrives inside
that window the daemon puts the old settings back. That is not an obstacle to
work around - it is what makes a bad voltage a mistake instead of a machine
that will not boot - so it is modelled here rather than hidden.

And what a card can be told varies by card. The ranges for clocks and voltage
come from the daemon's own report of that GPU, read leniently: the table is a
tagged union whose shape depends on vendor and generation, so rather than
model every variant this looks for the ranges wherever they are and draws
nothing for a knob it cannot find. A hardcoded shape would be one that is
wrong on somebody's hardware, which is the same rule the governor page
follows.
"""

from __future__ import annotations

import json
import os
import socket

# Where the daemon listens. LACT can also be given a TCP port, which is off by
# default and which this does not use: a panel on the machine has no reason to
# reach across a network, and the socket is the path the permissions are set
# up for.
SOCKET_PATH = "/run/lactd.sock"

# Every function below takes `path=None` and resolves it here rather than
# writing `path=SOCKET_PATH` in its signature. A default in a signature is
# bound once, when the module is imported - so the constant above could never
# be changed afterwards, by a test or by anything else, and the change would
# be silently ignored rather than refused.


def _where(path):
    return path or SOCKET_PATH

# Long enough for a daemon that is busy applying something, short enough that
# a wedged one does not hold the window. Every call here is one short round
# trip to a local socket; a second is already generous.
TIMEOUT = 2.0

# What the confirm window defaults to when the daemon does not say. Its own
# default is five seconds; this is only the fallback for an answer that has no
# number in it, and it is short on purpose - guessing high would leave settings
# applied that the daemon has already reverted.
CONFIRM_SECONDS = 5

# The knobs this panel offers, in the order they are shown. Each names the key
# in LACT's gpu config, the label, the unit, and where its range comes from -
# because the range is the GPU's, not ours, and a knob whose range cannot be
# read is one this machine does not have.
#
# Not the whole config. LACT's own window has per-power-state offset tables, VF
# curves and firmware heuristics; those are for somebody sitting with it open
# and a stress test running, and copying them here would be a second, worse
# LACT. These four are the ones people actually reach for.
POWER_CAP = "power_cap"

KNOBS = (
    (POWER_CAP, "Power limit", "W", "power"),
    ("max_core_clock", "Maximum GPU clock", "MHz", "sclk"),
    ("max_memory_clock", "Maximum VRAM clock", "MHz", "mclk"),
    ("voltage_offset", "Voltage offset", "mV", "vddc_offset"),
)

# How the fan is driven. LACT's own two, named the same.
FAN_STATIC = "static"
FAN_CURVE = "curve"

# A curve nobody has set yet. Not LACT's default - it makes its own when fan
# control is switched on - but what this panel draws while there is nothing to
# draw, so the editor has points to move rather than an empty box.
STARTING_CURVE = {40: 0.3, 50: 0.35, 60: 0.5, 70: 0.75, 80: 1.0}


# The fan settings that live in the card's *firmware* rather than in LACT's
# own control loop - RDNA3 and newer. Each entry is
#
#     (reported-as, written-as, label, unit)
#
# and the two names differ for four of the six, which is the thing to get
# wrong here: the daemon reports `target_temp` and accepts `target_temperature`,
# reports `zero_rpm_enable` and accepts `zero_rpm`, reports
# `zero_rpm_temperature` and accepts `zero_rpm_threshold`. Written out as one
# table so there is a single place they can disagree.
#
# The labels and units are LACT's own words, so somebody reading its window
# and this one is reading the same names for the same things.
FIRMWARE = (
    ("zero_rpm_enable", "zero_rpm", "Zero RPM", ""),
    ("zero_rpm_temperature", "zero_rpm_threshold",
     "Zero RPM stop temperature", "\u00b0C"),
    ("target_temp", "target_temperature", "Target temperature", "\u00b0C"),
    ("acoustic_limit", "acoustic_limit", "Acoustic limit", "RPM"),
    ("acoustic_target", "acoustic_target", "Acoustic target", "RPM"),
    ("minimum_pwm", "minimum_pwm", "Minimum fan speed", "%"),
)

# Where they sit in the config document, and where the card reports them.
FIRMWARE_CONFIG = "pmfw_options"
FIRMWARE_REPORT = "pmfw_info"


class LactError(Exception):
    """The daemon is not there, would not answer, or refused what was asked."""


def available(path=None):
    """Whether there is a socket to talk to at all.

    A file test rather than a connection, so this can be asked often and from
    anywhere - it is what decides whether the GPU half of the power page is
    drawn, and that question comes up on every visit.

    Being there is not the same as answering: a socket file outlives a daemon
    that was killed. `ping` is what settles that, and the page asks it once.
    """
    return os.path.exists(_where(path))


def request(name, args=None):
    """One request object. Arguments are omitted entirely when there are none.

    The daemon rejects `"args": null` on commands that take none, so an empty
    dictionary is left out rather than sent empty.

    Args come as a dictionary rather than as keywords, and that is not
    fussiness: one of LACT's own arguments is called `command`, which as a
    keyword collides with the name of the command it belongs to. Its argument
    names are its own and this should not be a place they can clash with.
    """
    made = {"command": name}
    if args:
        made["args"] = dict(args)
    return made


def read_answer(text):
    """Unwrap one response. Returns its data, or raises with what it said.

    Kept apart from the socket so every caller downstream can be tested
    against a recorded answer, and so the two ways an answer can be useless -
    not JSON, or JSON saying "error" - are told apart here rather than by each
    caller.
    """
    try:
        found = json.loads(text)
    except ValueError as exc:
        raise LactError("LACT did not answer in JSON: %s" % exc)
    if not isinstance(found, dict):
        raise LactError("LACT answered with %s, not a response"
                        % type(found).__name__)
    if found.get("status") != "ok":
        said = found.get("data")
        raise LactError(str(said) if said else "LACT refused the request")
    return found.get("data")


def talk(name, path=None, args=None, timeout=TIMEOUT):
    """Send one request and read one answer.

    A fresh connection per request rather than a kept one. The daemon is happy
    either way, and a socket held open across a window's lifetime is a socket
    to reconnect after every daemon restart, every suspend and every update -
    which is state to get wrong for no gain on a request that takes
    milliseconds.
    """
    path = _where(path)
    said = json.dumps(request(name, args)) + "\n"
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as link:
            link.settimeout(timeout)
            link.connect(path)
            link.sendall(said.encode("utf-8"))
            got = _read_line(link)
    except socket.timeout:
        raise LactError("LACT did not answer within %g seconds" % timeout)
    except PermissionError:
        # The one failure with a fix somebody can act on: the socket belongs
        # to a group this user is not in. Worth its own sentence rather than
        # "connection refused".
        raise LactError(
            "%s will not accept this user - LACT's daemon.admin_group in "
            "/etc/lact/config.yaml names the group that may, and this user is "
            "not in it" % path)
    except OSError as exc:
        raise LactError("could not reach LACT at %s: %s" % (path, exc))
    return read_answer(got)


def _read_line(link):
    """One newline-terminated answer, however many packets it arrives in.

    A single recv is not a message: the device list of a machine with two
    cards already exceeds one, and read as if it were, the JSON is truncated
    and the error blames the daemon for the client's mistake.
    """
    chunks = []
    while True:
        block = link.recv(65536)
        if not block:
            break
        chunks.append(block)
        if b"\n" in block:
            break
    return b"".join(chunks).decode("utf-8", "replace")


# -- what the daemon can be asked -------------------------------------------


def devices(path=None):
    found = talk("list_devices", path)
    return list(found) if isinstance(found, list) else []


def first_device(path=None):
    """The card to show. The first one, and this panel shows only that.

    A machine with two graphics cards is a machine whose owner wants LACT's
    own window, not four sliders on somebody's LED panel. Naming the choice
    here means the page can say so rather than silently configure whichever
    card came back first.
    """
    found = devices(path)
    return found[0].get("id", "") if found else ""


def stats(gpu, path=None):
    return talk("device_stats", path, {"id": gpu}) or {}


def clocks_info(gpu, path=None):
    return talk("device_clocks_info", path, {"id": gpu}) or {}


def gpu_config(gpu, path=None):
    return talk("get_gpu_config", path, {"id": gpu}) or {}


def set_gpu_config(gpu, config, path=None):
    """Apply a configuration. Returns how long there is to confirm it.

    The number is the daemon's, not ours - it is configurable there - so it is
    passed back rather than assumed. See confirm().
    """
    found = talk("set_gpu_config", path, {"id": gpu, "config": config})
    try:
        return int(found)
    except (TypeError, ValueError):
        return CONFIRM_SECONDS


def confirm(path=None, keep=True):
    """Keep the pending configuration, or put the old one back now.

    Reverting explicitly rather than waiting out the clock: somebody who has
    decided is somebody who should not have to watch a countdown finish.
    """
    return talk("confirm_pending_config", path,
                {"command": "confirm" if keep else "revert"})


def profiles(path=None):
    """The profiles LACT has, and which one is on.

    Returned as (names, current). The daemon reports this in more than one
    shape across versions - a bare list in older ones, a document with the
    current one named in newer - so both are read and neither is assumed.
    """
    found = talk("list_profiles", path)
    if isinstance(found, dict):
        names = [str(name) for name in found.get("profiles") or []]
        current = found.get("current_profile")
        return names, ("" if current is None else str(current))
    if isinstance(found, list):
        return [str(name) for name in found], ""
    return [], ""


def set_profile(name, path=None):
    """Switch to a saved profile. The default one is spelled as no name."""
    return talk("set_profile", path, {"name": name} if name else None)


# -- reading what this particular card offers -------------------------------


def power_range(found):
    """(minimum, maximum, current, default) watts, or None where unreported.

    From the card's own stats. A machine whose driver publishes no power cap -
    which is most integrated graphics - reports none of these, and the page
    then does not draw a power slider rather than drawing one that writes to
    nothing.
    """
    power = (found or {}).get("power") or {}
    return {
        "min": _number(power.get("cap_min")),
        "max": _number(power.get("cap_max")),
        "current": _number(power.get("cap_current")),
        "default": _number(power.get("cap_default")),
    }


def ranges(found):
    """What clocks and voltage this card will accept, as {name: (min, max)}.

    Searched for rather than read from a known place, and that is deliberate.
    The clocks table is a tagged union - AMD, Nvidia, Intel, and several
    generations within AMD - whose shape this panel would have to track
    version by version. What every one of them has in common is an `od_range`
    holding `sclk`, `mclk` and `vddc` as {min, max}, so this looks for that
    wherever it sits and offers whatever it finds.

    The cost of the alternative is what decided it: a hardcoded path is one
    that is wrong on somebody's hardware, and wrong here means a slider that
    writes a clock the card will not take.
    """
    out = {}
    for where in _find(found, "od_range"):
        if not isinstance(where, dict):
            continue
        for name, span in where.items():
            span = _span(span)
            if span and name not in out:
                out[name] = span
    # The three plain maxima LACT reports beside the table, for cards whose
    # table has no range of its own to offer.
    for name, key in (("sclk", "max_sclk"), ("mclk", "max_mclk"),
                      ("vddc", "max_voltage")):
        top = _number((found or {}).get(key))
        if name not in out and top:
            out[name] = (0, int(top))
    return out


def _span(value):
    """One {min, max} pair, when both ends are there and make a range."""
    if not isinstance(value, dict):
        return None
    low, high = _number(value.get("min")), _number(value.get("max"))
    if low is None or high is None or high <= low:
        return None
    return (int(low), int(high))


def _find(where, key, depth=0):
    """Every value stored under `key`, however deep. Depth-limited.

    The limit is not defensive dressing: these documents are the daemon's and
    a malformed or unexpectedly recursive one should not hang the window that
    is only trying to draw a slider.
    """
    if depth > 8 or not isinstance(where, (dict, list)):
        return
    items = where.values() if isinstance(where, dict) else where
    if isinstance(where, dict) and key in where:
        yield where[key]
    for item in items:
        for found in _find(item, key, depth + 1):
            yield found


def _number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def offered(config, clocks, found_stats):
    """Which knobs this card actually has, with their range and value.

    Built from what the daemon reported rather than from KNOBS alone: a knob
    with no range is one the card does not expose, and drawing it would be
    offering a setting that writes nowhere. Same rule as the governor page -
    the machine is asked, not remembered.
    """
    spans = ranges(clocks)
    power = power_range(found_stats)
    out = []
    for key, label, unit, source in KNOBS:
        if source == "power":
            if power["min"] is None or power["max"] is None:
                continue
            span = (int(power["min"]), int(power["max"]))
            value = config.get(key)
            # What the card is set to right now, which for the power cap is a
            # thing the card reports whether or not LACT has written it.
            fallback = power["current"] or power["default"] or span[1]
        elif source == "vddc_offset":
            # An offset, not an absolute: its range is not the card's voltage
            # range and LACT does not report one, so this is the window either
            # side that every card this applies to accepts.
            if "vddc" not in spans:
                continue
            span = (-250, 250)
            value = (config.get("clocks_configuration") or {}).get(key)
            fallback = 0                # no offset is the untouched state
        else:
            if source not in spans:
                continue
            span = spans[source]
            value = (config.get("clocks_configuration") or {}).get(key)
            # A maximum nobody has set is the card's own maximum. Starting
            # such a slider at the *bottom* of its range would draw an
            # untouched card as one clamped to its lowest clock, which is a
            # lie about the machine and the kind somebody acts on.
            fallback = span[1]
        out.append({"key": key, "label": label, "unit": unit,
                    "min": span[0], "max": span[1],
                    "value": None if value is None else float(value),
                    # Where the slider starts when nothing is set. Kept apart
                    # from `value` so "set to this" and "not set, and this is
                    # what that means" stay different questions.
                    "start": float(value if value is not None else fallback)})
    return out


# -- the fan ----------------------------------------------------------------


def fan(config):
    """How the fan is set, in the shape the page draws.

    `enabled` is LACT's own switch: off means the card's firmware drives the
    fan, which is what it does on a machine nobody has touched, and every
    other value here is inert until it is on.
    """
    settings = (config or {}).get("fan_control_settings") or {}
    curve = {}
    for at, speed in (settings.get("curve") or {}).items():
        degrees, fraction = _number(at), _number(speed)
        if degrees is not None and fraction is not None:
            curve[int(degrees)] = float(fraction)
    return {
        "enabled": bool((config or {}).get("fan_control_enabled")),
        "mode": settings.get("mode") or FAN_CURVE,
        "static_speed": _number(settings.get("static_speed")) or 0.5,
        "curve": curve or dict(STARTING_CURVE),
        "temperature_key": settings.get("temperature_key") or "edge",
    }


def firmware(found_stats):
    """The firmware fan settings this card actually has.

    The card is asked, and the asking is the capability test: the daemon
    reads each of these out of sysfs and reports the ones whose file exists,
    so a card without them - anything before RDNA3 - reports nothing and gets
    no controls rather than controls that write nowhere. It is upstream's own
    rule; its window gates them the same way.

    Reported whether or not LACT is driving the fan, because these are the
    firmware's settings rather than LACT's: they apply while the card is
    looking after its own fan, which is the state most people leave it in.
    """
    info = ((found_stats or {}).get("fan") or {}).get(FIRMWARE_REPORT) or {}
    out = []
    for key, writes, label, unit in FIRMWARE:
        if key not in info or info[key] is None:
            continue
        said = info[key]
        if isinstance(said, bool):
            out.append({"key": writes, "label": label, "unit": unit,
                        "switch": True, "value": said})
            continue
        if not isinstance(said, dict):
            continue
        current = _number(said.get("current"))
        span = said.get("allowed_range")
        if isinstance(span, (list, tuple)) and len(span) == 2:
            low, high = _number(span[0]), _number(span[1])
        else:
            # What upstream falls back to when the card reports a value with
            # no range: from nothing up to where it is now.
            low, high = 0, current
        if low is None or high is None or high <= low:
            continue
        out.append({"key": writes, "label": label, "unit": unit,
                    "switch": False, "value": current,
                    "min": int(low), "max": int(high)})
    return out


def with_firmware(config, values):
    """A copy of the config with the firmware fan settings written in.

    Into their own block, which is neither where the fan curve lives nor the
    top level - a third place, and the only reason this function exists is so
    that nothing else has to know which.
    """
    made = dict(config or {})
    options = dict(made.get(FIRMWARE_CONFIG) or {})
    for key, value in values.items():
        if value is None:
            options.pop(key, None)
        else:
            options[key] = bool(value) if isinstance(value, bool) else int(value)
    made[FIRMWARE_CONFIG] = options
    return made


def with_fan(config, enabled=None, mode=None, static_speed=None, curve=None):
    """A copy of the config with the fan settings changed.

    A copy, and the whole config, because that is the interface: LACT's
    set_gpu_config replaces the document rather than patching it, so anything
    dropped here is a setting silently turned off on somebody's card. Every
    caller reads the current config first and hands it back changed.
    """
    made = dict(config or {})
    settings = dict(made.get("fan_control_settings") or {})
    # The keys LACT requires in a settings block, filled from what is there or
    # from its own defaults - a block missing one of them is refused whole.
    settings.setdefault("temperature_key", "edge")
    settings.setdefault("interval_ms", 500)
    settings.setdefault("mode", FAN_CURVE)
    settings.setdefault("static_speed", 0.5)
    settings.setdefault("curve", {str(at): speed
                                  for at, speed in STARTING_CURVE.items()})
    if enabled is not None:
        made["fan_control_enabled"] = bool(enabled)
    if mode is not None:
        settings["mode"] = mode
    if static_speed is not None:
        settings["static_speed"] = max(0.0, min(1.0, float(static_speed)))
    if curve is not None:
        settings["curve"] = {str(int(at)): max(0.0, min(1.0, float(speed)))
                             for at, speed in sorted(curve.items())}
    made["fan_control_settings"] = settings
    return made


def with_knob(config, key, value):
    """A copy of the config with one knob changed, in the place it lives.

    The power cap is a field of its own; everything else sits in
    clocks_configuration. Two places, so this is the one function that knows
    which - a page that guessed would write a clock into a key nothing reads
    and report success.
    """
    made = dict(config or {})
    if key == POWER_CAP:
        made[key] = None if value is None else float(value)
        return made
    clocks = dict(made.get("clocks_configuration") or {})
    if value is None:
        clocks.pop(key, None)
    else:
        clocks[key] = int(value)
    made["clocks_configuration"] = clocks
    return made
