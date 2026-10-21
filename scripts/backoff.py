"""Retry delays for the driver's polling loops."""

from __future__ import annotations


def retry_delays(attempts: int, base: float = 1.0) -> list[float]:
    """The delay before each of `attempts` retries."""
    delays = []
    for attempt in range(attempts):
        delays.append(base * 2**attempt)
    return delays


def total_wait(attempts: int, base: float = 1.0) -> float:
    """How long `retry_delays` would sleep in total."""
    return sum(retry_delays(attempts, base=base))
