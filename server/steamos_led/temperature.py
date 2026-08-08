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


# How long a reading is kept, and how hard the readings are smoothed.
#
# A CPU sensor is noisy in a way the eye picks up immediately: Tctl jumps a
# degree or two between one second and the next while the machine is doing
# nothing in particular. Over the gauge's 45 degree span that is most of an
# LED, so the leading one - the only one lit part way - would flicker on every
# reading. Averaging over a few seconds settles it without hiding a real
# warm-up, which takes far longer than that.
READ_INTERVAL = 1.0
SMOOTHING_SECONDS = 6.0


class TemperatureSource:
    """The current temperature, read no more often than it changes.

    Sysfs is cheap but not free, and the render loop runs at up to 60 frames a
    second while a CPU temperature moves on the scale of seconds. So a reading
    is kept for `interval` and handed out until it goes stale, and what is
    handed out is smoothed - see SMOOTHING_SECONDS.
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
        """Move the reported value part of the way towards a new sample.

        The step is sized by how long it has been rather than by a fixed
        fraction, so a skipped frame or a slower loop does not change how
        quickly the gauge follows.
        """
        if sample is None or self._value is None or self.smoothing <= 0:
            # Nothing to smooth against - the first reading, or a sensor that
            # stopped answering. Report the truth rather than a memory of it.
            return sample
        weight = elapsed / (elapsed + self.smoothing)
        return self._value + (sample - self._value) * weight
