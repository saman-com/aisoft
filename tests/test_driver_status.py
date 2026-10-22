"""Tests for the final status a stopped loop prints.

T4.4 is the last thing a human reads after an unattended run, so the failure that
matters is a wrong verdict, not a clumsy sentence. Three rules get their own
tests:

- **A non-success is never printed as ready for merge.** The loop's whole job is
  to say whether the PR can be merged. Saying "ready" for a review that is not
  clean is the one output that causes real damage, because the obvious next step
  is `gh pr merge`.
- **Every stop carries the PR URL** when one is known. The reader of this line is
  being asked to go and do something, and the address is the difference between
  acting and hunting for it.
- **The reason is the decision's reason.** `decide` already knows why the loop
  stopped; the status only frames it. Re-deriving the reason here would let the
  two disagree.

The status is built from a `Decision`, so these tests construct decisions through
`decide` rather than hand-building them, exactly as the cycle does. A hand-built
`Decision(Outcome.SUCCESS, ...)` would test formatting against a state the rule
cannot produce.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from driver_decision import Outcome, decide  # noqa: E402
from driver_iteration import MAX_ITERATIONS  # noqa: E402
from driver_pr import PullRequest  # noqa: E402
from driver_status import EXIT_CODES, report  # noqa: E402
from parse_contract_comment import Review  # noqa: E402

PR = PullRequest(
    number=29,
    url="https://github.com/saman-com/aisoft/pull/29",
    state="OPEN",
    head_ref="slice-4-full-loop",
)

BLOCKING = Review(
    score=4,
    issues=("[P1] a.py:1 — Bug: loop never times out",),
    agent_prompt="Fix the loop.",
)


def success(score: int = 5, *issues: str):
    return decide(Review(score=score, issues=issues, agent_prompt="Done."), iteration=1, pr=PR.number)


def human(score: int = 3):
    return decide(
        Review(score=score, issues=("[P1] a.py:1 — Bug: real",), agent_prompt="Fix."),
        iteration=MAX_ITERATIONS,
        pr=PR.number,
    )


def repeated(score: int = 4):
    return decide(
        Review(score=score, issues=("[P1] a.py:9 — Bug: loop never times out",), agent_prompt="Fix."),
        iteration=2,
        pr=PR.number,
        previous=BLOCKING,
    )


def continuing(score: int = 3):
    return decide(
        Review(score=score, issues=("[P1] a.py:1 — Bug: real",), agent_prompt="Fix."),
        iteration=2,
        pr=PR.number,
    )


class SuccessStatusTest(unittest.TestCase):
    def test_it_says_ready_for_merge(self):
        text = report(success(), pr=PR).render()
        self.assertIn("READY FOR MERGE", text)

    def test_it_shows_the_score(self):
        self.assertIn("5/5", report(success(5), pr=PR).render())

    def test_it_shows_a_four_out_of_five_as_four_out_of_five(self):
        """The 4/5 exit is a success, but it must not read like a perfect review."""
        text = report(success(4, "[P2] a.py:1 — Style: Nit."), pr=PR).render()
        self.assertIn("4/5", text)
        self.assertNotIn("5/5", text)

    def test_it_prints_the_pr_url(self):
        self.assertIn(PR.url, report(success(), pr=PR).render())

    def test_it_is_ready(self):
        self.assertTrue(report(success(), pr=PR).is_ready)

    def test_it_does_not_ask_for_a_human(self):
        self.assertFalse(report(success(), pr=PR).needs_human)


class HumanStatusTest(unittest.TestCase):
    def test_it_says_stopped_after_the_iteration_limit(self):
        text = report(human(), pr=PR).render()
        self.assertIn(f"STOPPED AFTER {MAX_ITERATIONS} ITERATIONS", text)

    def test_it_says_a_human_review_is_needed(self):
        self.assertIn("HUMAN REVIEW NEEDED", report(human(), pr=PR).render())

    def test_it_prints_the_pr_url(self):
        self.assertIn(PR.url, report(human(), pr=PR).render())

    def test_it_is_not_ready(self):
        self.assertFalse(report(human(), pr=PR).is_ready)

    def test_it_needs_a_human(self):
        self.assertTrue(report(human(), pr=PR).needs_human)

    def test_it_counts_the_iterations_that_were_attempted(self):
        self.assertIn(f"{MAX_ITERATIONS} iteration", report(human(), pr=PR).render())


class RepeatedStatusTest(unittest.TestCase):
    def test_it_says_the_same_blocking_issues_came_back(self):
        text = report(repeated(), pr=PR).render()
        self.assertIn("SAME BLOCKING ISSUES", text)

    def test_it_says_a_human_review_is_needed(self):
        self.assertIn("HUMAN REVIEW NEEDED", report(repeated(), pr=PR).render())

    def test_it_prints_the_pr_url(self):
        self.assertIn(PR.url, report(repeated(), pr=PR).render())

    def test_it_is_not_ready(self):
        self.assertFalse(report(repeated(), pr=PR).is_ready)

    def test_it_does_not_claim_the_limit_was_hit(self):
        """Out of tries and going in circles are different problems to fix."""
        text = report(repeated(), pr=PR).render()
        self.assertNotIn("STOPPED AFTER", text)

    def test_it_names_the_iteration_that_changed_nothing(self):
        self.assertIn("iteration 2", report(repeated(), pr=PR).render().lower())


class ContinueStatusTest(unittest.TestCase):
    def test_it_never_says_ready_for_merge(self):
        text = report(continuing(), pr=PR).render()
        self.assertNotIn("READY FOR MERGE", text)

    def test_it_says_another_attempt_is_next(self):
        self.assertIn("NOT FINISHED", report(continuing(), pr=PR).render())

    def test_it_is_neither_ready_nor_a_human_stop(self):
        status = report(continuing(), pr=PR)
        self.assertFalse(status.is_ready)
        self.assertFalse(status.needs_human)

    def test_it_is_not_a_final_status(self):
        self.assertFalse(report(continuing(), pr=PR).is_final)


class NeverMislabeledTest(unittest.TestCase):
    """The scan across every outcome, so a new Outcome cannot slip past."""

    def reports(self):
        for outcome, decision in (
            (Outcome.SUCCESS, success()),
            (Outcome.CONTINUE, continuing()),
            (Outcome.HUMAN, human()),
            (Outcome.REPEATED, repeated()),
        ):
            with self.subTest(outcome=outcome.value):
                yield report(decision, pr=PR)

    def test_only_success_says_ready_for_merge(self):
        for status in self.reports():
            with self.subTest(outcome=status.outcome.value):
                if status.outcome is Outcome.SUCCESS:
                    self.assertIn("READY FOR MERGE", status.headline)
                else:
                    self.assertNotIn("ready for merge", status.render().lower())

    def test_no_other_outcome_is_ready(self):
        for status in self.reports():
            with self.subTest(outcome=status.outcome.value):
                self.assertEqual(status.is_ready, status.outcome is Outcome.SUCCESS)

    def test_every_stop_except_success_needs_a_human(self):
        for status in self.reports():
            with self.subTest(outcome=status.outcome.value):
                if status.outcome in (Outcome.HUMAN, Outcome.REPEATED):
                    self.assertTrue(status.needs_human)

    def test_every_outcome_has_an_exit_code(self):
        for outcome in Outcome:
            with self.subTest(outcome=outcome.value):
                self.assertIn(outcome, EXIT_CODES)


class UrlTest(unittest.TestCase):
    def test_a_missing_url_falls_back_to_the_pr_number(self):
        """`resolve` can hand back an empty url; a bare `#29` still identifies it."""
        bare = PullRequest(number=29, url="", state="OPEN", head_ref="b")
        text = report(human(), pr=bare).render()
        self.assertIn("#29", text)

    def test_the_placeholder_for_a_skipped_lookup_is_not_printed(self):
        """`driver._pull_request` sets this when `--pr` skips the gh lookup.

        The placeholder is a note to a reader of the JSON output, not an address.
        Printing it where the URL belongs produces a line nobody can open.
        """
        placeholder = PullRequest(
            number=29, url="(given on the command line)", state="OPEN", head_ref=""
        )
        status = report(human(), pr=placeholder)
        self.assertEqual(status.address, "#29")
        self.assertNotIn("given on the command line", status.render())

    def test_a_missing_pr_still_renders(self):
        text = report(decide(BLOCKING, iteration=MAX_ITERATIONS)).render()
        self.assertIn("HUMAN REVIEW NEEDED", text)

    def test_no_pr_is_not_ready(self):
        self.assertFalse(report(decide(BLOCKING, iteration=MAX_ITERATIONS)).is_ready)


class RenderTest(unittest.TestCase):
    def test_the_headline_comes_first_so_it_is_greppable(self):
        text = report(human(), pr=PR).render()
        self.assertEqual(text.splitlines()[0], report(human(), pr=PR).headline)

    def test_the_decision_reason_is_included(self):
        """`decide` already explains itself; the status must not lose that."""
        decision = human()
        self.assertIn(decision.message, report(decision, pr=PR).render())

    def test_it_renders_one_line_per_fact(self):
        text = report(human(), pr=PR).render()
        self.assertGreaterEqual(len(text.splitlines()), 3)

    def test_the_verdict_is_not_hidden_in_a_paragraph(self):
        for status in (report(success(), pr=PR), report(human(), pr=PR),
                       report(repeated(), pr=PR)):
            with self.subTest(outcome=status.outcome.value):
                self.assertFalse(status.headline.endswith("."))


class ExitCodeTest(unittest.TestCase):
    """The exit code answers "did the tool work", not "is the PR good".

    Both stops and successes exit 0: the cycle did what it was asked. A non-zero
    code would collide with the driver's documented 1/2/3 for gh, git and parse
    failures, and a caller that cannot tell "needs a human" from "gh is broken"
    will page someone for the wrong thing.
    """

    def test_a_success_exits_zero(self):
        self.assertEqual(EXIT_CODES[Outcome.SUCCESS], 0)

    def test_a_human_stop_exits_zero(self):
        self.assertEqual(EXIT_CODES[Outcome.HUMAN], 0)

    def test_a_repeated_stop_exits_zero(self):
        self.assertEqual(EXIT_CODES[Outcome.REPEATED], 0)

    def test_the_failure_codes_stay_free(self):
        self.assertNotIn(1, EXIT_CODES.values())
        self.assertNotIn(2, EXIT_CODES.values())
        self.assertNotIn(3, EXIT_CODES.values())


if __name__ == "__main__":
    unittest.main()
