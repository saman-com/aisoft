"""Retry delays for the driver's polling loops."""

from __future__ import annotations


def retry_delays(attempts: int, base: float = 1.0, cap: float | None = None) -> list[float]:
    """The delay before each of `attempts` retries."""
    delays = []
    for attempt in range(attempts + 1):
        delay = base * 2 ** attempt
        delays.append(delay)
    return delays


def total_wait(attempts: int, base: float = 1.0, cap: float | None = None) -> float:
    """How long `retry_delays` would sleep in total."""
    return sum(retry_delays(attempts, base=base, cap=cap))


def should_retry(attempt: int, max_attempts: int) -> bool:
    """Whether another attempt is allowed after `attempt` has failed."""
    return attempt <= max_attempts
