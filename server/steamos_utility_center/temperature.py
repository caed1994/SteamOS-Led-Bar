# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""The temperature of the machine, from the hwmon interface of the kernel.

Each sensor is under /sys/class/hwmon as a chip with one or more inputs. Each
input gives thousandths of a degree.

The difficult part is not the read. It is the selection of the correct sensor
from approximately twelve sensors. Most of them measure something that a person
does not mean by "how hot is the machine".
"""

from __future__ import annotations

import glob
import logging
import os
import time

from . import sampling

LOG = logging.getLogger(__name__)

HWMON_ROOT = "/sys/class/hwmon"

# The chips to watch, the best first.
#
# On a Steam Machine the APU is the answer. k10temp is its CPU side and amdgpu
# is its graphics side. coretemp is the Intel equivalent. The last two chips
# are on handheld machines and ARM boards.
#
# The SSD, the wifi card and the battery give real temperatures, but not the
# temperature that a person means.
PREFERRED_CHIPS = ("k10temp", "amdgpu", "coretemp", "cpu_thermal", "acpitz")

# In a chip, the sensor for the full package. AMD drives its own fan curve
# from Tctl. On amdgpu, edge is the die.
PREFERRED_LABELS = ("tctl", "tdie", "package", "edge", "composite")


def _read_text(path):
    try:
        with open(path, "r", errors="replace") as handle:
            return handle.read().strip()
    except OSError:
        return None


def read_celsius(path):
    """Returns one hwmon input in degrees, or None if the read fails.

    hwmon reports thousandths of a degree. A raw 52000 is 52 C. Without the
    conversion, each gauge stays at its maximum.
    """
    text = _read_text(path)
    if not text:
        return None
    try:
        return int(text) / 1000.0
    except ValueError:
        return None


# What a sensor can publish beside its reading, the best answer first.
#
# Each part of the hwmon interface is optional. A driver publishes what the
# hardware reports and nothing more, so a sensor can have none of these.
#
#   emergency - stop the machine now (amdgpu publishes it)
#   crit      - the critical point of the manufacturer
#   max       - the point at which the part expects a throttle
#
# These limits are worth a read, because "hot" is not one number. An APU at
# 95 C operates as its design intends. An NVMe drive at 95 C is far above its
# own limit. The part knows its own numbers. This project does not.
LIMIT_FILES = ("emergency", "crit", "max")

# The kernel sets these, and this project does not. Each one is the driver's
# own statement that the part is above a limit. Where one exists, it has
# priority over each threshold of ours.
ALARM_FILES = ("crit_alarm", "emergency_alarm", "max_alarm")


# Above this value, a "limit" is not a limit. NVMe keeps its thresholds as 16
# bit Kelvin and writes "not implemented" as 0xFFFF. The driver passes that
# value through without a change. 65535 K is 65261.85 C, and real drives
# report it.
SANE_LIMIT = 200.0


def read_limits(path):
    """Returns the limits of a sensor in degrees, keyed as in LIMIT_FILES.

    A file that does not exist is not in the result. Most sensors publish some
    of these limits and no sensor publishes all of them.

    A value above LIMIT_CEILING is also not in the result. Such a value is a
    disabled threshold and not a hot part.
    """
    limits = {}
    for name in LIMIT_FILES:
        value = read_celsius(path.replace("_input", "_" + name))
        if value is not None and value <= SANE_LIMIT:
            limits[name] = value
    return limits


def read_alarms(path):
    """Returns the alarm flags of the sensor that are set now."""
    raised = []
    for name in ALARM_FILES:
        text = _read_text(path.replace("_input", "_" + name))
        if text and text.strip() not in ("0", ""):
            raised.append(name)
    return raised


def find_sensors(root=HWMON_ROOT):
    """Returns each temperature input on the machine, as a dictionary.

    Each dictionary has chip, label, path and rank. A lower rank is a better
    answer to the question "how hot is the machine".
    """
    found = []
    for chip_dir in sorted(glob.glob(os.path.join(root, "hwmon*"))):
        chip = _read_text(os.path.join(chip_dir, "name")) or "?"
        for path in sorted(glob.glob(os.path.join(chip_dir, "temp*_input"))):
            label = _read_text(path.replace("_input", "_label")) or ""
            found.append({
                "chip": chip,
                "label": label,
                "path": path,
                "rank": _rank(chip, label),
            })
    return found


def _rank(chip, label):
    """Returns the quality of this sensor as an answer. Lower is better."""
    lowered_chip = chip.lower()
    for position, name in enumerate(PREFERRED_CHIPS):
        if lowered_chip == name:
            chip_rank = position
            break
    else:
        chip_rank = len(PREFERRED_CHIPS)

    lowered_label = label.lower()
    for position, name in enumerate(PREFERRED_LABELS):
        if name in lowered_label:
            label_rank = position
            break
    else:
        label_rank = len(PREFERRED_LABELS)

    # The chip has priority. Without this, an SSD sensor with a good label
    # would have priority over a CPU sensor with no label.
    return (chip_rank, label_rank)


def pick_sensor(sensors):
    """Returns the best sensor, or None if the machine reports none."""
    if not sensors:
        return None
    return min(sensors, key=lambda sensor: sensor["rank"])


# The time that a reading is kept, and the strength of the average.
#
# A CPU sensor is irregular, and the eye sees it. Tctl moves one or two degrees
# from second to second while the machine does nothing. Over the 45 degree
# range of the gauge, that is most of one LED, and the first LED would thus
# move at each reading.
#
# An average over some seconds stops the movement. It does not hide a real
# increase in the temperature.
READ_INTERVAL = 1.0
SMOOTHING_SECONDS = 6.0


class TemperatureSource:
    """The current temperature, read at the rate at which it changes.

    The render loop runs at 60 frames each second. A CPU temperature changes
    over seconds. This class thus keeps a reading for `interval` and returns an
    average. See SMOOTHING_SECONDS.
    """

    def __init__(self, path="auto", interval=READ_INTERVAL,
                 smoothing=SMOOTHING_SECONDS, root=HWMON_ROOT):
        self.wanted = path
        self.interval = max(0.0, float(interval))
        self.smoothing = max(0.0, float(smoothing))
        self.root = root
        self.path = None
        self.chip = None
        self._value = None
        self._taken = None
        self._complained = False

    def resolve(self):
        """Selects a sensor one time. Returns the path, or None."""
        if self.path is not None:
            return self.path
        if self.wanted and self.wanted != "auto":
            self.path, self.chip = self.wanted, "configured"
            return self.path

        best = pick_sensor(find_sensors(self.root))
        if best is None:
            return None
        self.path, self.chip = best["path"], best["chip"]
        LOG.info("reading temperature from %s (%s%s)", self.path, self.chip,
                 ", " + best["label"] if best["label"] else "")
        return self.path

    def celsius(self, now=None):
        """Returns the average temperature, or None if it can read none."""
        now = time.monotonic() if now is None else now
        if self._taken is not None and now - self._taken < self.interval:
            return self._value

        path = self.resolve()
        sample = read_celsius(path) if path else None
        elapsed = self.interval if self._taken is None else now - self._taken
        self._taken = now
        self._value = self._smooth(sample, elapsed)

        if sample is None and not self._complained:
            self._complained = True
            LOG.warning("no temperature to read%s",
                        " at " + path if path else " - no sensor found")
        return self._value

    def _smooth(self, sample, elapsed):
        """Moves the reported value part of the distance to a new sample."""
        return sampling.smooth(self._value, sample, elapsed, self.smoothing)


# -- the watch for a sensor that stays too hot -----------------------------
#
# This is a different question from the question of the gauge, and it is thus a
# separate class.
#
# The gauge shows one sensor that a person selected. It uses a strong average,
# so that the first LED does not move.
#
# This class reads each sensor with no average. It measures the time that a
# sensor stays high. One reader for both would give one of the two the data
# for the other.

# The distance below the critical point of the part at which this warns. It
# gives a person time to see the warning before the hardware acts.
OVERHEAT_MARGIN = 5.0
# The time that a sensor must stay there. A CPU reaches its limit for a small
# part of a second at each boost. One minute at the limit is a cooling fault.
OVERHEAT_DWELL = 60.0
# The distance that the temperature must fall before that sensor can warn
# again. A sensor exactly at the threshold thus does not warn at each small
# movement.
OVERHEAT_RELEASE = 5.0
# The minimum time between two warnings, whatever caused them. The bar can say
# only "something is too hot". To repeat that each minute adds nothing.
OVERHEAT_QUIET = 300.0
# The read interval for each sensor. The sensors change over seconds and the
# dwell is one minute. This interval is thus short enough to be exact and long
# enough to cost nothing.
OVERHEAT_INTERVAL = 5.0

# The published limit to measure against, the best first.
#
# "max" is deliberately not here. Its meaning depends on the driver. A DDR5
# module reports max 55 with crit 85. An Ethernet controller reports max 120
# and no crit. A threshold on "max" would thus warn about a warm memory module
# in a warm room.
LIMIT_SOURCES = ("crit", "emergency")


class OverheatWatch:
    """Reports a sensor that stays near its own limit for one minute.

    Each threshold comes from the part and not from a number in this file. The
    meaning of "hot" depends on the measured part. An APU at 95 C operates as
    its design intends. An NVMe drive at 95 C is above its critical point.

    This class does not watch a sensor that publishes no limit. A guess for an
    unknown part makes false alarms.
    """

    def __init__(self, root=HWMON_ROOT, margin=OVERHEAT_MARGIN,
                 dwell=OVERHEAT_DWELL, release=OVERHEAT_RELEASE,
                 quiet=OVERHEAT_QUIET, interval=OVERHEAT_INTERVAL):
        self.root = root
        self.margin = margin
        self.dwell = dwell
        self.release = release
        self.quiet = quiet
        self.interval = interval
        self.watched = None         # [(sensor, threshold)], resolved once
        self._hot_since = {}        # path -> when it first went over
        self._armed = {}            # path -> may warn again
        self._next_read = None
        self._quiet_until = 0.0

    def resolve(self):
        """Selects what to watch and at which temperature, one time."""
        if self.watched is not None:
            return self.watched

        self.watched = []
        sensors = find_sensors(self.root)
        for sensor in sensors:
            limits = read_limits(sensor["path"])
            for name in LIMIT_SOURCES:
                if name in limits:
                    threshold = limits[name] - self.margin
                    self.watched.append((sensor, threshold))
                    self._armed[sensor["path"]] = True
                    LOG.debug("watching %s (%s%s) above %.1f C, its %s is %g",
                              sensor["path"], sensor["chip"],
                              ", " + sensor["label"] if sensor["label"] else "",
                              threshold, name, limits[name])
                    break

        if not self.watched:
            LOG.warning("no sensor on this machine publishes a limit of its "
                        "own, so there is nothing to compare against and "
                        "overheat warnings stay off")
        else:
            LOG.info("watching %d of %d sensors for overheating",
                     len(self.watched), len(sensors))
        return self.watched

    def poll(self, now):
        """Returns a sentence about what is too hot, or None.

        A call at each frame costs little. It reads nothing until `interval`
        passes, so the render loop can call it at each frame.
        """
        if self._next_read is not None and now < self._next_read:
            return None
        self._next_read = now + self.interval

        reason = None
        for sensor, threshold in self.resolve():
            path = sensor["path"]
            celsius = read_celsius(path)
            if celsius is None:
                # A sensor that stops to answer was not hot for one minute.
                # It was absent. Start its timer again if it returns.
                self._hot_since.pop(path, None)
                continue

            if celsius < threshold - self.release:
                self._armed[path] = True
            if celsius < threshold:
                self._hot_since.pop(path, None)
                continue

            since = self._hot_since.setdefault(path, now)
            if reason is None and self._armed.get(path) \
                    and now - since >= self.dwell and now >= self._quiet_until:
                reason = ("%s%s has been at %.1f C for %d s, and warns above "
                          "%.1f C" % (sensor["chip"],
                                      " " + sensor["label"]
                                      if sensor["label"] else "",
                                      celsius, int(now - since), threshold))
                self._armed[path] = False
                self._quiet_until = now + self.quiet
        return reason
