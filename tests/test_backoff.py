"""Tests for the retry delays."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from backoff import retry_delays, should_retry, total_wait  # noqa: E402


class DelaysTest(unittest.TestCase):
    def test_delays_double_from_the_base(self):
        self.assertEqual(retry_delays(4), [1.0, 2.0, 4.0, 8.0])

    def test_no_cap_means_no_ceiling(self):
        """`cap=None` is "unbounded", not "cap at None".

        The branch started with a bug here: the cap was applied unconditionally,
        so this returned `[None, None, None, None]` and `total_wait` crashed
        summing them. The review caught it, and this test is what stops it coming
        back.
        """
        self.assertEqual(retry_delays(4, cap=None), [1.0, 2.0, 4.0, 8.0])
        self.assertEqual(total_wait(4, cap=None), 15.0)

    def test_no_attempts_waits_nothing(self):
        self.assertEqual(retry_delays(0), [])

    def test_a_cap_bounds_every_delay(self):
        self.assertEqual(retry_delays(4, base=2.0, cap=5.0), [2.0, 4.0, 5.0, 5.0])

    def test_total_wait_is_the_sum(self):
        self.assertEqual(total_wait(3), 7.0)


class RetryTest(unittest.TestCase):
    def test_retry_is_allowed_up_to_the_maximum(self):
        self.assertTrue(should_retry(0, 3))
        self.assertTrue(should_retry(3, 3))

    def test_retry_is_refused_past_the_maximum(self):
        self.assertFalse(should_retry(4, 3))


if __name__ == "__main__":
    unittest.main()
