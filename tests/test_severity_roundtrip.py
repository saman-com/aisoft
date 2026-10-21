"""The severity rule end to end: PR-Agent review -> comment -> parser -> decision.

`tests/test_issue_severity.py` proves the rule reads a `[P2]` tag correctly.
That is necessary but not sufficient, because the tags have to survive the trip
from a structured review through `render_contract_comment.py` to the parser.
This file runs that trip, and its main job is to record an honest gap:

**The renderer never emits a severity tag.** `_format_issue` in
`scripts/render_contract_comment.py` builds `- <location> — <header>: <content>`
and PR-Agent's review schema has no severity field to put in front of it. So on
a real PR every bullet arrives untagged, every bullet counts as blocking, and
the `P2` allowance in `Review.is_clean` cannot fire for a 4/5 that still lists
findings.

Today that costs nothing on the plateau case PR #27 showed: a 4/5 with *no*
findings is clean, and that is the path the loop actually takes. It does mean the
"4/5 with only nits" exit is dormant until the model supplies a severity, which
is a contract change (CONTRACT.md defines the bullet shape and requires all three
of renderer, `.pr_agent.toml` and parser to move together) and is tracked
separately rather than smuggled into T4.2.

These tests assert what the pipeline does *now*. When a severity field lands, the
renderer test is the one that should fail first.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from driver_decision import Outcome, decide  # noqa: E402
from parse_contract_comment import parse_comment  # noqa: E402
from render_contract_comment import render_contract_comment  # noqa: E402


def finding(path="a.py", start=1, end=2, header="Bug", content="Real bug."):
    return {
        "relevant_file": path,
        "start_line": start,
        "end_line": end,
        "issue_header": header,
        "issue_content": content,
    }


def roundtrip(score: int, findings: list[dict], prompt: str = "Fix it."):
    """Render a structured review, then parse the comment the way the loop does."""
    body = render_contract_comment(
        {"score": score, "key_issues_to_review": findings, "agent_prompt": prompt}
    )
    return parse_comment(body)


class RenderedBulletsTest(unittest.TestCase):
    def test_a_rendered_bullet_carries_no_severity_tag(self):
        """The gap, stated as a fact about the renderer.

        If this fails because a `[P0]` appeared, the severity field has landed
        and the round-trip tests below can start asserting real severities.
        """
        body = render_contract_comment(
            {"score": 85, "key_issues_to_review": [finding()], "agent_prompt": "Fix."}
        )
        bullet = [line for line in body.splitlines() if line.startswith("- ")][0]
        self.assertEqual(bullet, "- a.py:1-2 — Bug: Real bug.")

    def test_a_tag_in_the_finding_text_is_not_read_as_severity(self):
        """A `[P2]` inside the description is prose, not a tag on the bullet."""
        parsed = roundtrip(85, [finding(content="Severity [P2] was chosen.")])
        self.assertTrue(parsed.blocking_issues)


class RenderedDecisionTest(unittest.TestCase):
    """What the real pipeline decides, for the scores that matter."""

    def test_a_plateau_with_no_findings_succeeds(self):
        """PR #27's case: the reviewer settles at 4/5 and reports nothing."""
        self.assertIs(
            decide(roundtrip(85, []), iteration=1).outcome, Outcome.SUCCESS
        )

    def test_five_out_of_five_succeeds(self):
        self.assertIs(
            decide(roundtrip(95, []), iteration=1).outcome, Outcome.SUCCESS
        )

    def test_a_plateau_with_a_finding_continues(self):
        """Untagged because the renderer has no severity to emit, so it blocks."""
        self.assertIs(
            decide(roundtrip(85, [finding()]), iteration=1).outcome, Outcome.CONTINUE
        )

    def test_a_three_with_findings_continues(self):
        self.assertIs(
            decide(roundtrip(65, [finding()]), iteration=1).outcome, Outcome.CONTINUE
        )

    def test_the_same_review_at_the_limit_goes_to_a_human(self):
        review = roundtrip(85, [finding()])
        self.assertIs(decide(review, iteration=5).outcome, Outcome.HUMAN)

    def test_a_hand_edited_bullet_with_a_p2_still_reaches_the_allowance(self):
        """The rule works; only the renderer does not produce this shape yet.

        Written by hand, because nothing in the pipeline writes it. Keeping it
        here separates "the rule is broken" from "the rule is unreachable".
        """
        review = parse_comment(
            "## Confidence Score\n4/5\n\n## Issues\n- [P2] a.py:1 — Style: Nit.\n\n"
            "## Agent Prompt\nFix the nit.\n"
        )
        self.assertIs(decide(review, iteration=1).outcome, Outcome.SUCCESS)


if __name__ == "__main__":
    unittest.main()
