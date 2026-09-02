# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""The CPU load and the GPU load, for the load gauge.

The kernel gives the two numbers through two different interfaces.

The CPU interface is /proc/stat, which publishes totals from the boot. The
caller must do the arithmetic. The load in an interval is the difference
between two readings, so the first reading gives only a baseline.

The GPU interface is sysfs, which publishes the answer as a percentage. Only
some drivers publish it. amdgpu does, and a Steam Machine has amdgpu.
"""

from __future__ import annotations

import glob
import logging
import time

from . import sampling

LOG = logging.getLogger(__name__)

CPU_STAT = "/proc/stat"

# amdgpu, and i915 on a new kernel. No other driver publishes it. A machine
# with another driver thus shows the CPU alone and not one dark half.
GPU_BUSY_GLOB = "/sys/class/drm/card*/device/gpu_busy_percent"

# The read interval of the counters, and the strength of the average.
#
# The interval is shorter than the interval of the temperature gauge, because
# the load is the immediate condition of the machine.
#
# The average is stronger than the read interval, because the load is also the
# more irregular of the two numbers. The frame pacing of a game moves the
# counters much more than the bar must follow.
READ_INTERVAL = 0.25
SMOOTHING_SECONDS = 1.0


def _read_text(path):
    try:
        with open(path, "r") as handle:
            return handle.read()
    except (OSError, ValueError):
        return None


def read_cpu_totals(path=CPU_STAT):
    """Returns (busy, total) jiffies from the boot, or None with no /proc/stat.

    Idle and iowait both count as "no work". A core that waits for the SSD
    cannot accept more work, but it also does no work.
    """
    text = _read_text(path)
    if not text:
        return None
    for line in text.splitlines():
        fields = line.split()
        # "cpu" alone is the total of the cores. "cpu0" and the others give
        # one number for each core, which is more than a bar can show.
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
    """Returns the first gpu_busy_percent it can read, or None."""
    for path in sorted(glob.glob(pattern)):
        if _read_text(path) is not None:
            return path
    return None


def read_gpu_percent(path):
    """Returns the GPU load as a fraction of 1, or None."""
    text = _read_text(path)
    if text is None:
        return None
    try:
        return max(0.0, min(int(text.strip()) / 100.0, 1.0))
    except ValueError:
        return None


class LoadSource:
    """The CPU load and the GPU load, each from 0 to 1.

    One class gives both numbers, and it gives both as an average. The gauge
    draws them as one picture. Two readings at two moments would make a frame
    that did not occur.
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
        # The last value from the counters, and the value that the bar shows
        # now. They are two values, because they move at different rates.
        self._cpu_read = None
        self._gpu_read = None
        self._cpu = None
        self._gpu = None
        self._taken = None
        self._shown = None
        self._complained = False

    def resolve(self):
        """Selects a GPU file one time. Returns the path, or None."""
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
        """Returns (cpu, gpu) as fractions of 1, or None if it reads neither.

        One entry alone can be None. A machine can have a CPU counter and no
        GPU counter. That gauge has half of the detail. It is not a gauge that
        failed.

        The read rate and the draw rate are different, and this is deliberate.
        The counters permit one read in each `interval`, because the CPU
        counters are totals and a load needs two of them. The value that this
        returns moves a small amount at each call. The bar thus moves smoothly
        at the draw rate and does not step four times each second.
        """
        now = time.monotonic() if now is None else now
        if self._taken is None or now - self._taken >= self.interval:
            self._taken = now
            self._cpu_read = self._sample_cpu()
            path = self.resolve()
            self._gpu_read = read_gpu_percent(path) if path else None

            # This is not the same as "both values are None". At the first
            # reading the CPU has no baseline to subtract. That gauge is in
            # its first interval. It is not a machine that cannot answer.
            # _totals stays None only when /proc/stat itself is unreadable.
            if self._totals is None and path is None and not self._complained:
                self._complained = True
                LOG.warning("no CPU or GPU load to read - the gauge falls back "
                            "to the rainbow")

        step = self.interval if self._shown is None else now - self._shown
        self._shown = now
        self._cpu = sampling.smooth(self._cpu, self._cpu_read, step,
                                    self.smoothing)
        self._gpu = sampling.smooth(self._gpu, self._gpu_read, step,
                                    self.smoothing)
        return self._pair()

    def _pair(self):
        if self._cpu is None and self._gpu is None:
            return None
        return self._cpu, self._gpu

    def _sample_cpu(self):
        """Returns the CPU part of the time from the last reading, 0 to 1."""
        totals = read_cpu_totals(self.stat_path)
        if totals is None:
            return None
        previous, self._totals = self._totals, totals
        if previous is None:
            # The first reading has nothing to subtract. The average from
            # the boot would show a new machine at 2% while it compiles
            # something. Report nothing for one interval instead.
            return None
        busy = totals[0] - previous[0]
        total = totals[1] - previous[1]
        if total <= 0:
            # The clock did not move between the readings, or the counters
            # returned to zero.
            return None
        return max(0.0, min(busy / float(total), 1.0))
