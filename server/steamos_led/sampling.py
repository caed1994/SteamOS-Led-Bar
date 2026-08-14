"""Smoothing shared by the things that read a number out of /sys and /proc.

The render loop runs at up to sixty frames a second while the numbers it shows
move on the scale of seconds. Handing them over raw makes the leading LED
twitch on every reading, so both gauges average - and they average the same
way, from here, rather than each growing its own version of it.
"""

from __future__ import annotations


def smooth(previous, sample, elapsed, seconds):
    """Move `previous` part of the way towards `sample`.

    The step is sized by how much time passed rather than being a fixed
    fraction, so a skipped frame does not change how quickly the gauge
    follows. `seconds` is roughly how long it takes to cover most of the
    distance to a new level.

    A missing sample, a missing history or smoothing switched off all report
    the sample itself: on a first reading there is nothing to smooth against,
    and on a sensor that stopped answering a remembered value would be a
    comfortable lie.
    """
    if sample is None or previous is None or seconds <= 0:
        return sample
    weight = elapsed / (elapsed + seconds)
    return previous + (sample - previous) * weight
