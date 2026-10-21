"""Tests for parsing the contract comment.

The parser is the one place where a mistake costs the loop real work: a wrong
score stops a PR early or keeps it going forever, and a mangled Agent Prompt is
handed straight to Pi. So the failure rules matter as much as the happy path -
every malformed comment must raise, never fall back to a default score or a
partial prompt.

These tests follow CONTRACT.md section "Parsing rules" and "Failure rules".
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from parse_contract_comment import ParseError, parse_comment  # noqa: E402

GOOD = """## Confidence Score
4/5

## Issues
- scripts/driver.py:42-50 — Missing timeout: The poll loop never times out.

## Agent Prompt
Add a timeout to the polling loop in scripts/driver.py.
"""


class HappyPathTest(unittest.TestCase):
    def test_parses_score_issues_and_prompt(self):
        review = parse_comment(GOOD)
        self.assertEqual(review.score, 4)
        self.assertEqual(
            review.issues,
            ("scripts/driver.py:42-50 — Missing timeout: The poll loop never times out.",),
        )
        self.assertEqual(
            review.agent_prompt,
            "Add a timeout to the polling loop in scripts/driver.py.",
        )

    def test_no_issues_gives_an_empty_tuple(self):
        body = GOOD.replace(
            "- scripts/driver.py:42-50 — Missing timeout: The poll loop never times out.",
            "None",
        )
        self.assertEqual(parse_comment(body).issues, ())

    def test_several_issues_keep_their_order(self):
        body = GOOD.replace(
            "- scripts/driver.py:42-50 — Missing timeout: The poll loop never times out.",
            "- a.py:1 — First: One.\n- b.py:2 — Second: Two.",
        )
        self.assertEqual(parse_comment(body).issues, ("a.py:1 — First: One.", "b.py:2 — Second: Two."))

    def test_a_multi_paragraph_prompt_keeps_its_newlines(self):
        body = GOOD.replace(
            "Add a timeout to the polling loop in scripts/driver.py.",
            "First do this.\n\nThen do that.",
        )
        self.assertEqual(
            parse_comment(body).agent_prompt, "First do this.\n\nThen do that."
        )

    def test_surrounding_blank_lines_are_trimmed_from_the_prompt(self):
        body = GOOD.replace(
            "Add a timeout to the polling loop in scripts/driver.py.",
            "\n\n   \nAdd a timeout.\n\n\n",
        )
        self.assertEqual(parse_comment(body).agent_prompt, "Add a timeout.")


class ScoreTest(unittest.TestCase):
    def test_accepts_a_label_before_the_score(self):
        body = GOOD.replace("4/5", "Score: 4/5")
        self.assertEqual(parse_comment(body).score, 4)

    def test_accepts_confidence_as_the_label(self):
        body = GOOD.replace("4/5", "Confidence: 4/5")
        self.assertEqual(parse_comment(body).score, 4)

    def test_accepts_surrounding_spaces(self):
        body = GOOD.replace("4/5", "   4 / 5   ")
        self.assertEqual(parse_comment(body).score, 4)

    def test_accepts_the_extremes(self):
        for raw, expected in (("0/5", 0), ("5/5", 5)):
            with self.subTest(raw=raw):
                self.assertEqual(parse_comment(GOOD.replace("4/5", raw)).score, expected)

    def test_rejects_a_score_above_five(self):
        with self.assertRaises(ParseError) as caught:
            parse_comment(GOOD.replace("4/5", "6/5"))
        self.assertIn("invalid confidence score", str(caught.exception))
        self.assertIn("6/5", str(caught.exception))

    def test_rejects_a_score_that_is_not_an_integer(self):
        with self.assertRaises(ParseError):
            parse_comment(GOOD.replace("4/5", "four/five"))

    def test_rejects_a_bare_number_with_no_fifths_marker(self):
        """`4` alone is ambiguous - it could be a 0-100 score."""
        with self.assertRaises(ParseError):
            parse_comment(GOOD.replace("4/5", "4"))


class StructureTest(unittest.TestCase):
    def test_missing_section_names_the_section(self):
        body = GOOD.split("## Agent Prompt")[0]
        with self.assertRaises(ParseError) as caught:
            parse_comment(body)
        self.assertIn("missing section: Agent Prompt", str(caught.exception))

    def test_a_missing_issues_section_is_an_error_even_at_five_out_of_five(self):
        """A clean score is not permission to skip the prompt the loop needs."""
        body = "## Confidence Score\n5/5\n\n## Agent Prompt\nNothing.\n"
        with self.assertRaises(ParseError) as caught:
            parse_comment(body)
        self.assertIn("missing section: Issues", str(caught.exception))

    def test_a_duplicated_header_is_an_error(self):
        body = GOOD + "\n## Issues\n- a.py:1 — Again: Duplicate.\n"
        with self.assertRaises(ParseError) as caught:
            parse_comment(body)
        self.assertIn("duplicated section: Issues", str(caught.exception))

    def test_out_of_order_sections_are_an_error(self):
        body = (
            "## Issues\nNone\n\n## Confidence Score\n4/5\n\n## Agent Prompt\nDo it.\n"
        )
        with self.assertRaises(ParseError) as caught:
            parse_comment(body)
        self.assertIn("sections out of order", str(caught.exception))

    def test_an_empty_prompt_is_an_error(self):
        body = "## Confidence Score\n4/5\n\n## Issues\nNone\n\n## Agent Prompt\n\n\n"
        with self.assertRaises(ParseError) as caught:
            parse_comment(body)
        self.assertIn("empty agent prompt", str(caught.exception))

    def test_a_header_inside_an_inline_code_span_is_not_a_section(self):
        """A finding may quote the contract's own headings.

        Reading the quoted copy as a real header would split the comment in the
        wrong place and hand Pi a fragment of a finding.
        """
        body = GOOD.replace(
            "- scripts/driver.py:42-50 — Missing timeout: The poll loop never times out.",
            "- a.md:3 — Quoted heading: The docs show `## Issues` inline.",
        )
        review = parse_comment(body)
        self.assertEqual(review.score, 4)
        self.assertEqual(len(review.issues), 1)
        self.assertIn("`## Issues`", review.issues[0])

    def test_a_header_inside_a_fence_is_not_a_section(self):
        """A prompt showing the contract as an example is still one prompt."""
        body = GOOD.replace(
            "Add a timeout to the polling loop in scripts/driver.py.",
            "Produce exactly this shape:\n\n```\n## Confidence Score\n5/5\n```\n",
        )
        review = parse_comment(body)
        self.assertEqual(review.score, 4)
        self.assertIn("## Confidence Score", review.agent_prompt)

    def test_a_fenced_prompt_keeps_its_fence(self):
        body = GOOD.replace(
            "Add a timeout to the polling loop in scripts/driver.py.",
            "Patch this:\n\n```python\nwait(timeout=1)\n```",
        )
        self.assertIn("```python\nwait(timeout=1)\n```", parse_comment(body).agent_prompt)

    def test_headers_must_be_case_sensitive(self):
        body = GOOD.replace("## Confidence Score", "## confidence score")
        with self.assertRaises(ParseError):
            parse_comment(body)

    def test_text_before_the_first_header_is_ignored(self):
        body = "Thanks for the PR!\n\n" + GOOD
        self.assertEqual(parse_comment(body).score, 4)


class ContextTest(unittest.TestCase):
    def test_errors_name_the_comment_and_pr(self):
        with self.assertRaises(ParseError) as caught:
            parse_comment("no sections here", comment_id=6306554503, pr=27)
        message = str(caught.exception)
        self.assertIn("6306554503", message)
        self.assertIn("#27", message)

    def test_errors_quote_the_offending_text(self):
        with self.assertRaises(ParseError) as caught:
            parse_comment(GOOD.replace("4/5", "99/5"))
        self.assertIn("99/5", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
