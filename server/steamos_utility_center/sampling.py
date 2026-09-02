# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""The average that the gauges use for a number from /sys or /proc.

The render loop runs at 60 frames each second. The numbers that it shows
change over seconds. Without an average, the first LED moves at each reading.

Both gauges thus use this function. One function keeps the two gauges the
same. Two copies become different.
"""

from __future__ import annotations


def smooth(previous, sample, elapsed, seconds):
    """Moves `previous` part of the distance to `sample`.

    The time that passed sets the size of the step. It is not a fixed
    fraction, so a lost frame does not change the speed of the gauge.
    `seconds` is approximately the time to cover most of the distance to a new
    value.

    Three conditions give the sample itself: no sample, no history, and no
    average. At the first reading there is nothing to average against. For a
    sensor that stops to answer, a remembered value is not true.
    """
    if sample is None or previous is None or seconds <= 0:
        return sample
    weight = elapsed / (elapsed + seconds)
    return previous + (sample - previous) * weight
