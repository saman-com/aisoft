"""Tests for the comparison key behind repeated-issue detection (T4.3).

The fingerprint answers "is this the same finding as last time", and both kinds
of error are expensive:

- Too strict, and the loop pays four more iterations to rediscover that Pi
  cannot fix something. That is the failure T4.3 exists to remove.
- Too loose, and a loop that was making progress stops on a false positive. That
  is worse, because the PR then needs a human for no reason.

The tests are grouped by which side of that trade they defend. The key must
survive what a re-review changes (line numbers, severity, header wording,
punctuation, ordering) and must not collapse what makes two findings different
(the file, or what is actually wrong).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from parse_contract_comment import issue_fingerprint, parse_comment  # noqa: E402

BODY = """## Confidence Score
{score}/5

## Issues
{issues}

## Agent Prompt
Fix the listed issues.
"""


def reviews(score: int, issues: str):
    return parse_comment(BODY.format(score=score, issues=issues))


class SurvivesAReReviewTest(unittest.TestCase):
    """Variations the reviewer produces for a finding that has not been fixed."""

    def assertSame(self, first: str, second: str):
        self.assertEqual(issue_fingerprint(first), issue_fingerprint(second))

    def test_the_line_number_moving_is_the_same_finding(self):
        """Pi's commit shifts lines, so this is the common case, not an edge."""
        self.assertSame("[P1] a.py:1 — Bug: wrong", "[P1] a.py:9 — Bug: wrong")

    def test_a_line_range_is_the_same_finding(self):
        self.assertSame("[P1] a.py:1 — Bug: wrong", "[P1] a.py:1-3 — Bug: wrong")

    def test_the_severity_changing_is_the_same_finding(self):
        self.assertSame("[P1] a.py:1 — Bug: wrong", "[P0] a.py:1 — Bug: wrong")

    def test_the_header_being_reworded_is_the_same_finding(self):
        self.assertSame("[P1] a.py:1 — Bug: wrong", "[P1] a.py:1 — Defect: wrong")

    def test_case_is_ignored(self):
        self.assertSame("[P1] A.PY:1 — Bug: WRONG.", "[P1] a.py:1 — bug: wrong")

    def test_trailing_punctuation_is_ignored(self):
        self.assertSame("[P1] a.py:1 — Bug: wrong.", "[P1] a.py:1 — Bug: wrong")

    def test_a_double_space_is_ignored(self):
        self.assertSame("[P1] a.py:1 — Bug: wrong", "[P1] a.py:1 — Bug:  wrong")

    def test_an_untagged_bullet_matches_its_tagged_self(self):
        self.assertSame("a.py:1 — Bug: wrong", "[P1] a.py:1 — Bug: wrong")

    def test_a_path_with_underscores_and_dashes_survives(self):
        self.assertSame(
            "[P1] src/driver_git.py:10 — Bug: wrong",
            "[P1] src/driver_git.py:44 — Bug: wrong",
        )


class KeepsDistinctTest(unittest.TestCase):
    """Things that mean the finding changed, so the loop is not going in circles."""

    def assertDifferent(self, first: str, second: str):
        self.assertNotEqual(issue_fingerprint(first), issue_fingerprint(second))

    def test_a_different_file_is_a_different_finding(self):
        self.assertDifferent("[P1] a.py:1 — Bug: wrong", "[P1] b.py:1 — Bug: wrong")

    def test_a_different_description_is_a_different_finding(self):
        self.assertDifferent("[P1] a.py:1 — Bug: wrong", "[P1] a.py:1 — Bug: also wrong")

    def test_a_fixed_finding_replaced_by_a_new_one_is_not_a_repeat(self):
        self.assertDifferent(
            "[P1] a.py:1 — Bug: loop never times out",
            "[P1] a.py:1 — Bug: commit message missing iteration",
        )

    def test_a_dot_is_not_a_slash(self):
        """Folding a path's punctuation would make these the same file."""
        self.assertDifferent("[P1] a.py:1 — Bug: wrong", "[P1] a/py:1 — Bug: wrong")

    def test_different_paths_with_a_shared_prefix_differ(self):
        self.assertDifferent("[P1] src/a.py:1 — Bug: wrong", "[P1] lib/a.py:1 — Bug: wrong")

    def test_a_same_named_file_in_another_directory_differs(self):
        self.assertDifferent(
            "[P1] scripts/driver.py:1 — Bug: wrong",
            "[P1] tests/driver.py:1 — Bug: wrong",
        )


class ReviewFingerprintTest(unittest.TestCase):
    def test_order_does_not_matter(self):
        first = reviews(
            3, "- [P1] a.py:1 — Bug: one.\n- [P1] b.py:2 — Bug: two."
        )
        second = reviews(
            3, "- [P1] b.py:2 — Bug: two.\n- [P1] a.py:1 — Bug: one."
        )
        self.assertEqual(first.blocking_fingerprint, second.blocking_fingerprint)

    def test_a_duplicated_finding_does_not_change_the_set(self):
        once = reviews(3, "- [P1] a.py:1 — Bug: one.")
        twice = reviews(3, "- [P1] a.py:1 — Bug: one.\n- [P1] a.py:1 — Bug: one.")
        self.assertEqual(once.blocking_fingerprint, twice.blocking_fingerprint)

    def test_only_blocking_issues_are_in_the_fingerprint(self):
        parsed = reviews(
            4, "- [P1] a.py:1 — Bug: real.\n- [P2] b.py:2 — Style: nit."
        )
        self.assertEqual(len(parsed.blocking_fingerprint), 1)

    def test_p2_churn_alone_does_not_change_the_fingerprint(self):
        """A reviewer reshuffling nits must not look like progress, or a repeat."""
        first = reviews(
            4, "- [P1] a.py:1 — Bug: real.\n- [P2] b.py:2 — Style: nit."
        )
        second = reviews(
            4, "- [P1] a.py:1 — Bug: real.\n- [P2] c.py:3 — Style: another nit."
        )
        self.assertEqual(first.blocking_fingerprint, second.blocking_fingerprint)

    def test_no_issues_is_an_empty_fingerprint(self):
        self.assertEqual(reviews(5, "None").blocking_fingerprint, ())
        self.assertEqual(reviews(5, "").blocking_fingerprint, ())


if __name__ == "__main__":
    unittest.main()
