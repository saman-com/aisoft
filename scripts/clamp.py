"""Clamp a number into a range, refusing ranges that cannot be satisfied."""

from __future__ import annotations


class BadRange(ValueError):
    """The low bound is above the high bound, so no value can satisfy it."""


def clamp(value: float, *, low: float, high: float) -> float:
    """Return `value` moved inside `[low, high]`.

    A range whose low bound is above its high bound is refused rather than
    silently swapped: a caller that wrote the bounds the wrong way round has a
    bug, and swapping them would hide it behind a plausible-looking answer.
    """
    if low > high:
        raise BadRange(f"low {low} is above high {high}")
    if value < low:
        return low
    if value > high:
        return high
    return value
