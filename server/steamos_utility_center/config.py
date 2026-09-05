# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""The configuration: /etc/steamos-utility-center.conf and the CLI options.

The file has plain KEY=value lines. A shell script and the EnvironmentFile of
systemd can thus read the same file.
"""

from __future__ import annotations

import logging
import os

from . import desktop
from . import link
from . import notify
from . import phone
from . import render
from .serialport import BAUD_CONSTANTS

LOG = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = "/etc/steamos-utility-center.conf"

# The options that existed before and do not exist now, with their result.
#
# An unknown key stops the service, and this is deliberate. Without that rule,
# LED_COUTN=60 does nothing and says nothing.
#
# But a key that *this project* removed is not a mistake of the reader. To
# refuse to start because of one makes an old line into a service that does
# not start. This project thus reads these keys, ignores them, reports them
# one time, and removes them at the next write by the panel.
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

# The removed options that still carry a setting worth keeping, and how to
# read it: {old: (new, {old value: new value})}.
#
# To ignore an option is correct when the option lost its meaning. But
# TEMPERATURE_GAUGE=1 means exactly one of the values that replaced it. To
# remove it would switch a gauge off while a person watches it, with no error
# and with nothing to search for. This project thus translates it instead.
#
# A new setting in the file always has priority, in either order.
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
    # What the ESP draws while the machine sleeps, and in which colour.
    #
    # The colour and the level were constants in service.py, and the level is
    # the reason they are two settings and not one. A colour from the menu is
    # a colour at full strength, and a standby light at full strength is a
    # night light. White at 30 of 255 is what this was before either setting
    # existed, so a machine that upgrades sees no change.
    "STANDBY_SHOWS": "breath",
    "STANDBY_COLOR": "#ffffff",
    "STANDBY_BRIGHTNESS": 30,
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
    "LOAD_SWAP": False,
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

    # Only where the file does not set it. A line with the new option is the
    # intention of the reader. It thus has priority over the translation of the
    # old option, in either order.
    for key, value in carried.items():
        values.setdefault(key, value)
    return values


def _migrate(key, value):
    """Returns what a removed option sets, as {key: value}. Usually {}."""
    target = MIGRATED.get(key)
    if target is None:
        return {}
    new_key, table = target
    template = next(iter(table))    # the old option's type, from its own keys
    try:
        old = _coerce(key, value, template)
    except ConfigError:
        # A setting that this project does not accept, with a spelling error.
        # This project reads the line only for the reader. To refuse to start
        # is worse than the default of the new option.
        LOG.warning("%s=%s could not be carried over to %s", key, value, new_key)
        return {}
    if old not in table:
        return {}
    return {new_key: table[old]}


def format_value(value):
    """Returns a value in the form of the file: 1 or 0 for a boolean."""
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value)


def update_text(text, values):
    """Returns `text` with these options set. It changes nothing else.

    A write from the parsed values is shorter, but it removes each comment,
    and most of this file is comments. This function thus edits the lines
    that are there and adds a new option below a heading.
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
            # Remove it. Without this, each start reports the same line.
            retired.append(index)
            continue
        if key not in values:
            continue
        # Each occurrence, and not only the first. A file that a person edited
        # can name an option two times, and parse_file() takes the last one. A
        # duplicate that stays thus has priority, and nothing reports it.
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
    """The order of priority: defaults, file, environment, CLI options."""
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
# These come from the modules that implement them. The validator thus cannot
# become different from the implementation.
NOTIFY_STYLES = notify.STYLES
RAINBOW_CHOICES = render.RAINBOW_CHOICES
DESKTOP_SCENES = desktop.SCENES

# What the ESP can draw while the machine sleeps. The name is here and the
# number is in link.py: this file holds what a person writes, and the protocol
# holds what goes on the wire.
#
# The breath is first because it is what the bar did before there was a
# choice, and because a board with an old firmware draws it whatever this
# says. See link.send_standby.
STANDBY_SHAPES = {
    "breath": link.STANDBY_BREATH,
    "dot": link.STANDBY_DOT,
}

# The permitted speed of a desktop scene. The limits are the limits of the
# delay field that the speed sets.
#
# Below the low limit, each multiplier gives the slowest step of the module and
# the control has no effect. The high limit is the fastest step.
#
# MIN_CYCLE_SECONDS in render.py stops even the fastest step from becoming a
# strobe.
DESKTOP_SPEED_FLOOR = 0.4
DESKTOP_SPEED_CEILING = 4.0
# The style of one notification can also be "the style in NOTIFY_STYLE".
PER_KIND_STYLES = NOTIFY_STYLES + (notify.STYLE_INHERIT,)

# The named triggers that the configuration can change, with the prefix of
# their options.
#
# One table serves the defaults, the validator and the service. A kind in one
# table and not in another is a setting that does nothing and reports nothing.
#
# "warning" is deliberately not here. It is the one notification that a person
# must recognise with no learning, so it is always red and always the alarm
# shape. See notify.FIXED_KINDS. NOTIFY_WARNING controls only whether it
# flashes.
CONFIGURABLE_KINDS = ((notify.KIND_ACHIEVEMENT, "ACHIEVEMENT"),
                      (notify.KIND_MESSAGE, "MESSAGE"),
                      (notify.KIND_FRIEND, "FRIEND"),
                      (notify.KIND_PHONE, "PHONE"))

# The permitted positions of the two marks of the gauge. Below the first mark
# the bar is green. Above the second mark it is red.
#
# The range is wide, because people watch different sensors. It is not
# unlimited: a value outside it is a mistake in the unit and not an intention.
TEMPERATURE_FLOOR = 0.0
TEMPERATURE_CEILING = 150.0
# The two marks also need a distance between them. Equal marks make a division
# by zero. A bar that changes from green to red in one step says less than a
# bar that moves through yellow.
TEMPERATURE_SPAN = 5.0


def validate(config):
    if not 1 <= config["LED_COUNT"] <= 1024:
        raise ConfigError("LED_COUNT must be between 1 and 1024")
    if config["MAPPING"] not in MAPPINGS:
        raise ConfigError("MAPPING must be one of: %s" % ", ".join(MAPPINGS))
    if config["BAUD"] not in BAUD_CONSTANTS:
        # Linux sets only the rates that termios has a constant for. Each other
        # rate fails at the open, and that looks like defective hardware.
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
    if config["STANDBY_SHOWS"] not in STANDBY_SHAPES:
        raise ConfigError("STANDBY_SHOWS must be one of: %s"
                          % ", ".join(sorted(STANDBY_SHAPES)))
    try:
        notify.parse_color(config["STANDBY_COLOR"])
    except ValueError as exc:
        raise ConfigError("STANDBY_COLOR: %s" % exc)
    if not 0 <= config["STANDBY_BRIGHTNESS"] <= 255:
        raise ConfigError("STANDBY_BRIGHTNESS must be between 0 and 255")
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
    # The chip that each half of the load gauge shows. Each setting accepts any
    # colour, as each other colour setting does.
    #
    # This project deliberately does not refuse two shades of one colour. The
    # gauge is readable when a person can see the difference between the halves.
    # But "too similar" is a judgement and not an arithmetic fact, as the
    # temperature marks below are. A validator that made that judgement would
    # refuse the taste of a person.
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
