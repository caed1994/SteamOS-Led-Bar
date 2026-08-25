# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""Configuration loading: /etc/steamos-led-serial.conf plus CLI overrides.

Plain KEY=value lines, so shell scripts and systemd's EnvironmentFile can read
the same file.
"""

from __future__ import annotations

import logging
import os

from . import desktop
from . import notify
from . import phone
from . import render
from .serialport import BAUD_CONSTANTS

LOG = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = "/etc/steamos-led-serial.conf"

# Options that existed once and no longer do, with what became of them.
#
# An unknown key is fatal on purpose - LED_COUTN=60 would otherwise do nothing
# and say nothing. But a key that *we* withdrew is not the reader's mistake,
# and refusing to start over one turns a stale line into a service that will
# not come up. So these are read, ignored, mentioned once, and dropped from
# the file the next time the panel writes it.
RETIRED = {
    "WARNING_COLOR": "a warning is always red now",
    "WARNING_STYLE": "a warning always uses the alternate shape now",
    "TEMPERATURE_GAUGE": "the rainbow slot now picks which of several "
                         "effects it shows, and RAINBOW_SHOWS says which",
    "PHONE_SOURCE": "the phone's notifications only ever come from KDE "
                    "Connect now. The other setting read the desktop's own "
                    "notification bus, which carries this machine's "
                    "notifications as well as the phone's - and carries "
                    "nothing at all in Game Mode",
}

# Withdrawn options that still carry a setting worth keeping, and what to make
# of it: {old: (new, {old value: new value})}.
#
# Being ignored is right for an option that stopped meaning anything, but
# TEMPERATURE_GAUGE=1 means exactly one of the choices that replaced it. Just
# dropping it would switch off a gauge somebody is looking at, with no error
# and nothing to search for - so it is translated instead. An explicit new
# setting always wins, whichever order the two appear in.
MIGRATED = {
    "TEMPERATURE_GAUGE": ("RAINBOW_SHOWS", {True: "temperature",
                                            False: "rainbow"}),
}

DEFAULTS = {
    "DEVICE": "/dev/valve-leds-shim",
    "SERIAL_PORT": "auto",
    "BAUD": 230400,
    "BAUD_AUTODETECT": True,
    "LED_COUNT": 17,
    "MAPPING": "stretch",
    "REVERSE": False,
    "MAX_BRIGHTNESS": 255,
    "MIN_BRIGHTNESS": 0,
    "GAMMA": 1.0,
    "SPEED": 1.0,
    "PATROL_DOTS": 1,
    "NOTIFY": True,
    "NOTIFY_DURATION": 3.5,
    "NOTIFY_REPEAT_GAP": notify.DEFAULT_REPEAT_GAP,
    "NOTIFY_FIFO": notify.DEFAULT_FIFO,
    "NOTIFY_STYLE": "bloom",
    "NOTIFY_ACHIEVEMENTS": True,
    "NOTIFY_MESSAGES": True,
    "NOTIFY_FRIEND_ONLINE": True,
    "NOTIFY_PHONE": False,
    "NOTIFY_WARNING": True,
    "STANDBY_PULSE": True,
    "ACHIEVEMENT_COLOR": "#ffff00",
    "MESSAGE_COLOR": "#8000ff",
    "FRIEND_COLOR": "#00ff00",
    "PHONE_COLOR": "#00ffff",
    "ACHIEVEMENT_STYLE": notify.STYLE_INHERIT,
    "MESSAGE_STYLE": notify.STYLE_INHERIT,
    "FRIEND_STYLE": notify.STYLE_INHERIT,
    "PHONE_STYLE": notify.STYLE_INHERIT,
    "PHONE_APPS": "",
    "PHONE_APPS_ONLY": False,
    "DESKTOP_SCENE": desktop.SCENE_STEAM,
    "DESKTOP_COLOR": "#ffffff",
    "DESKTOP_BRIGHTNESS": 128,
    "DESKTOP_SPEED": 1.0,
    "RAINBOW_SHOWS": "rainbow",
    "LOAD_CPU_COLOR": "#ff6e00",
    "LOAD_GPU_COLOR": "#1a9fff",
    "TEMPERATURE_MIN": 40.0,
    "TEMPERATURE_MAX": 80.0,
    "TEMPERATURE_SENSOR": "auto",
    "STEAM_LIBRARY": "auto",
    "STEAM_ROUTE": "auto",
    "FPS": 60,
    "IDLE_FPS": 4,
    "RECONNECT_DELAY": 2.0,
    "LOG_LEVEL": "info",
}

_BOOL_TRUE = {"1", "true", "yes", "on"}
_BOOL_FALSE = {"0", "false", "no", "off"}


class ConfigError(ValueError):
    pass


def _coerce(key, value, template):
    if isinstance(template, bool):
        lowered = str(value).strip().lower()
        if lowered in _BOOL_TRUE:
            return True
        if lowered in _BOOL_FALSE:
            return False
        raise ConfigError("%s: expected a boolean, got %r" % (key, value))
    try:
        if isinstance(template, int):
            return int(str(value).strip(), 0)
        if isinstance(template, float):
            return float(str(value).strip())
    except ValueError:
        raise ConfigError("%s: expected a number, got %r" % (key, value))
    return str(value).strip()


def _strip_quotes(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def parse_file(path):
    values = {}
    carried = {}
    with open(path, "r") as handle:
        for lineno, raw in enumerate(handle, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                raise ConfigError("%s:%d: expected KEY=value" % (path, lineno))
            key, _, value = line.partition("=")
            key = key.strip().upper()
            if key in RETIRED:
                moved = _migrate(key, _strip_quotes(value))
                LOG.warning("%s:%d: %s is no longer a setting - %s. %s; the "
                            "control panel drops the line the next time it "
                            "saves.",
                            path, lineno, key, RETIRED[key],
                            "carried over as "
                            + ", ".join("%s=%s" % (new, format_value(setting))
                                        for new, setting in moved.items())
                            if moved else "it is ignored")
                carried.update(moved)
                continue
            if key not in DEFAULTS:
                raise ConfigError("%s:%d: unknown option %r" % (path, lineno, key))
            values[key] = _coerce(key, _strip_quotes(value), DEFAULTS[key])

    # Only where the file did not say it itself: a line naming the new option
    # is the reader's actual intention, and outranks whatever the old one
    # would have translated to regardless of which came first.
    for key, value in carried.items():
        values.setdefault(key, value)
    return values


def _migrate(key, value):
    """What a withdrawn option now sets, as {key: value}. Usually nothing."""
    target = MIGRATED.get(key)
    if target is None:
        return {}
    new_key, table = target
    template = next(iter(table))    # the old option's type, from its own keys
    try:
        old = _coerce(key, value, template)
    except ConfigError:
        # A setting we no longer accept, spelled wrongly. Refusing to start
        # over a line we are only reading out of politeness would be worse
        # than letting the new option's default stand.
        LOG.warning("%s=%s could not be carried over to %s", key, value, new_key)
        return {}
    if old not in table:
        return {}
    return {new_key: table[old]}


def format_value(value):
    """A value as the config file spells it: booleans as 1/0."""
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value)


def update_text(text, values):
    """Return `text` with these options set, leaving everything else alone.

    Rewriting from the parsed values would be shorter but would throw away
    every comment, and this file is mostly comments. So existing lines are
    edited in place and anything new is appended under a heading.
    """
    lines = text.splitlines(keepends=True)
    written = set()
    retired = []

    for index, raw in enumerate(lines):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.partition("=")[0].strip().upper()
        if key in RETIRED:
            # Out with it, or every start logs the same complaint forever.
            retired.append(index)
            continue
        if key not in values:
            continue
        # Every occurrence, not only the first. A hand-edited file can name an
        # option twice, and parse_file() takes the last one - so a duplicate
        # left behind would quietly outrank the change and nothing would say so.
        written.add(key)
        ending = "\n" if raw.endswith("\n") else ""
        lines[index] = "%s=%s%s" % (key, format_value(values[key]), ending)

    for index in reversed(retired):      # from the back, or the rest shift
        del lines[index]

    missing = [key for key in sorted(values) if key not in written]
    if missing:
        if lines and not lines[-1].endswith("\n"):
            lines.append("\n")
        lines.append("\n# Added by the control panel.\n")
        for key in missing:
            lines.append("%s=%s\n" % (key, format_value(values[key])))
    return "".join(lines)


def load(path=DEFAULT_CONFIG_PATH, overrides=None):
    """Defaults < config file < environment < CLI overrides."""
    config = dict(DEFAULTS)

    if path and os.path.exists(path):
        config.update(parse_file(path))

    for key in DEFAULTS:
        env_value = os.environ.get("STEAMOS_LED_%s" % key)
        if env_value is not None:
            config[key] = _coerce(key, env_value, DEFAULTS[key])

    for key, value in (overrides or {}).items():
        if value is None:
            continue
        config[key.upper()] = _coerce(key.upper(), value, DEFAULTS[key.upper()])

    validate(config)
    return config


MAPPINGS = ("stretch", "repeat", "crop")
# Taken from the modules that implement them, so the validator cannot drift.
NOTIFY_STYLES = notify.STYLES
RAINBOW_CHOICES = render.RAINBOW_CHOICES
DESKTOP_SCENES = desktop.SCENES

# What a desktop scene's own speed may be. Bounded by what the delay field it
# sets can carry: below the floor every multiplier lands on the module's
# slowest step and the slider stops meaning anything, and the ceiling is where
# the fastest step is reached. MIN_CYCLE_SECONDS in render.py stops even that
# from becoming a strobe.
DESKTOP_SPEED_FLOOR = 0.4
DESKTOP_SPEED_CEILING = 4.0
# One notification's own style may also say "whichever NOTIFY_STYLE says".
PER_KIND_STYLES = NOTIFY_STYLES + (notify.STYLE_INHERIT,)

# The named triggers the configuration can change, with the prefix their
# options are spelled under. One table for the defaults, the validator and the
# service, because a kind listed in one and missed in another is a setting that
# quietly does nothing and says so nowhere.
#
# "warning" is deliberately absent: it is the one notification you must not
# have to recognise, so it is always red and always the alarm shape - see
# notify.FIXED_KINDS. NOTIFY_WARNING says whether it fires at all, nothing
# more.
CONFIGURABLE_KINDS = ((notify.KIND_ACHIEVEMENT, "ACHIEVEMENT"),
                      (notify.KIND_MESSAGE, "MESSAGE"),
                      (notify.KIND_FRIEND, "FRIEND"),
                      (notify.KIND_PHONE, "PHONE"))

# Where the gauge's two marks may sit: below the first the bar is green, above
# the second it is red. Wide, because people watch different sensors, but not
# unbounded - outside this is a unit mix-up, not an intention.
TEMPERATURE_FLOOR = 0.0
TEMPERATURE_CEILING = 150.0
# And they need room between them: equal marks would divide by nothing, and a
# bar that jumps green to red says less than one that walks there.
TEMPERATURE_SPAN = 5.0


def validate(config):
    if not 1 <= config["LED_COUNT"] <= 1024:
        raise ConfigError("LED_COUNT must be between 1 and 1024")
    if config["MAPPING"] not in MAPPINGS:
        raise ConfigError("MAPPING must be one of: %s" % ", ".join(MAPPINGS))
    if config["BAUD"] not in BAUD_CONSTANTS:
        # Linux can only set rates termios has a constant for; anything else
        # fails at open() time, which looks like dead hardware.
        raise ConfigError(
            "BAUD=%s cannot be set on a Linux serial port. Supported rates: %s"
            % (config["BAUD"],
               ", ".join(str(rate) for rate in sorted(BAUD_CONSTANTS))))
    if not 1 <= config["FPS"] <= 240:
        raise ConfigError("FPS must be between 1 and 240")
    if not 1 <= config["IDLE_FPS"] <= config["FPS"]:
        raise ConfigError("IDLE_FPS must be between 1 and FPS")
    if not 0 <= config["MAX_BRIGHTNESS"] <= 255:
        raise ConfigError("MAX_BRIGHTNESS must be between 0 and 255")
    if not 0 <= config["MIN_BRIGHTNESS"] <= 255:
        raise ConfigError("MIN_BRIGHTNESS must be between 0 and 255")
    if config["GAMMA"] <= 0:
        raise ConfigError("GAMMA must be greater than 0")
    if config["SPEED"] <= 0:
        raise ConfigError("SPEED must be greater than 0")
    if not 1 <= config["PATROL_DOTS"] <= 8:
        raise ConfigError("PATROL_DOTS must be between 1 and 8")
    if not 0.1 <= config["NOTIFY_DURATION"] <= 60:
        raise ConfigError("NOTIFY_DURATION must be between 0.1 and 60 seconds")
    if not 0 <= config["NOTIFY_REPEAT_GAP"] <= 3600:
        raise ConfigError("NOTIFY_REPEAT_GAP must be between 0 and 3600 seconds")
    if config["NOTIFY_STYLE"] not in NOTIFY_STYLES:
        raise ConfigError("NOTIFY_STYLE must be one of: %s"
                          % ", ".join(NOTIFY_STYLES))
    if config["RAINBOW_SHOWS"] not in RAINBOW_CHOICES:
        raise ConfigError("RAINBOW_SHOWS must be one of: %s"
                          % ", ".join(RAINBOW_CHOICES))
    if config["DESKTOP_SCENE"] not in DESKTOP_SCENES:
        raise ConfigError("DESKTOP_SCENE must be one of: %s"
                          % ", ".join(DESKTOP_SCENES))
    try:
        notify.parse_color(config["DESKTOP_COLOR"])
    except ValueError as exc:
        raise ConfigError("DESKTOP_COLOR: %s" % exc)
    if not 0 <= config["DESKTOP_BRIGHTNESS"] <= 255:
        raise ConfigError("DESKTOP_BRIGHTNESS must be between 0 and 255")
    if not DESKTOP_SPEED_FLOOR <= config["DESKTOP_SPEED"] <= DESKTOP_SPEED_CEILING:
        raise ConfigError("DESKTOP_SPEED must be between %g and %g"
                          % (DESKTOP_SPEED_FLOOR, DESKTOP_SPEED_CEILING))
    # Which chip each half of the load gauge stands for. Any colour at all,
    # the same as every other colour setting - and deliberately not refused
    # for being two shades of the same thing. What makes the gauge readable is
    # telling the halves apart, but "too similar" is a judgement, not an
    # arithmetic fact like the temperature marks below, and a validator that
    # made it would be refusing somebody's taste.
    for key in ("LOAD_CPU_COLOR", "LOAD_GPU_COLOR"):
        try:
            notify.parse_color(config[key])
        except ValueError as exc:
            raise ConfigError("%s: %s" % (key, exc))
    for key in ("TEMPERATURE_MIN", "TEMPERATURE_MAX"):
        if not TEMPERATURE_FLOOR <= config[key] <= TEMPERATURE_CEILING:
            raise ConfigError("%s must be between %g and %g degrees"
                              % (key, TEMPERATURE_FLOOR, TEMPERATURE_CEILING))
    if config["TEMPERATURE_MAX"] - config["TEMPERATURE_MIN"] < TEMPERATURE_SPAN:
        raise ConfigError("TEMPERATURE_MAX must be at least %g degrees above "
                          "TEMPERATURE_MIN" % TEMPERATURE_SPAN)
    for _kind, prefix in CONFIGURABLE_KINDS:
        key = prefix + "_STYLE"
        if config[key] not in PER_KIND_STYLES:
            raise ConfigError("%s must be one of: %s"
                              % (key, ", ".join(PER_KIND_STYLES)))
        key = prefix + "_COLOR"
        try:
            notify.parse_color(config[key])
        except ValueError as exc:
            raise ConfigError("%s: %s" % (key, exc))
    try:
        phone.parse_rules(config["PHONE_APPS"])
    except ValueError as exc:
        raise ConfigError("PHONE_APPS: %s" % exc)
    return config
