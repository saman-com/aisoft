"""Tests for reading a `[P0]` / `[P1]` / `[P2]` tag off an issue bullet.

The severity tag is what turns a review into a decision, so a wrong reading here
is a wrong stopping decision one layer up. Two readings are dangerous:

- Treating an untagged bullet as harmless. The reviewer leaving the tag off is a
  formatting slip, not an approval, and guessing "minor" ships the finding.
- Treating an unknown tag as harmless. `[P3]`, `[high]` or `[critical]` are not
  in CONTRACT.md, so the loop has no idea what they mean. The safe default is
  the same as untagged: assume it blocks.

The tag is read case-insensitively because it is model-written, and it is
stripped only from the front of the bullet so the finding text survives intact.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from parse_contract_comment import (  # noqa: E402
    Review,
    is_blocking,
    parse_comment,
    severity_of,
)

BODY = """## Confidence Score
{score}/5

## Issues
{issues}

## Agent Prompt
Fix the listed issues.
"""


def review(score: int, issues: str) -> Review:
    return parse_comment(BODY.format(score=score, issues=issues))


class SeverityOfTest(unittest.TestCase):
    def test_reads_each_documented_tag(self):
        for tag in ("P0", "P1", "P2"):
            with self.subTest(tag=tag):
                self.assertEqual(severity_of(f"[{tag}] a.py:1 — X: Y."), tag)

    def test_is_case_insensitive(self):
        """The tag is model output, so `[p1]` has to mean the same as `[P1]`."""
        self.assertEqual(severity_of("[p1] a.py:1 — X: Y."), "P1")

    def test_an_untagged_bullet_has_no_severity(self):
        self.assertIsNone(severity_of("a.py:1 — X: Y."))

    def test_an_unknown_tag_is_returned_as_written(self):
        """It is reported, not swallowed; the caller decides it blocks."""
        self.assertEqual(severity_of("[P9] a.py:1 — X: Y."), "P9")

    def test_a_bracket_later_in_the_location_is_not_a_tag(self):
        """Only a leading `[...]` group is a tag, not any bracket in the text."""
        self.assertIsNone(severity_of("a.py:1 — Bug: index[0] is wrong."))

    def test_leading_whitespace_does_not_hide_a_tag(self):
        self.assertEqual(severity_of("   [P0] a.py:1 — X: Y."), "P0")


class IsBlockingTest(unittest.TestCase):
    def test_p0_and_p1_block(self):
        for tag in ("P0", "P1"):
            with self.subTest(tag=tag):
                self.assertTrue(is_blocking(f"[{tag}] a.py:1 — X: Y."))

    def test_p2_does_not_block(self):
        self.assertFalse(is_blocking("[P2] a.py:1 — X: Y."))

    def test_an_untagged_bullet_blocks(self):
        """No tag is not the same as a low severity. Assume it matters."""
        self.assertTrue(is_blocking("a.py:1 — X: Y."))

    def test_an_unknown_tag_blocks(self):
        for tag in ("P3", "P9", "HIGH", "CRITICAL", "nit"):
            with self.subTest(tag=tag):
                self.assertTrue(is_blocking(f"[{tag}] a.py:1 — X: Y."))


class ReviewSeverityTest(unittest.TestCase):
    def test_blocking_issues_are_the_p0_and_p1_ones(self):
        parsed = review(
            4,
            "- [P1] a.py:1 — Bug: Real.\n- [P2] b.py:2 — Style: Nit.\n"
            "- [P0] c.py:3 — Security: Wide open.",
        )
        self.assertEqual(len(parsed.issues), 3)
        self.assertEqual(len(parsed.blocking_issues), 2)
        self.assertEqual(parsed.blocking_count, 2)

    def test_no_issues_means_no_blocking_issues(self):
        parsed = review(5, "None")
        self.assertEqual(parsed.issues, ())
        self.assertEqual(parsed.blocking_issues, ())

    def test_an_empty_issues_section_means_no_blocking_issues(self):
        parsed = review(5, "")
        self.assertEqual(parsed.blocking_issues, ())

    def test_the_parsed_bullet_keeps_its_tag(self):
        """The tag is part of the finding text; stripping it here loses context."""
        parsed = review(4, "- [P1] a.py:1 — Bug: Real.")
        self.assertEqual(parsed.issues[0], "[P1] a.py:1 — Bug: Real.")


class IsCleanTest(unittest.TestCase):
    """`is_clean` is now the loop's real exit, per docs/TASKS.md T4.2."""

    def test_five_out_of_five_is_clean(self):
        self.assertTrue(review(5, "None").is_clean)

    def test_five_out_of_five_with_issues_is_still_clean(self):
        self.assertTrue(review(5, "- [P1] a.py:1 — Bug: Real.").is_clean)

    def test_four_out_of_five_with_no_issues_is_clean(self):
        self.assertTrue(review(4, "None").is_clean)

    def test_four_out_of_five_with_only_p2_is_clean(self):
        self.assertTrue(review(4, "- [P2] a.py:1 — Style: Nit.").is_clean)

    def test_four_out_of_five_with_a_p1_is_not_clean(self):
        self.assertFalse(review(4, "- [P1] a.py:1 — Bug: Real.").is_clean)

    def test_four_out_of_five_with_a_p0_is_not_clean(self):
        self.assertFalse(review(4, "- [P0] a.py:1 — Security: Wide open.").is_clean)

    def test_four_out_of_five_with_an_untagged_issue_is_not_clean(self):
        self.assertFalse(review(4, "- a.py:1 — Bug: No tag.").is_clean)

    def test_three_out_of_five_is_not_clean_even_with_no_issues(self):
        self.assertFalse(review(3, "None").is_clean)

    def test_three_out_of_five_with_only_p2_is_not_clean(self):
        """The P2 allowance only applies at 4 or better."""
        self.assertFalse(review(3, "- [P2] a.py:1 — Style: Nit.").is_clean)


if __name__ == "__main__":
    unittest.main()
