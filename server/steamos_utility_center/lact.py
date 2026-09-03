# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""Communication with LACT, when LACT is present.

LACT is the Linux AMDGPU Controller Tool. It is another person's daemon, and it
owns the power limit, the clocks, the voltage and the fans of the graphics
card.

None of that work is ours, and no part of LACT is in this repository. Unlike
the CEC toolkit, there is nothing to install. Either lactd runs on this machine
or it does not, and this module asks the question.

**The protocol.** The daemon listens on a unix socket. It speaks JSON with one
document on each line: one request object in, one response object out.

That is the full protocol. There is thus no client library here and none is
necessary. `socket` and `json` from the standard library are the client.

    {"command": "list_devices"}
    {"status": "ok", "data": [{"id": "...", "name": "..."}]}

**No password is necessary.** The daemon gives its socket to the first group
of `wheel` and `sudo` that exists. On SteamOS the desktop user is in `wheel`.

The panel thus speaks to the daemon as the user, as the HDMI CEC switches do. A
machine with a different configuration says so in /etc/lact/config.yaml, and
this module reports a refused connection.

**This module is careful about two things.** A mistake in either is expensive
on another person's machine.

First, a configuration change is *pending* until a confirmation.
`set_gpu_config` answers with a number of seconds. Without a
`confirm_pending_config` inside that time, the daemon puts the old settings
back.

That is not an obstacle to remove. It is what makes a wrong voltage a mistake
and not a machine that cannot boot. This module thus models it and does not
hide it.

Second, the values that a card accepts are different for each card. The ranges
for the clocks and the voltage come from the daemon's own report of that GPU,
and this module reads that report with care.

The table is a tagged union. Its shape depends on the manufacturer and the
generation. This module thus does not model each variant. It looks for the
ranges in each position and draws no control that it cannot find a range for.

