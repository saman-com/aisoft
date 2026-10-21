"""Tests for the retry delays."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from backoff import next_delay, retry_delays, should_retry, total_wait  # noqa: E402


class DelaysTest(unittest.TestCase):
    def test_delays_double_from_the_base(self):
        self.assertEqual(retry_delays(4), [1.0, 2.0, 4.0, 8.0])

    def test_no_attempts_waits_nothing(self):
        self.assertEqual(retry_delays(0), [])

    def test_a_cap_bounds_every_delay(self):
        self.assertEqual(retry_delays(4, base=2.0, cap=5.0), [2.0, 4.0, 5.0, 5.0])

    def test_total_wait_is_the_sum(self):
        self.assertEqual(total_wait(3), 7.0)


class NextDelayTest(unittest.TestCase):
    def test_the_delay_doubles(self):
        self.assertEqual(next_delay(2.0), 4.0)

    def test_a_cap_bounds_the_next_delay(self):
        self.assertEqual(next_delay(4.0, cap=5.0), 5.0)

    def test_no_cap_leaves_it_unbounded(self):
        self.assertEqual(next_delay(4.0, cap=None), 8.0)


class RetryTest(unittest.TestCase):
    def test_retry_is_allowed_up_to_the_maximum(self):
        self.assertTrue(should_retry(0, 3))
        self.assertTrue(should_retry(3, 3))

    def test_retry_is_refused_past_the_maximum(self):
        self.assertFalse(should_retry(4, 3))


if __name__ == "__main__":
    unittest.main()
