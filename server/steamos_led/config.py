"""Configuration loading: /etc/steamos-led-serial.conf plus CLI overrides.

The file uses plain KEY=value lines so it can be read by shell scripts and by
systemd's EnvironmentFile as well.
"""

from __future__ import annotations

import os

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


def validate(config):
    if not 1 <= config["LED_COUNT"] <= 1024:
        raise ConfigError("LED_COUNT must be between 1 and 1024")
    if config["MAPPING"] not in MAPPINGS:
        raise ConfigError("MAPPING must be one of: %s" % ", ".join(MAPPINGS))
    if config["BAUD"] not in BAUD_CONSTANTS:
        # Linux can only set the rates termios has a constant for; anything
        # else fails at open() time, which looks like dead hardware.
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
    return config