A fixed shape is a shape that is wrong on somebody's hardware, and a wrong
shape here is a control that writes a value the card refuses. The governor page
follows the same rule.
"""

from __future__ import annotations

import json
import os
import socket

# Where the daemon listens. LACT can also open a TCP port. That port is off by
# default and this module does not use it: a panel on the machine has no reason
# to use a network, and the permissions are set for the socket.
SOCKET_PATH = "/run/lactd.sock"

# Each function below takes `path=None` and reads the constant here. None of
# them writes `path=SOCKET_PATH` in its signature.
#
# Python evaluates a default in a signature one time, at the import of the
# module. The constant above is thus fixed after that. A test that changed it
# would have no effect, and nothing would report the failure.


def _where(path):
    return path or SOCKET_PATH

# Long enough for a daemon that applies a setting, and short enough that a
# daemon that stops does not hold the window. Each call here is one short
# exchange with a local socket, and one second is already sufficient.
TIMEOUT = 2.0

# The confirmation time to use when the daemon does not give one. The daemon's
# own default is five seconds.
#
# This value is for an answer with no number in it, and it is deliberately
# short. A high value leaves settings on the page that the daemon already put
# back.
CONFIRM_SECONDS = 5

# The controls that this panel offers, in the order of the display. Each entry
# names the key in the gpu config of LACT, the label, the unit, and the source
# of its range.
#
# The range belongs to the GPU and not to this project. A control whose range
# this module cannot read is a control that this machine does not have.
#
# This is not the full configuration. The window of LACT has offset tables for
# each power state, VF curves and firmware settings. Those are for a person
# with that window open and a stress test in progress. A copy of them here is a
# second and worse LACT.
#
# These four controls are the controls that people use.
POWER_CAP = "power_cap"

# The position of the clocks in the configuration document. A new daemon keeps
# them at the top, beside the power limit. An old daemon keeps them in a block
# of their own.
#
# This module reads both. The document decides which one this module writes,
# and this file does not. See with_knob.
CLOCKS_BLOCK = "clocks_configuration"

# The GPU clock offset in the document is not one number. It is a table of
# numbers, one for each power state. The window of LACT offers one offset and
# writes it into state 0, and this module does the same.
GPU_CLOCK_OFFSET = "gpu_clock_offset"
GPU_CLOCK_OFFSETS = "gpu_clock_offsets"
OFFSET_STATE = "0"

# (key, label, unit, where its range comes from, where its slider starts)
#
# The last field gives the start position of a control that nobody has set. It
# names an end of what the card reports *now*: "max" and "min" take that end of
# the reported range, and "now" takes the one value that the card reports.
#
# A maximum that starts at the top of what the card would *accept* reads an
# unchanged card as a card with a higher clock than it runs. That is how the
# VRAM control showed 1500 on a card whose memory runs at 1259.
#
# sclk and sclk_offset are alternatives and not a pair. An RDNA card reports no
# absolute core-clock range and takes an offset. An older card is the opposite.
# Each card thus gets the control that it reports.
KNOBS = (
    (POWER_CAP, "Power limit", "W", "power", "max"),
    ("max_core_clock", "Maximum GPU clock", "MHz", "sclk", "max"),
    (GPU_CLOCK_OFFSET, "GPU clock offset", "MHz", "sclk_offset", "now"),
    ("voltage_offset", "Voltage offset", "mV", "voltage_offset", "now"),
    ("max_memory_clock", "Maximum VRAM clock", "MHz", "mclk", "max"),
    ("min_memory_clock", "Minimum VRAM clock", "MHz", "mclk", "min"),
)

# The table kinds whose memory clock appears at two times the stored value.
#
# The window of LACT does this because the memory is DDR and the table holds
# the clock of the controller. A card that reports 1259 appears as 2518, and a
# value of 2400 in that window is stored as 1200.
#
# This module does the same. Two windows beside each other thus do not report
# the same card with a factor of two between them. with_knob reverses the
# factor, so this module writes what LACT writes.
MEMORY_DOUBLED = ("rdna",)

# How the fan is driven. These are LACT's own two modes, with its names.
FAN_STATIC = "static"
FAN_CURVE = "curve"

# A curve that nobody has set. This is not the default of LACT: LACT makes its
# own curve when a person switches fan control on. This is what this panel
# draws while there is no curve, so that the editor has points to move.
STARTING_CURVE = {40: 0.3, 50: 0.35, 60: 0.5, 70: 0.75, 80: 1.0}


# The fan settings in the *firmware* of the card, and not in the control loop
# of LACT. RDNA3 cards and newer cards have them. Each entry is
#
#     (reported-as, written-as, label, unit)
#
# The two names are different for four of the six, and that is the mistake to
# avoid here. The daemon reports `target_temp` and accepts
# `target_temperature`. It reports `zero_rpm_enable` and accepts `zero_rpm`. It
# reports `zero_rpm_temperature` and accepts `zero_rpm_threshold`.
#
# One table holds each pair, so there is one place where they can be wrong.
#
# The labels and the units are LACT's own words. A person who reads its window
# and this one thus reads the same names for the same settings.
FIRMWARE = (
    ("zero_rpm_enable", "zero_rpm", "Zero RPM", ""),
    ("zero_rpm_temperature", "zero_rpm_threshold",
     "Zero RPM stop temperature", "\u00b0C"),
    ("target_temp", "target_temperature", "Target temperature", "\u00b0C"),
    ("acoustic_limit", "acoustic_limit", "Acoustic limit", "RPM"),
    ("acoustic_target", "acoustic_target", "Acoustic target", "RPM"),
    ("minimum_pwm", "minimum_pwm", "Minimum fan speed", "%"),
)

# Their position in the configuration document, and where the card reports
# them.
FIRMWARE_CONFIG = "pmfw_options"
FIRMWARE_REPORT = "pmfw_info"


class LactError(Exception):
    """The daemon is absent, gave no answer, or refused the request."""


def available(path=None):
    """Returns whether there is a socket to speak to.

    This is a file test and not a connection. A caller can thus ask it often
    and from each place. It decides whether the panel draws the GPU half of the
    power page, and that question occurs at each visit to the page.

    A socket that exists is not a daemon that answers: a socket file stays after
    a stop of the daemon. `ping` answers that question, and the page asks it one
    time.
    """
    return os.path.exists(_where(path))


def request(name, args=None):
    """Returns one request object. It omits the arguments when there are none.

    The daemon refuses `"args": null` on a command that takes no arguments.
    This function thus omits an empty dictionary and does not send one.

    The arguments come as a dictionary and not as keywords. One of LACT's own
    arguments has the name `command`, and as a keyword that name collides with
    the name of the command. The argument names belong to LACT, and this module
    must not be a place where they can collide.
    """
    made = {"command": name}
    if args:
        made["args"] = dict(args)
    return made


def read_answer(text):
    """Reads one response. Returns its data, or raises with its message.

    This function is separate from the socket. Each caller can thus be tested
    against a recorded answer.

    An answer is useless in two ways: it is not JSON, or it is JSON that says
    "error". This function separates the two, and each caller does not.
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
    """Sends one request and reads one answer.

    It makes a new connection for each request and does not keep one. The
    daemon accepts both methods.

    A socket that stays open for the life of a window is a socket to connect
    again after each restart of the daemon, each suspend and each update. That
    is state to get wrong, and it gains nothing on a request of some
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
        # This is the one failure with a repair that a person can make: the
        # socket belongs to a group that this user is not in. It thus has its
        # own sentence and not "connection refused".
        raise LactError(
            "%s will not accept this user - LACT's daemon.admin_group in "
            "/etc/lact/config.yaml names the group that may, and this user is "
            "not in it" % path)
    except OSError as exc:
        raise LactError("could not reach LACT at %s: %s" % (path, exc))
    return read_answer(got)


def _read_line(link):
    """Returns one answer that ends with a newline, from any number of packets.

    One recv is not one message. The device list of a machine with two cards is
    larger than one packet. A read of one packet gives truncated JSON, and the
    error then names the daemon for a mistake of the client.
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
    """Returns the card to show. It is the first card, and the only card.

    The owner of a machine with two graphics cards wants the window of LACT and
    not four controls on an LED panel.

    This function has a name, so that the page can report the choice. Without
    it, the page configures the first card in the list and reports nothing.
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
    """Applies a configuration. Returns the time to confirm it.

    The number belongs to the daemon and not to this project. It is a setting
    there. This function thus returns it and does not assume it. See confirm().
    """
    found = talk("set_gpu_config", path, {"id": gpu, "config": config})
    try:
        return int(found)
    except (TypeError, ValueError):
        return CONFIRM_SECONDS


def confirm(path=None, keep=True):
    """Keeps the pending configuration, or puts the old one back now.

    It reverses the change directly and does not wait for the clock. A person
    who made a decision must not watch a countdown to its end.
    """
    return talk("confirm_pending_config", path,
                {"command": "confirm" if keep else "revert"})


def profiles(path=None):
    """Returns the profiles of LACT and the active profile, as (names, current).

    The daemon reports this in more than one shape. An old daemon reports a
    list. A new daemon reports a document that names the active profile. This
    function reads both and assumes neither.
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
    """Changes to a saved profile. The default profile has no name."""
    return talk("set_profile", path, {"name": name} if name else None)


