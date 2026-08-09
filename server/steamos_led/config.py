"""Configuration loading: /etc/steamos-led-serial.conf plus CLI overrides.

Plain KEY=value lines, so shell scripts and systemd's EnvironmentFile can read
the same file.
"""

from __future__ import annotations

import os

from . import notify
from .serialport import BAUD_CONSTANTS

DEFAULT_CONFIG_PATH = "/etc/steamos-led-serial.conf"

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
    "ACHIEVEMENT_COLOR": "#ffd700",
    "MESSAGE_COLOR": "#8000ff",
    "TEMPERATURE_GAUGE": False,
    "TEMPERATURE_MIN": 40.0,
    "TEMPERATURE_MAX": 85.0,
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
    with open(path, "r") as handle:
        for lineno, raw in enumerate(handle, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                raise ConfigError("%s:%d: expected KEY=value" % (path, lineno))
            key, _, value = line.partition("=")
            key = key.strip().upper()
            if key not in DEFAULTS:
                raise ConfigError("%s:%d: unknown option %r" % (path, lineno, key))
            values[key] = _coerce(key, _strip_quotes(value), DEFAULTS[key])
    return values


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

    for index, raw in enumerate(lines):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.partition("=")[0].strip().upper()
        if key not in values:
            continue
        # Every occurrence, not only the first. A hand-edited file can name an
        # option twice, and parse_file() takes the last one - so a duplicate
        # left behind would quietly outrank the change and nothing would say so.
        written.add(key)
        ending = "\n" if raw.endswith("\n") else ""
        lines[index] = "%s=%s%s" % (key, format_value(values[key]), ending)

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
# Taken from the module that implements them, so the validator cannot drift.
NOTIFY_STYLES = notify.STYLES

# Where the gauge's two marks may sit. Wide, because people watch different
# sensors, but not unbounded: outside this is a unit mix-up, not an intention.
TEMPERATURE_FLOOR = 0.0
TEMPERATURE_CEILING = 150.0
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
    for key in ("ACHIEVEMENT_COLOR", "MESSAGE_COLOR"):
        try:
            notify.parse_color(config[key])
        except ValueError as exc:
            raise ConfigError("%s: %s" % (key, exc))
    for key in ("TEMPERATURE_MIN", "TEMPERATURE_MAX"):
        if not TEMPERATURE_FLOOR <= config[key] <= TEMPERATURE_CEILING:
            raise ConfigError("%s must be between %g and %g degrees"
                              % (key, TEMPERATURE_FLOOR, TEMPERATURE_CEILING))
    # Equal ends would divide by zero, and a bar that jumps empty to full.
    if config["TEMPERATURE_MAX"] - config["TEMPERATURE_MIN"] < TEMPERATURE_SPAN:
        raise ConfigError("TEMPERATURE_MAX must be at least %g degrees above "
                          "TEMPERATURE_MIN" % TEMPERATURE_SPAN)
    return config
