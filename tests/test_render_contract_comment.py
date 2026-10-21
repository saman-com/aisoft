"""Tests for the contract comment renderer (slice 1, T1.3).

The seam under test is ``render_contract_comment(review: dict) -> str``: it
takes the structured review dict PR-Agent emits on ``GITHUB_OUTPUT`` and
returns the canonical three-section comment described in CONTRACT.md.

Run with: python3 -m unittest discover -s tests -v
"""

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

from render_contract_comment import ContractError, render_contract_comment  # noqa: E402


def review(**overrides):
    base = {
        "score": 100,
        "key_issues_to_review": [],
        "agent_prompt": "Fix src/driver.py line 42.",
    }
    base.update(overrides)
    return base


class SectionLayoutTest(unittest.TestCase):
    def test_emits_the_three_sections_in_order(self):
        out = render_contract_comment(review())
        headings = [line for line in out.splitlines() if line.startswith("## ")]
        self.assertEqual(headings, ["## Confidence Score", "## Issues", "## Agent Prompt"])

    def test_nothing_follows_the_agent_prompt(self):
        out = render_contract_comment(review(agent_prompt="Do the thing."))
        self.assertTrue(out.rstrip().endswith("Do the thing."))

    def test_agent_prompt_is_passed_through_verbatim(self):
        prompt = "First line.\n\nSecond line,   indented."
        out = render_contract_comment(review(agent_prompt=prompt))
        self.assertIn(prompt, out)


class ScoreTest(unittest.TestCase):
    """PR-Agent scores 0-100; the loop speaks in fifths."""

    def score_line(self, raw):
        out = render_contract_comment(review(score=raw))
        return out.split("## Confidence Score\n", 1)[1].split("\n", 1)[0]

    def test_perfect_score_is_five(self):
        self.assertEqual(self.score_line(100), "5/5")

    def test_ninety_is_five(self):
        self.assertEqual(self.score_line(90), "5/5")

    def test_eighty_nine_is_four(self):
        self.assertEqual(self.score_line(89), "4/5")

    def test_zero_is_zero(self):
        self.assertEqual(self.score_line(0), "0/5")

    def test_out_of_range_score_is_rejected(self):
        with self.assertRaises(ContractError):
            render_contract_comment(review(score=101))

    def test_missing_score_is_rejected(self):
        data = review()
        del data["score"]
        with self.assertRaises(ContractError):
            render_contract_comment(data)


class IssuesTest(unittest.TestCase):
    def test_issue_becomes_a_bullet_with_its_location(self):
        data = review(key_issues_to_review=[{
            "relevant_file": "src/driver.py",
            "issue_header": "Missing timeout",
            "issue_content": "The poll loop never times out.",
            "start_line": 42,
            "end_line": 50,
        }])
        out = render_contract_comment(data)
        section = out.split("## Issues\n", 1)[1].split("## Agent Prompt", 1)[0]
        self.assertIn("- src/driver.py:42-50 — Missing timeout: The poll loop never times out.", section)

    def test_no_issues_renders_an_explicit_none(self):
        out = render_contract_comment(review())
        section = out.split("## Issues\n", 1)[1].split("## Agent Prompt", 1)[0]
        self.assertEqual(section.strip(), "None")

    def test_single_line_issue_collapses_the_range(self):
        data = review(key_issues_to_review=[{
            "relevant_file": "a.py",
            "issue_header": "Bug",
            "issue_content": "Broken.",
            "start_line": 7,
            "end_line": 7,
        }])
        out = render_contract_comment(data)
        self.assertIn("- a.py:7 — Bug: Broken.", out)


class AgentPromptTest(unittest.TestCase):
    """`agent_prompt` is not in PR-Agent's schema, so it may be absent.

    The loop still needs a prompt, so the renderer derives one from the
    findings rather than failing the whole run on model behaviour we do not
    control. With no findings either, the review is unusable: score 5/5 with
    nothing to fix means there is genuinely nothing to hand an agent, but a
    blank prompt next to a low score would silently stall the loop.
    """

    issue = {
        "relevant_file": "src/driver.py",
        "issue_header": "Missing timeout",
        "issue_content": "The poll loop never times out.",
        "start_line": 42,
        "end_line": 42,
    }

    def prompt_section(self, out):
        return out.split("## Agent Prompt\n", 1)[1]

    def test_missing_agent_prompt_is_derived_from_the_findings(self):
        data = review(key_issues_to_review=[self.issue])
        del data["agent_prompt"]
        prompt = self.prompt_section(render_contract_comment(data))
        self.assertIn("src/driver.py:42", prompt)
        self.assertIn("The poll loop never times out.", prompt)

    def test_blank_agent_prompt_is_derived_from_the_findings(self):
        data = review(key_issues_to_review=[self.issue], agent_prompt="   \n\n  ")
        prompt = self.prompt_section(render_contract_comment(data))
        self.assertIn("Missing timeout", prompt)

    def test_derived_prompt_is_deterministic(self):
        data = review(key_issues_to_review=[self.issue])
        del data["agent_prompt"]
        self.assertEqual(render_contract_comment(data), render_contract_comment(data))

    def test_no_prompt_and_no_issues_is_rejected(self):
        data = review()
        del data["agent_prompt"]
        with self.assertRaises(ContractError):
            render_contract_comment(data)


if __name__ == "__main__":
    unittest.main()