# -- reading what this particular card offers -------------------------------


def power_range(found):
    """Returns (minimum, maximum, current, default) watts. None where absent.

    The values come from the statistics of the card. A machine whose driver
    publishes no power limit reports none of them. Most integrated graphics do
    not publish one.

    The page then draws no power control. Without this test it draws a control
    that writes nowhere.
    """
    power = (found or {}).get("power") or {}
    return {
        "min": _number(power.get("cap_min")),
        "max": _number(power.get("cap_max")),
        "current": _number(power.get("cap_current")),
        "default": _number(power.get("cap_default")),
    }


def ranges(found):
    """Returns the clocks and the voltage the card accepts, {name: (min, max)}.

    This function searches for the values. It does not read them from a known
    position, and that is deliberate.

    The clocks table is a tagged union: AMD, Nvidia, Intel, and several
    generations inside AMD. This panel would have to follow its shape for each
    version.

    Each variant has an `od_range` with `sclk`, `mclk` and `vddc` as
    {min, max}. This function thus looks for that key in each position and
    offers what it finds.

    The cost of the alternative decided this. A fixed path is wrong on
    somebody's hardware, and wrong here is a control that writes a clock the
    card refuses.
    """
    out = {}
    for where in _find(found, "od_range"):
        if not isinstance(where, dict):
            continue
        for name, span in where.items():
            span = _span(span)
            if span and name not in out:
                out[name] = span
    # The three maximum values that LACT reports beside the table. They are
    # for a card whose table has no range of its own.
    for name, key in (("sclk", "max_sclk"), ("mclk", "max_mclk"),
                      ("vddc", "max_voltage")):
        top = _number((found or {}).get(key))
        if name not in out and top:
            out[name] = (0, int(top))
    return out


def _span(value):
    """Returns one {min, max} pair, when both ends are present and valid."""
    if not isinstance(value, dict):
        return None
    low, high = _number(value.get("min")), _number(value.get("max"))
    if low is None or high is None or high <= low:
        return None
    return (int(low), int(high))


