"""Reading how hot the machine is, from the kernel's hwmon interface.

Every sensor shows up under /sys/class/hwmon as a chip with one or more
inputs, in thousandths of a degree. The work is not reading one - it is
picking the right one out of a dozen, most of which measure something nobody
means by "how hot is it".
"""

from __future__ import annotations

import glob
import logging
import os
import time

from . import sampling

LOG = logging.getLogger(__name__)

HWMON_ROOT = "/sys/class/hwmon"

# Chips worth watching, best first. On a Steam Machine the APU is the answer:
# k10temp is its CPU side, amdgpu its graphics side; coretemp is the Intel
# equivalent, the last two turn up on handhelds and ARM boards. The SSD, the
# wifi card and the battery are real temperatures, but not the one meant.
PREFERRED_CHIPS = ("k10temp", "amdgpu", "coretemp", "cpu_thermal", "acpitz")

# Within a chip, the sensor that speaks for the whole package. Tctl is what AMD
# drives its own fan curve from; edge is the die on amdgpu.
PREFERRED_LABELS = ("tctl", "tdie", "package", "edge", "composite")


def _read_text(path):
    try:
        with open(path, "r", errors="replace") as handle:
            return handle.read().strip()
    except OSError:
        return None


def read_celsius(path):
    """One hwmon input, in degrees, or None if it will not read.

    hwmon reports thousandths: a raw 52000 is 52 C, and unconverted it would
    peg any gauge at maximum forever.
    """
    text = _read_text(path)
    if not text:
        return None
    try:
        return int(text) / 1000.0
    except ValueError:
        return None


# What a sensor may publish next to its reading, best answer first. hwmon's
# interface is optional throughout: a driver exposes what the hardware tells
# it and nothing more, so any of these may be missing on any sensor.
#
#   emergency - throw the switch now (amdgpu publishes it)
#   crit      - the manufacturer's critical point
#   max       - where the part expects to be throttled
#
# Worth reading because "hot" is not one number: an APU sitting at 95 C is
# doing what it was designed to do, while an NVMe drive is long past its own
# limit there. The part knows its own numbers; we do not.
LIMIT_FILES = ("emergency", "crit", "max")

# Set by the kernel, not by us: the driver's own opinion that a limit has been
# passed. Where it exists it beats any threshold of ours.
ALARM_FILES = ("crit_alarm", "emergency_alarm", "max_alarm")


# Above this a "limit" is not one. NVMe stores its thresholds as 16 bit Kelvin
# and spells "not implemented" as 0xFFFF, which the driver passes through
# verbatim: 65535 K is 65261.85 C, and it turns up on real drives.
SANE_LIMIT = 200.0


def read_limits(path):
    """The limits a sensor publishes, in degrees, keyed as in LIMIT_FILES.

    Missing files are simply absent from the result - most sensors publish
    some of these and none publish all. So are implausible ones, which are a
    disabled threshold rather than a hot part.
    """
    limits = {}
    for name in LIMIT_FILES:
        value = read_celsius(path.replace("_input", "_" + name))
        if value is not None and value <= SANE_LIMIT:
            limits[name] = value
    return limits


def read_alarms(path):
    """Which of the sensor's own alarm flags are currently raised."""
    raised = []
    for name in ALARM_FILES:
        text = _read_text(path.replace("_input", "_" + name))
        if text and text.strip() not in ("0", ""):
            raised.append(name)
    return raised


def find_sensors(root=HWMON_ROOT):
    """Every temperature input on the machine, as dicts.

    Each has chip, label, path and rank - lower is a better answer to "how hot
    is it".
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
    """How good an answer this sensor is; lower is better."""
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

    # The chip decides first, or a well-labelled SSD sensor would beat an
    # unlabelled CPU one.
    return (chip_rank, label_rank)


def pick_sensor(sensors):
    """The best of them, or None when the machine reports no temperature."""
    if not sensors:
        return None
    return min(sensors, key=lambda sensor: sensor["rank"])


# How long a reading is kept, and how hard readings are smoothed. A CPU sensor
# is noisy in a way the eye picks up: Tctl jumps a degree or two second to
# second while nothing is happening, which over the gauge's 45 degree span is
# most of an LED - so the leading one would flicker on every reading. A few
# seconds of averaging settles it without hiding a real warm-up.
READ_INTERVAL = 1.0
SMOOTHING_SECONDS = 6.0


class TemperatureSource:
    """The current temperature, read no more often than it changes.

    The render loop runs at up to 60 frames a second while a CPU temperature
    moves on the scale of seconds, so a reading is kept for `interval` and
    handed out smoothed - see SMOOTHING_SECONDS.
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
        """Settle on a sensor, once. Returns the path, or None."""
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
        """The smoothed temperature, or None if there is nothing to read."""
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
        """Move the reported value part of the way towards a new sample."""
        return sampling.smooth(self._value, sample, elapsed, self.smoothing)


# -- watching every sensor for one that stays too hot ----------------------
#
# A different question from the gauge's, and deliberately a separate object:
# the gauge shows one sensor you chose, smoothed hard so the leading LED does
# not flicker. This reads all of them, unsmoothed, and cares about how long
# one has been high. Sharing a reader would have meant one of the two getting
# data shaped for the other.

# Warn this far below the part's own critical point, so there is time to
# notice before the hardware acts on it.
OVERHEAT_MARGIN = 5.0
# How long a sensor has to stay there. A CPU touches its limit for a fraction
# of a second whenever it boosts; a minute of it is a cooling problem.
OVERHEAT_DWELL = 60.0
# How far it has to fall before that sensor may warn again, so one sitting
# exactly on the threshold does not warn every time it wobbles.
OVERHEAT_RELEASE = 5.0
# And a floor between two warnings whatever tripped them. The bar cannot say
# more than "something is too hot"; saying it every minute adds nothing.
OVERHEAT_QUIET = 300.0
# All the sensors, this often. They move on the scale of seconds and the
# dwell is a minute, so this is frequent enough to be exact and rare enough
# to be free.
OVERHEAT_INTERVAL = 5.0

# Which published limit to measure against, best first. "max" is deliberately
# not here: it means whatever a driver wants it to. A DDR5 module reports
# max 55 with crit 85, an Ethernet controller reports max 120 and no crit -
# thresholding on that would warn about a warm DIMM in a warm room.
LIMIT_SOURCES = ("crit", "emergency")


class OverheatWatch:
    """Reports a sensor that has stayed close to its own limit for a while.

    Every threshold comes from the part, not from a number written here: what
    is hot depends entirely on what is being measured. An APU sitting at 95 C
    is doing what it was designed to do; an NVMe drive at 95 C is past its
    critical point. Sensors that publish no limit are not watched at all,
    because guessing one for an unknown part is how false alarms are made.
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
        """Settle on what to watch and at which temperature, once."""
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
        """A sentence describing what is too hot, or None.

        Cheap to call every frame: it reads nothing until `interval` has
        passed, so the render loop can ask without thinking about it.
        """
        if self._next_read is not None and now < self._next_read:
            return None
        self._next_read = now + self.interval

        reason = None
        for sensor, threshold in self.resolve():
            path = sensor["path"]
            celsius = read_celsius(path)
            if celsius is None:
                # A sensor that stopped answering has not been hot for a
                # minute; it has been absent. Start it over if it returns.
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
