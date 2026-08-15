# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""How busy the CPU and the GPU are, for the load gauge.

Two very different kernel interfaces for the same kind of number. The CPU
publishes running totals in /proc/stat and expects you to do the arithmetic:
the load over an interval is the difference between two readings, so the first
one can only ever produce a baseline. The GPU publishes the answer directly in
sysfs as a percentage, but only on drivers that bother - amdgpu does, which is
what a Steam Machine has.
"""

from __future__ import annotations

import glob
import logging
import time

from . import sampling

LOG = logging.getLogger(__name__)

CPU_STAT = "/proc/stat"

# amdgpu, and i915 on new enough kernels. Nothing else publishes it, so a
# machine without one shows the CPU alone rather than half a dark bar.
GPU_BUSY_GLOB = "/sys/class/drm/card*/device/gpu_busy_percent"

# Faster than the temperature gauge and smoothed far less: load is what the
# machine is doing right now, and a meter that lags a second behind the thing
# you just started is not showing you the thing you just started.
READ_INTERVAL = 0.25
SMOOTHING_SECONDS = 0.6


def _read_text(path):
    try:
        with open(path, "r") as handle:
            return handle.read()
    except (OSError, ValueError):
        return None


def read_cpu_totals(path=CPU_STAT):
    """(busy, total) jiffies since boot, or None if /proc/stat is not there.

    Idle and iowait are both "not working": a core waiting on the SSD is not
    one you can give more work to, but it is also not one that is doing any.
    """
    text = _read_text(path)
    if not text:
        return None
    for line in text.splitlines():
        fields = line.split()
        # "cpu" alone is the total across cores; "cpu0" and friends are the
        # per-core breakdown, which is one number more than a bar can show.
        if not fields or fields[0] != "cpu":
            continue
        try:
            times = [int(field) for field in fields[1:]]
        except ValueError:
            return None
        if len(times) < 4:
            return None
        total = sum(times)
        idle = times[3] + (times[4] if len(times) > 4 else 0)
        return total - idle, total
    return None


def find_gpu_busy(pattern=GPU_BUSY_GLOB):
    """The first readable gpu_busy_percent, or None on a driver without one."""
    for path in sorted(glob.glob(pattern)):
        if _read_text(path) is not None:
            return path
    return None


def read_gpu_percent(path):
    """The GPU's busy percentage as a fraction, or None."""
    text = _read_text(path)
    if text is None:
        return None
    try:
        return max(0.0, min(int(text.strip()) / 100.0, 1.0))
    except ValueError:
        return None


class LoadSource:
    """How busy the two big chips are, each 0..1, read at a sane rate.

    Both are handed out smoothed and from one place, because they are drawn
    as one picture: reading them at different moments would show a frame that
    never actually happened.
    """

    def __init__(self, interval=READ_INTERVAL, smoothing=SMOOTHING_SECONDS,
                 stat_path=CPU_STAT, gpu_pattern=GPU_BUSY_GLOB):
        self.interval = max(0.0, float(interval))
        self.smoothing = max(0.0, float(smoothing))
        self.stat_path = stat_path
        self.gpu_pattern = gpu_pattern
        self.gpu_path = None
        self._resolved = False
        self._totals = None
        self._cpu = None
        self._gpu = None
        self._taken = None
        self._complained = False

    def resolve(self):
        """Settle on a GPU file, once. Returns the path, or None."""
        if not self._resolved:
            self._resolved = True
            self.gpu_path = find_gpu_busy(self.gpu_pattern)
            if self.gpu_path:
                LOG.info("reading GPU load from %s", self.gpu_path)
            else:
                LOG.info("no GPU load counter on this machine - the gauge "
                         "shows the CPU alone")
        return self.gpu_path

    def fractions(self, now=None):
        """(cpu, gpu) as fractions of 1, or None when neither can be read.

        Either entry may be None on its own: a machine can have a CPU counter
        and no GPU one, which is a gauge with something to say and half the
        detail, not a gauge that has failed.
        """
        now = time.monotonic() if now is None else now
        if self._taken is not None and now - self._taken < self.interval:
            return self._pair()

        elapsed = self.interval if self._taken is None else now - self._taken
        self._taken = now
        self._cpu = sampling.smooth(self._cpu, self._sample_cpu(), elapsed,
                                    self.smoothing)
        path = self.resolve()
        self._gpu = sampling.smooth(self._gpu,
                                    read_gpu_percent(path) if path else None,
                                    elapsed, self.smoothing)

        # Not "both values are None": on the first reading the CPU has no
        # baseline to subtract from yet, which is a gauge warming up rather
        # than a machine that cannot answer. _totals stays None only when
        # /proc/stat itself could not be read.
        if self._totals is None and path is None and not self._complained:
            self._complained = True
            LOG.warning("no CPU or GPU load to read - the gauge falls back to "
                        "the rainbow")
        return self._pair()

    def _pair(self):
        if self._cpu is None and self._gpu is None:
            return None
        return self._cpu, self._gpu

    def _sample_cpu(self):
        """The CPU's share of the time since the last reading, 0..1."""
        totals = read_cpu_totals(self.stat_path)
        if totals is None:
            return None
        previous, self._totals = self._totals, totals
        if previous is None:
            # The very first reading has nothing to subtract from. Reporting
            # the since-boot average here would show a fresh machine at 2%
            # while it compiles something, so say nothing for one interval.
            return None
        busy = totals[0] - previous[0]
        total = totals[1] - previous[1]
        if total <= 0:
            # The clock did not tick between readings, or the counters wrapped.
            return None
        return max(0.0, min(busy / float(total), 1.0))