def _find(where, key, depth=0):
    """Returns each value under `key`, at any depth. The depth has a limit.

    The limit is necessary. These documents belong to the daemon. A document
    that is malformed or recursive must not stop the window, which only draws a
    control.
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


def memory_scale(clocks):
    """Returns 2 where the memory clock appears at two times its stored value.

    The key is the `kind` of the table. That is the daemon's own word for the
    shape that it reports, and not a guess about the card.
    """
    for kind in _find(clocks, "kind"):
        if isinstance(kind, str) and kind in MEMORY_DOUBLED:
            return 2
    return 1


def reported(found):
    """Returns the current value of each control, as {name: value}.

    This function is separate from ranges(), because the two answer different
    questions. ranges() gives what the card accepts. This gives where the card
    is now. A control that nobody has set belongs at the second value.

    A card can report a maximum far above what it runs: 1500 against 1259 on
    RDNA. A control that starts at the maximum thus reports a clock that the
    card does not use.

    It searches in the same way as ranges() and for the same reason: the table
    is a tagged union whose shape belongs to the daemon.
    """
    out = {}
    for key, name in (("current_sclk_range", "sclk"),
                      ("current_mclk_range", "mclk")):
        for where in _find(found, key):
            span = _span(where)
            if span and name not in out:
                out[name] = span
    # The offsets are numbers in the same table. They also share their
    # names with the {min, max} entries in od_range. An entry here that is
    # not a number is thus one of those.
    for name in ("sclk_offset", "voltage_offset"):
        for value in _find(found, name):
            number = _number(value)
            if number is not None and name not in out:
                out[name] = number
    return out


def knob_value(config, key):
    """Returns the value of one control in a document, at any position.

    It reads both shapes. The document is the daemon's own answer. The daemon
    decides whether it keeps the clocks at the top or in a block, and this
    module does not.
    """
    config = config or {}
    if key == GPU_CLOCK_OFFSET:
        return _offset_value(config.get(GPU_CLOCK_OFFSETS))
    if key in config:
        return config.get(key)
    return (config.get(CLOCKS_BLOCK) or {}).get(key)


def _offset_value(offsets):
    """Returns the one GPU clock offset from the table of offsets.

    State 0 is the state that the window of LACT writes, and it is the state
    that this module offers.

    This function also reads a card with offsets for other states only. The
    control thus shows a number that a person set, and not an empty control.
    """
    number = _number(offsets)
    if number is not None:
        return number               # a daemon that keeps just the one
    if not isinstance(offsets, dict) or not offsets:
        return None
    if OFFSET_STATE in offsets:
        return _number(offsets[OFFSET_STATE])
    return _number(next(iter(offsets.values())))


def offered(config, clocks, found_stats):
    """Returns the controls that this card has, with each range and value.

    This comes from the report of the daemon and not from KNOBS alone. A
    control with no range is a control that the card does not publish, and to
    draw it is to offer a setting that writes nowhere.

    The governor page follows the same rule: this project asks the machine and
    does not remember an answer.
    """
    spans = ranges(clocks)
    power = power_range(found_stats)
    now = reported(clocks)
    doubled = memory_scale(clocks)
    out = []
    for key, label, unit, source, end in KNOBS:
        scale = 1
        if source == "power":
            if power["min"] is None or power["max"] is None:
                continue
            span = (int(power["min"]), int(power["max"]))
            value = config.get(key)
            # The current value of the card. For the power limit, the card
            # reports that value whether or not LACT wrote it.
            fallback = power["current"] or power["default"] or span[1]
        else:
            span = spans.get(source)
            if span is None:
                # There is no range for this control. For the voltage offset,
                # that is not the same as a card with no offset.
                #
                # An RDNA2 card publishes an od_range of null values. It still
                # reports the offset that it is set to, and it accepts a new
                # one. An older card reports an absolute voltage range in place
                # of a range of offsets.
                #
                # Both cards thus get the range around zero that they accept.
                # Each other control with no range is a control that the card
                # does not have, and to draw it is to write nowhere.
                if source != "voltage_offset" or not (
                        "vddc" in spans
                        or now.get("voltage_offset") is not None):
                    continue
                span = (-250, 250)
            if source == "mclk":
                scale = doubled
            value = knob_value(config, key)
            fallback = _start(now, source, end, span)
        if scale != 1:
            span = (span[0] * scale, span[1] * scale)
            fallback = fallback * scale
            if value is not None:
                value = float(value) * scale
        out.append({"key": key, "label": label, "unit": unit,
                    "min": span[0], "max": span[1],
                    # The factor between the unit of this control and the unit of
                    # the document. See MEMORY_DOUBLED. It travels with the
                    # control, so that the one function that writes can reverse
                    # it.
                    "scale": scale,
                    "value": None if value is None else float(value),
                    # The start position of the control when nothing is set. It is
                    # separate from `value`, so that "set to this value" and "not
                    # set, and this is the result" stay two questions.
                    "start": float(value if value is not None else fallback)})
    return out


def _start(now, source, end, span):
    """Returns the position of a control that nobody has set.

    It returns what the card reports for that control now. It returns an end
    of the accepted range only when the card reports nothing.

    The range is what the card accepts. On a card whose maximum is above what
    it runs, that is a different number, and it reports a clock that the card
    does not use.
    """
    found = now.get(source)
    if isinstance(found, tuple):
        return found[1] if end == "max" else found[0]
    if found is not None:
        return found
    if end == "max":
        return span[1]
    if end == "min":
        return span[0]
    return 0


# -- the fan ----------------------------------------------------------------


def fan(config):
    """Returns the fan settings, in the form that the page draws.

    `enabled` is LACT's own switch. Off means that the firmware of the card
    drives the fan, and that is the state of a machine that nobody changed.
    Each other value here has no effect until the switch is on.
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
    """Returns the firmware fan settings that this card has.

    This function asks the card, and the question is the test. The daemon reads
    each setting from sysfs and reports the ones whose file exists.

    A card without them reports nothing and thus gets no controls. Each card
    before RDNA3 is such a card. Without this test it gets controls that write
    nowhere. This is the rule of the upstream project, and its window uses the
    same test.

    It reports the settings whether or not LACT drives the fan. These are the
    settings of the firmware and not of LACT. They apply while the card
    controls its own fan, and most people leave the card in that state.
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
            # What the upstream project uses when the card reports a value
            # with no range: from zero to the current value.
            low, high = 0, current
        if low is None or high is None or high <= low:
            continue
        out.append({"key": writes, "label": label, "unit": unit,
                    "switch": False, "value": current,
                    "min": int(low), "max": int(high)})
    return out


def with_firmware(config, values):
    """Returns a copy of the config with the firmware fan settings in it.

    They go into a block of their own. That block is not the position of the
    fan curve and not the top level. It is a third position, and this function
    exists so that no other function must know which.
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
    """Returns a copy of the full config with the fan settings changed.

    It is a copy, and it is the full config, because that is the interface. The
    set_gpu_config command of LACT replaces the document and does not patch it.

    A setting that this function omits is thus a setting that stops on
    somebody's card, with no message. Each caller reads the current config
    first and returns it with the change.
    """
    made = dict(config or {})
    settings = dict(made.get("fan_control_settings") or {})
    # The keys that LACT needs in a settings block. This function fills each
    # one from the document or from the default of LACT. LACT refuses a full
    # block that has no value for one of them.
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


