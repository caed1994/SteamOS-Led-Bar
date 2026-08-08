"""Reading how hot the machine is, from the kernel's hwmon interface.

Every temperature sensor a Linux machine has shows up under /sys/class/hwmon
as a chip with one or more inputs, in thousandths of a degree. The work is not
reading one - it is picking the right one out of a dozen, most of which
measure something nobody means by "how hot is it".
"""

from __future__ import annotations

import glob
import logging
import os
import time

LOG = logging.getLogger(__name__)

HWMON_ROOT = "/sys/class/hwmon"

# Chips worth watching, best first. On a Steam Machine the APU is the answer:
# k10temp is its CPU side, amdgpu its graphics side. coretemp is the Intel
# equivalent, and the last two turn up on handhelds and ARM boards. Everything
# else a machine reports - the SSD, the wifi card, the battery - is a real
# temperature but not the one anybody means.
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

    hwmon reports thousandths of a degree - a raw 52000 is 52 C, and reporting
    it unconverted would peg any gauge at maximum forever.
    """
    text = _read_text(path)
    if not text:
        return None
    try:
        return int(text) / 1000.0
    except ValueError:
        return None


def find_sensors(root=HWMON_ROOT):
    """Every temperature input on the machine, as dicts.

    Each has: chip (the driver's name), label (what the input measures, if it
    says), path, and rank - lower is a better answer to "how hot is it".
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

    # The chip decides first: a well-labelled SSD sensor must not beat an
    # unlabelled CPU one.
    return (chip_rank, label_rank)


def pick_sensor(sensors):
    """The best of them, or None when the machine reports no temperature."""
    if not sensors:
        return None
    return min(sensors, key=lambda sensor: sensor["rank"])


class TemperatureSource:
    """The current temperature, read no more often than it changes.

    Sysfs is cheap but not free, and the render loop runs at up to 60 frames a
    second while a CPU temperature moves on the scale of seconds. So a reading
    is kept for `interval` and handed out until it goes stale.
    """

    def __init__(self, path="auto", interval=1.0, root=HWMON_ROOT):
        self.wanted = path
        self.interval = max(0.0, float(interval))
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
        """The temperature, or None if there is nothing to read."""
        now = time.monotonic() if now is None else now
        if self._taken is not None and now - self._taken < self.interval:
            return self._value

        path = self.resolve()
        value = read_celsius(path) if path else None
        self._taken = now
        self._value = value

        if value is None and not self._complained:
            self._complained = True
            LOG.warning("no temperature to read%s",
                        " at " + path if path else " - no sensor found")
        return value
