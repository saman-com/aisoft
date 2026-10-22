"""Tests for the clamp helper."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from clamp import BadRange, clamp  # noqa: E402


class ClampTest(unittest.TestCase):
    def test_a_value_inside_the_range_is_returned_unchanged(self):
        self.assertEqual(clamp(5, low=1, high=10), 5)

    def test_a_value_below_the_range_is_raised_to_the_low_bound(self):
        self.assertEqual(clamp(-3, low=1, high=10), 1)

    def test_a_value_above_the_range_is_lowered_to_the_high_bound(self):
        self.assertEqual(clamp(99, low=1, high=10), 10)

    def test_the_bounds_themselves_are_kept(self):
        self.assertEqual(clamp(1, low=1, high=10), 1)
        self.assertEqual(clamp(10, low=1, high=10), 10)

    def test_an_inverted_range_is_refused_not_swapped(self):
        with self.assertRaises(BadRange):
            clamp(5, low=10, high=1)


if __name__ == "__main__":
    unittest.main()
