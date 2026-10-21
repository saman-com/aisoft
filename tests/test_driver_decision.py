"""Tests for the stopping decision.

T4.2 is the rule that decides whether the loop is finished. It is the one place
where a wrong answer is expensive in both directions: a too-easy success ships a
PR the reviewer was still worried about, and a too-strict one keeps paying Pi to
fix findings the reviewer itself rated as cosmetic.

The rule is the one written in docs/TASKS.md and settled in AGENT.md:

    success <=> score == 5
               or (score >= 4 and no P0/P1 issue)

Three properties get their own tests because each is easy to get subtly wrong:

- **Unknown severity blocks.** A bullet with no tag, or a tag outside P0/P2, is
  treated as blocking. The safe reading of "I cannot tell how bad this is" is
  "assume it is bad": the alternative lets a malformed review end the loop early
  on a PR nobody has really approved.
- **P2 alone does not block at 4/5**, which is the entire point of the rule. The
  reviewer plateaus at 4/5 while listing low-confidence findings (PR #27).
- **The limit wins over the fix.** At iteration 5, a non-success review stops for
  a human rather than starting a sixth attempt. That check comes from
  `MAX_ITERATIONS` in `driver_iteration.py`, not from a second copy here.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from driver_decision import Outcome, decide  # noqa: E402
from driver_iteration import MAX_ITERATIONS  # noqa: E402
from parse_contract_comment import Review  # noqa: E402


def review(score: int, *issues: str) -> Review:
    return Review(score=score, issues=issues, agent_prompt="Fix what is listed.")


class SuccessTest(unittest.TestCase):
    def test_five_out_of_five_with_no_issues_succeeds(self):
        decision = decide(review(5), iteration=1)
        self.assertEqual(decision.outcome, Outcome.SUCCESS)

    def test_five_out_of_five_with_only_a_p2_still_succeeds(self):
        """5/5 is success whatever the Issues section lists."""
        decision = decide(review(5, "[P2] a.py:1 — Style: Nit."), iteration=1)
        self.assertEqual(decision.outcome, Outcome.SUCCESS)

    def test_four_out_of_five_with_no_issues_succeeds(self):
        decision = decide(review(4), iteration=1)
        self.assertEqual(decision.outcome, Outcome.SUCCESS)

    def test_four_out_of_five_with_only_p2_issues_succeeds(self):
        """The plateau case: the reviewer lists nits and never goes higher."""
        decision = decide(
            review(4, "[P2] a.py:1 — Style: Nit.", "[P2] b.py:9 — Style: Another nit."),
            iteration=2,
        )
        self.assertEqual(decision.outcome, Outcome.SUCCESS)


class NotYetTest(unittest.TestCase):
    def test_four_out_of_five_with_a_p1_continues(self):
        decision = decide(review(4, "[P1] a.py:1 — Bug: Real bug."), iteration=1)
        self.assertEqual(decision.outcome, Outcome.CONTINUE)

    def test_four_out_of_five_with_a_p0_continues(self):
        decision = decide(review(4, "[P0] a.py:1 — Security: Wide open."), iteration=1)
        self.assertEqual(decision.outcome, Outcome.CONTINUE)

    def test_three_out_of_five_continues_even_with_only_p2(self):
        """A low score is a low score; the P2 allowance is only for score >= 4."""
        decision = decide(review(3, "[P2] a.py:1 — Style: Nit."), iteration=1)
        self.assertEqual(decision.outcome, Outcome.CONTINUE)

    def test_an_untagged_issue_blocks_a_four(self):
        decision = decide(review(4, "a.py:1 — Bug: No severity tag."), iteration=1)
        self.assertEqual(decision.outcome, Outcome.CONTINUE)

    def test_an_unknown_severity_blocks_a_four(self):
        """P3 is not in the contract, so it cannot be trusted as cosmetic."""
        for tag in ("[P3]", "[P9]", "[HIGH]", "[critical]"):
            with self.subTest(tag=tag):
                decision = decide(review(4, f"{tag} a.py:1 — X: Y."), iteration=1)
                self.assertEqual(decision.outcome, Outcome.CONTINUE)


class LimitTest(unittest.TestCase):
    def test_a_non_success_at_the_limit_stops_for_a_human(self):
        decision = decide(review(3), iteration=MAX_ITERATIONS)
        self.assertEqual(decision.outcome, Outcome.HUMAN)

    def test_a_blocking_p1_at_the_limit_stops_for_a_human(self):
        decision = decide(
            review(4, "[P1] a.py:1 — Bug: Still there."), iteration=MAX_ITERATIONS
        )
        self.assertEqual(decision.outcome, Outcome.HUMAN)

    def test_success_at_the_limit_is_still_success(self):
        """The limit stops attempts, it does not override a good review."""
        decision = decide(review(5), iteration=MAX_ITERATIONS)
        self.assertEqual(decision.outcome, Outcome.SUCCESS)

    def test_the_last_attempt_before_the_limit_may_continue(self):
        decision = decide(review(3), iteration=MAX_ITERATIONS - 1)
        self.assertEqual(decision.outcome, Outcome.CONTINUE)


class MessageTest(unittest.TestCase):
    """Every exit path has to be readable in a log with nothing else around it."""

    def test_success_says_ready_for_merge(self):
        decision = decide(review(5), iteration=1)
        self.assertIn("ready for merge", decision.message.lower())

    def test_success_on_a_four_says_why_it_counts(self):
        """A 4/5 that ends the loop needs to explain itself in the log."""
        decision = decide(review(4, "[P2] a.py:1 — Style: Nit."), iteration=1)
        self.assertIn("4/5", decision.message)
        self.assertIn("P0/P1", decision.message)

    def test_a_four_with_no_issues_does_not_claim_two_things(self):
        """`(4/5: no P0/P1 issues, no issues)` reads as a contradiction."""
        decision = decide(review(4), iteration=1)
        self.assertIn("4/5", decision.message)
        self.assertEqual(decision.message.count("no issues"), 1)
        self.assertNotIn("P0/P1", decision.message)

    def test_the_tolerated_count_is_pluralised(self):
        for count, expected in ((1, "1 P2 issue)"), (2, "2 P2 issues)")):
            with self.subTest(count=count):
                issues = tuple(f"[P2] f{i}.py:{i} — Style: Nit." for i in range(count))
                self.assertIn(expected, decide(review(4, *issues), iteration=1).message)

    def test_continue_names_the_score_and_the_next_attempt(self):
        decision = decide(review(3, "[P1] a.py:1 — Bug: Bad."), iteration=2)
        self.assertIn("3/5", decision.message)
        self.assertIn("iteration 3", decision.message)

    def test_continue_lists_the_blocking_issues(self):
        decision = decide(
            review(4, "[P1] a.py:1 — Bug: Real.", "[P2] b.py:2 — Style: Nit."),
            iteration=1,
        )
        self.assertIn("a.py:1", decision.message)
        self.assertNotIn("b.py:2", decision.message)

    def test_human_says_the_limit_was_reached(self):
        decision = decide(review(3), iteration=MAX_ITERATIONS)
        self.assertIn(str(MAX_ITERATIONS), decision.message)
        self.assertIn("human", decision.message.lower())

    def test_every_message_names_the_pr_when_it_is_known(self):
        for score, iteration in ((5, 1), (3, 1), (3, MAX_ITERATIONS)):
            with self.subTest(score=score, iteration=iteration):
                decision = decide(review(score), iteration=iteration, pr=42)
                self.assertIn("#42", decision.message)


if __name__ == "__main__":
    unittest.main()
