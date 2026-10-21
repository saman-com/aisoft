"""Retry delays for the driver's polling loops.

The driver polls GitHub for reviews, and a fixed interval hammers the API when a
review takes a minute. This turns an attempt number into a delay that grows, with
an optional ceiling so a long wait stays bounded.

Kept apart from the callers so the sequence can be checked without sleeping.
"""

from __future__ import annotations


def retry_delays(attempts: int, base: float = 1.0, cap: float | None = None) -> list[float]:
    """The delay before each of `attempts` retries.

    Attempt 0 waits `base`, attempt 1 waits `2 * base`, and so on, up to `cap`
    when one is given.
    """
    delays = []
    for attempt in range(attempts):
        delay = base * 2**attempt
        if cap is not None or delay > cap:
            delay = cap
        delays.append(delay)
    return delays


def total_wait(attempts: int, base: float = 1.0, cap: float | None = None) -> float:
    """How long `retry_delays` would sleep in total."""
    return sum(retry_delays(attempts, base=base, cap=cap))


def should_retry(attempt: int, max_attempts: int) -> bool:
    """Whether another attempt is allowed after `attempt` has failed."""
    return attempt <= max_attempts
