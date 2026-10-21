"""Tests for the iteration counter and its hard limit.

T4.1 is the only thing standing between the driver and a loop that runs until
someone notices. Three properties matter, and each has failed in real tools before:

- The limit is five, it lives in exactly one place, and both the log line and the
  refusal name it. A limit nobody can see is a limit nobody trusts.
- The sixth attempt is refused *before* anything happens: no Pi run, no commit, no
  push. Checking afterwards would push a sixth fix and then report that it should
  not have.
- The guard is callable on its own, so T4.2's decision logic decides *whether* to
  iterate while this module decides *whether it is still allowed*. Two different
  questions; merging them lets a future edit answer one with the other's rule.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from driver_comments import DriverError  # noqa: E402
from driver_iteration import (  # noqa: E402
    MAX_ITERATIONS,
    IterationLimit,
    check_iteration,
    describe_iteration,
    is_final,
)

ROOT = Path(__file__).resolve().parents[1]


class TheLimitTest(unittest.TestCase):
    def test_the_hard_limit_is_five(self):
        self.assertEqual(MAX_ITERATIONS, 5)

    def test_the_limit_is_documented_where_an_operator_reads_it(self):
        """A number in code only is a number an operator cannot check."""
        in_tasks = (ROOT / "docs" / "TASKS.md").read_text()
        self.assertIn("5 iterations", in_tasks)

    def test_the_limit_is_in_the_agent_rules_as_an_invariant(self):
        """AGENT.md is what the next agent reads; an undocumented cap gets removed."""
        text = (ROOT / "AGENT.md").read_text()
        self.assertIn("MAX_ITERATIONS", text)
        self.assertIn("iteration N/5", text)

    def test_the_limit_exceeding_error_is_a_driver_error(self):
        """It must land in the exit-code branch the CLI already handles."""
        self.assertTrue(issubclass(IterationLimit, DriverError))


class CheckIterationTest(unittest.TestCase):
    def test_every_allowed_iteration_passes(self):
        for iteration in range(1, MAX_ITERATIONS + 1):
            with self.subTest(iteration=iteration):
                self.assertEqual(check_iteration(iteration), iteration)

    def test_the_sixth_iteration_is_refused(self):
        with self.assertRaises(IterationLimit) as caught:
            check_iteration(MAX_ITERATIONS + 1)
        message = str(caught.exception)
        self.assertIn("6", message)
        self.assertIn("5", message)

    def test_zero_and_negative_iterations_are_refused(self):
        """An off-by-one that starts at 0 would silently buy a sixth attempt."""
        for iteration in (0, -1):
            with self.subTest(iteration=iteration):
                with self.assertRaises(IterationLimit):
                    check_iteration(iteration)

    def test_the_refusal_says_the_pr_needs_a_human(self):
        with self.assertRaises(IterationLimit) as caught:
            check_iteration(6)
        self.assertIn("human", str(caught.exception).lower())


class DescribeIterationTest(unittest.TestCase):
    def test_the_label_shows_the_attempt_and_the_limit(self):
        self.assertEqual(describe_iteration(2), "iteration 2/5")

    def test_the_label_survives_the_limit_changing(self):
        self.assertEqual(
            describe_iteration(3), f"iteration 3/{MAX_ITERATIONS}"
        )


class IsFinalTest(unittest.TestCase):
    def test_only_the_last_allowed_iteration_is_final(self):
        self.assertTrue(is_final(MAX_ITERATIONS))
        self.assertFalse(is_final(MAX_ITERATIONS - 1))

    def test_is_final_agrees_with_the_guard_at_the_boundary(self):
        """One past the limit must be refused, and must not look like the last one."""
        self.assertTrue(is_final(MAX_ITERATIONS))
        with self.assertRaises(IterationLimit):
            check_iteration(MAX_ITERATIONS + 1)


if __name__ == "__main__":
    unittest.main()