def with_knob(config, key, value, scale=1):
    """Returns a copy of the config with one control changed at its position.

    There are three positions, so this is the one function that knows which. A
    page that guessed writes a clock into a key that nothing reads, and the
    daemon accepts the document and reports success.

    The power limit is a field of its own. The GPU clock offset is a table of
    offsets, one for each power state. Each other control is a field, at the
    top of the document.

    The exception is a document with a clocks_configuration block, where an
    older daemon collected them. The document decides this and not a version
    number, because the document is the daemon's own answer.

    `scale` is the factor between the unit of the control and the unit of the
    document, and this function reverses it. A memory clock that the page
    offers as 2400 is thus stored as 1200, which is what the window of LACT
    stores for the same control.
    """
    made = dict(config or {})
    if key == POWER_CAP:
        made[key] = None if value is None else float(value)
        return made
    if value is not None and scale != 1:
        value = value / float(scale)
    if key == GPU_CLOCK_OFFSET:
        offsets = dict(made.get(GPU_CLOCK_OFFSETS) or {})
        if value is None:
            offsets.pop(OFFSET_STATE, None)
        else:
            offsets[OFFSET_STATE] = int(round(value))
        if offsets:
            made[GPU_CLOCK_OFFSETS] = offsets
        else:
            # Not an empty table. An empty table is a card with an
            # instruction to hold no offsets, and not a card with none.
            made.pop(GPU_CLOCK_OFFSETS, None)
        return made
    if CLOCKS_BLOCK in made:
        clocks = dict(made.get(CLOCKS_BLOCK) or {})
        if value is None:
            clocks.pop(key, None)
        else:
            clocks[key] = int(round(value))
        made[CLOCKS_BLOCK] = clocks
        return made
    if value is None:
        made.pop(key, None)
    else:
        made[key] = int(round(value))
    return made
