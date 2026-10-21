"""Tests for the driver's human-readable output.

The driver runs unattended for up to an hour, so its output is the only record of
what it saw. Two things must always be visible: what it read (so a wrong run can
be explained afterwards) and whether anything was wrong (so a silent parse
failure cannot look like a clean run).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from driver_log import describe_failure, describe_review  # noqa: E402
from parse_contract_comment import Review  # noqa: E402

REVIEW = Review(
    score=4,
    issues=("scripts/driver.py:42-50 — Missing timeout: The poll loop never times out.",),
    agent_prompt="Add a timeout to the polling loop in scripts/driver.py.",
)


class ReviewLogTest(unittest.TestCase):
    def test_reports_the_score_the_issue_count_and_the_prompt_size(self):
        text = describe_review(REVIEW, pr=27, comment_id=6306554503, iteration=1)
        self.assertIn("4/5", text)
        self.assertIn("1 issue", text)
        self.assertIn(f"{len(REVIEW.agent_prompt)} chars", text)

    def test_names_the_pr_the_comment_and_the_iteration(self):
        text = describe_review(REVIEW, pr=27, comment_id=6306554503, iteration=2)
        self.assertIn("#27", text)
        self.assertIn("6306554503", text)
        self.assertIn("iteration 2", text)

    def test_each_issue_is_listed(self):
        text = describe_review(REVIEW, pr=27, comment_id=1, iteration=1)
        self.assertIn("scripts/driver.py:42-50", text)

    def test_many_issues_are_summarised_rather_than_dumped(self):
        many = Review(score=2, issues=tuple(f"f{i}.py:{i} — H: text." for i in range(12)),
                      agent_prompt="Fix them.")
        text = describe_review(many, pr=27, comment_id=1, iteration=1)
        self.assertIn("12 issues", text)
        # Enough to recognise the review, not the whole list.
        self.assertLessEqual(text.count("\n  - "), 5)

    def test_a_clean_review_says_so_plainly(self):
        clean = Review(score=5, issues=(), agent_prompt="No issues found; no changes required.")
        text = describe_review(clean, pr=27, comment_id=1, iteration=3)
        self.assertIn("5/5", text)
        self.assertIn("no issues", text.lower())

    def test_singular_and_plural_are_both_readable(self):
        self.assertIn("1 issue", describe_review(REVIEW, pr=27, comment_id=1, iteration=1))
        two = Review(score=3, issues=("a.py:1 — A: One.", "b.py:2 — B: Two."),
                     agent_prompt="Fix.")
        self.assertIn("2 issues", describe_review(two, pr=27, comment_id=1, iteration=1))


class FailureLogTest(unittest.TestCase):
    def test_a_failure_is_marked_as_an_error(self):
        text = describe_failure("missing section: Issues", pr=27, comment_id=5)
        self.assertIn("ERROR", text)
        self.assertIn("missing section: Issues", text)
        self.assertIn("#27", text)

    def test_a_failure_is_not_mistakable_for_a_score(self):
        text = describe_failure("empty agent prompt", pr=27, comment_id=5)
        self.assertNotIn("/5", text.split("ERROR")[1].split("\n")[0])


if __name__ == "__main__":
    unittest.main()
