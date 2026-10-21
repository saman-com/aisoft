"""Tests for one complete fix cycle.

T3.4 is the first place the pieces meet, so what it verifies is the *seam*: that
the review read before the push is the baseline the wait compares against, that a
failed step stops the cycle instead of continuing into a meaningless one, and that
each outcome keeps the exit code the other drivers already use.

The order is load-bearing:

    read review -> run Pi -> commit+push -> wait for a NEWER review -> parse

- Pi must not run on a review that failed to parse. Its prompt comes from that
  review, and a partial prompt sends Pi to edit code based on nothing.
- The push must happen before the wait, or the wait returns the review that was
  already read.
- The wait must use the Comment read *before* the push, not its id: the poster
  edits one comment in place, so the id survives (T3.3).

A cycle is not "Pi ran". A cycle is "Pi ran, the change landed, and the reviewer
looked at it again".
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import driver_cycle  # noqa: E402
from driver_comments import (  # noqa: E402
    DEFAULT_INTERVAL,
    DEFAULT_TIMEOUT,
    Comment,
    ReviewTimeout,
)
from driver_cycle import CycleResult, NoReview, run_cycle  # noqa: E402
from driver_decision import Outcome, decide  # noqa: E402
from driver_git import GitError, NothingToPush, PushResult  # noqa: E402
from driver_iteration import MAX_ITERATIONS, IterationLimit  # noqa: E402
from driver_pi import PiError, PiRun  # noqa: E402
from driver_pr import GhFailed, NoPullRequest, PullRequest  # noqa: E402
from parse_contract_comment import ParseError, Review  # noqa: E402

PR = PullRequest(
    number=31,
    url="https://github.com/saman-com/aisoft/pull/31",
    state="OPEN",
    head_ref="slice-3-single-fix-cycle",
)

BEFORE_BODY = (
    "## Confidence Score\n4/5\n\n## Issues\n- a.py:1-2 — Bug: wrong\n\n"
    "## Agent Prompt\nFix the bug in a.py.\n"
)
AFTER_BODY = (
    "## Confidence Score\n5/5\n\n## Issues\nNone\n\n## Agent Prompt\nNo issues found.\n"
)

BEFORE = Comment(id=100, body=BEFORE_BODY, author="github-actions[bot]")
AFTER = Comment(id=100, body=AFTER_BODY, author="github-actions[bot]")
BEFORE_REVIEW = Review(
    score=4, issues=("a.py:1-2 — Bug: wrong",), agent_prompt="Fix the bug in a.py."
)
AFTER_REVIEW = Review(score=5, issues=(), agent_prompt="No issues found.")
CLEAN_REVIEW = Review(score=5, issues=(), agent_prompt="No issues found.")


class Fixture:
    """Every collaborator the cycle touches, replaceable per test.

    `patches` maps a module attribute name to the mock that replaces it, so a test
    can spy on one collaborator without rebuilding the whole stage.
    """

    def __init__(
        self,
        *,
        before=BEFORE,
        before_review=BEFORE_REVIEW,
        after=AFTER,
        after_review=AFTER_REVIEW,
        pi_ok=True,
        push_ok=True,
        nothing_to_push=False,
        wait_error=None,
        parse_error=None,
    ):
        self.spies: dict[str, mock.MagicMock] = {
            "resolve": mock.MagicMock(return_value=PR),
            "list_comments": mock.MagicMock(return_value=[]),
            "contract_comment": mock.MagicMock(return_value=before),
            "parse_comment": mock.MagicMock(
                side_effect=[parse_error] if parse_error else [before_review, after_review]
            ),
            "run_pi": mock.MagicMock(
                return_value=PiRun(
                    returncode=0 if pi_ok else 1,
                    stdout="did the work\n",
                    stderr="" if pi_ok else "pi exploded",
                )
            ),
            "commit_and_push": mock.MagicMock(
                side_effect=self._push(push_ok, nothing_to_push)
            ),
            "wait_for_contract_comment": mock.MagicMock(
                side_effect=self._wait(wait_error, after)
            ),
        }

    @staticmethod
    def _push(push_ok, nothing_to_push):
        def push(**_kwargs):
            if nothing_to_push:
                raise NothingToPush("nothing to push")
            if not push_ok:
                raise GitError("push failed")
            return PushResult(branch=PR.head_ref, commit="abc123", made_commit=True)

        return push

    @staticmethod
    def _wait(wait_error, after):
        def wait(*_args, **_kwargs):
            if wait_error is not None:
                raise wait_error
            return after

        return wait

    def run(self, iteration=1, **kwargs):
        patchers = [
            mock.patch.object(driver_cycle, name, spy) for name, spy in self.spies.items()
        ]
        for patcher in patchers:
            patcher.start()
        try:
            return run_cycle(repo="owner/repo", iteration=iteration, **kwargs)
        finally:
            for patcher in reversed(patchers):
                patcher.stop()

    # Convenience accessors: the recorded call, not the mock.
    def kwargs_of(self, name: str) -> dict:
        return self.spies[name].call_args.kwargs

    def called(self, name: str) -> bool:
        return self.spies[name].called


class HappyPathTest(unittest.TestCase):
    def test_one_cycle_returns_before_and_after(self):
        result = Fixture().run()
        self.assertEqual(result.before.score, 4)
        self.assertEqual(result.after.score, 5)
        self.assertTrue(result.after.is_clean)
        self.assertTrue(result.pi_ok)
        self.assertTrue(result.pushed)

    def test_the_prompt_given_to_pi_is_the_one_from_the_review(self):
        fixture = Fixture()
        fixture.run()
        self.assertEqual(
            fixture.spies["run_pi"].call_args.args[0], BEFORE_REVIEW.agent_prompt
        )

    def test_the_wait_uses_the_comment_read_before_the_push(self):
        """T3.3: the id survives an in-place rewrite, the body does not."""
        fixture = Fixture()
        fixture.run()
        baseline = fixture.kwargs_of("wait_for_contract_comment")["baseline"]
        self.assertEqual(baseline.body, BEFORE.body)
        self.assertEqual(baseline.id, BEFORE.id)

    def test_the_wait_uses_the_documented_numbers(self):
        fixture = Fixture()
        fixture.run()
        kwargs = fixture.kwargs_of("wait_for_contract_comment")
        self.assertEqual(kwargs["timeout"], DEFAULT_TIMEOUT)
        self.assertEqual(kwargs["interval"], DEFAULT_INTERVAL)

    def test_the_push_names_the_branch_the_pr_is_on(self):
        fixture = Fixture()
        fixture.run()
        self.assertEqual(fixture.kwargs_of("commit_and_push")["expected_branch"], PR.head_ref)

    def test_the_push_carries_the_score_and_issue_count(self):
        """The commit message should say what the review asked for."""
        fixture = Fixture()
        fixture.run()
        kwargs = fixture.kwargs_of("commit_and_push")
        self.assertEqual(kwargs["score"], 4)
        self.assertEqual(kwargs["issues"], 1)

    def test_the_after_comment_is_parsed_with_its_own_id(self):
        fixture = Fixture()
        fixture.run()
        last = fixture.spies["parse_comment"].call_args
        self.assertEqual(last.kwargs["comment_id"], AFTER.id)
        self.assertEqual(last.kwargs["pr"], PR.number)


class EarlyStopTest(unittest.TestCase):
    def test_a_clean_review_does_not_run_pi(self):
        """Nothing to fix means nothing to do; a no-op push would burn an iteration."""
        fixture = Fixture(before_review=CLEAN_REVIEW)
        result = fixture.run()
        self.assertFalse(result.pi_ran)
        self.assertFalse(result.pushed)
        self.assertFalse(fixture.called("run_pi"))
        self.assertTrue(result.after.is_clean)

    def test_a_parse_failure_stops_before_pi_runs(self):
        fixture = Fixture(parse_error=ParseError("bad section"))
        with self.assertRaises(ParseError):
            fixture.run()
        self.assertFalse(fixture.called("run_pi"))

    def test_a_pi_failure_does_not_commit_or_wait(self):
        fixture = Fixture(pi_ok=False)
        with self.assertRaises(PiError):
            fixture.run()
        self.assertFalse(fixture.called("commit_and_push"))
        self.assertFalse(fixture.called("wait_for_contract_comment"))

    def test_nothing_to_push_is_its_own_outcome(self):
        """Pi ran but changed nothing: not success, not a crash."""
        fixture = Fixture(nothing_to_push=True)
        with self.assertRaises(NothingToPush):
            fixture.run()
        self.assertFalse(fixture.called("wait_for_contract_comment"))

    def test_a_failed_push_does_not_wait(self):
        fixture = Fixture(push_ok=False)
        with self.assertRaises(GitError):
            fixture.run()
        self.assertFalse(fixture.called("wait_for_contract_comment"))

    def test_a_review_timeout_is_reported_after_the_push(self):
        fixture = Fixture(wait_error=ReviewTimeout("no new review"))
        with self.assertRaises(ReviewTimeout):
            fixture.run()
        self.assertTrue(fixture.called("commit_and_push"))


class IterationLimitTest(unittest.TestCase):
    """T4.1: the cap is enforced on the way in, not admitted afterwards."""

    def test_the_sixth_iteration_is_refused_before_anything_happens(self):
        """No PR read, no Pi run, no commit, no push, no wait.

        Checking after the work would push a sixth fix and only then report that
        it should not have run. Every collaborator must be untouched.
        """
        fixture = Fixture()
        with self.assertRaises(IterationLimit):
            fixture.run(iteration=MAX_ITERATIONS + 1)
        for name in (
            "resolve",
            "list_comments",
            "run_pi",
            "commit_and_push",
            "wait_for_contract_comment",
        ):
            with self.subTest(collaborator=name):
                self.assertFalse(fixture.called(name))

    def test_the_last_allowed_iteration_still_runs_when_it_can_help(self):
        """T4.2 changed the answer here, and this pins the new one.

        On the fifth attempt the loop still acts if the review says fixing it
        would help. It is the *sixth* attempt that T4.1 forbids, not the fifth.
        """
        fixture = Fixture()
        result = fixture.run(iteration=MAX_ITERATIONS)
        self.assertTrue(result.pi_ran)
        self.assertTrue(fixture.called("commit_and_push"))

    def test_the_last_attempt_is_not_stopped_by_the_limit_on_the_way_in(self):
        """No HUMAN verdict can be reached before the first review of attempt 5.

        The in-guard count is `iteration - 1`, so at most 4 attempts have been
        made and the rule cannot report the limit yet. This pins that the fifth
        attempt is a real attempt rather than a no-op the limit swallows: Pi ran
        and the verdict came from the review the fifth attempt produced.
        """
        result = Fixture().run(iteration=MAX_ITERATIONS)
        self.assertTrue(result.pi_ran)
        self.assertEqual(result.decision.iteration, MAX_ITERATIONS)

    def test_the_iteration_label_is_the_one_in_the_log(self):
        """`iteration N/5` is what an operator sees and what the issue records."""
        from driver_iteration import describe_iteration  # noqa: PLC0415

        self.assertEqual(describe_iteration(MAX_ITERATIONS), "iteration 5/5")


class DecisionTest(unittest.TestCase):
    """T4.2: the cycle acts on the rule, not on `score == 5`.

    These run the seam, not the rule itself (that lives in
    `tests/test_driver_decision.py`). What matters here is that the cycle reads
    the decision and that each of the three outcomes has the effects the design
    says: success stops silently, continue runs Pi, human stops without a push.
    """

    def test_a_four_with_only_nits_stops_without_running_pi(self):
        """The plateau case: 4/5 plus P2 is the loop's success, not a retry."""
        fixture = Fixture(
            before_review=Review(
                score=4,
                issues=("[P2] a.py:1 — Style: Nit.",),
                agent_prompt="Fix the nit.",
            )
        )
        result = fixture.run()
        self.assertFalse(fixture.called("run_pi"))
        self.assertFalse(fixture.called("commit_and_push"))
        self.assertIs(result.decision.outcome, Outcome.SUCCESS)

    def test_a_four_with_a_p1_still_runs_pi(self):
        fixture = Fixture(
            before_review=Review(
                score=4,
                issues=("[P1] a.py:1 — Bug: Real.",),
                agent_prompt="Fix the bug.",
            )
        )
        result = fixture.run()
        self.assertTrue(result.pi_ran)
        self.assertIs(result.decision.outcome, Outcome.SUCCESS)

    def test_three_out_of_five_runs_pi(self):
        fixture = Fixture(
            before_review=Review(score=3, issues=(), agent_prompt="Do better.")
        )
        result = fixture.run()
        self.assertTrue(result.pi_ran)

    def test_a_failing_review_after_the_fifth_attempt_goes_to_a_human(self):
        """The fifth attempt runs; it is the *sixth* that the limit forbids.

        So the limit is not felt on the way in at iteration 5. It is felt on the
        way out, when attempt 5's review is still not good enough: `decide`
        reports HUMAN and the caller stops there instead of looping again.
        """
        fixture = Fixture(
            before_review=Review(
                score=3, issues=("[P1] a.py:1 — Bug: Bad.",), agent_prompt="Fix it."
            ),
            after_review=Review(
                score=4, issues=("[P1] a.py:1 — Bug: Still bad.",),
                agent_prompt="Fix it again.",
            ),
        )
        result = fixture.run(iteration=MAX_ITERATIONS)
        self.assertTrue(result.pi_ran)
        self.assertIs(result.decision.outcome, Outcome.HUMAN)
        self.assertEqual(result.decision.iteration, MAX_ITERATIONS)

    def test_a_fifth_attempt_that_succeeds_does_not_go_to_a_human(self):
        """The limit must not report failure for a PR that just passed."""
        fixture = Fixture()
        result = fixture.run(iteration=MAX_ITERATIONS)
        self.assertIs(result.decision.outcome, Outcome.SUCCESS)

    def test_the_decision_is_taken_on_the_review_after_the_push(self):
        """Otherwise the loop decides from the review it already fixed."""
        fixture = Fixture()
        result = fixture.run()
        self.assertIsNotNone(result.after)
        self.assertEqual(result.decision.iteration, 1)
        self.assertIs(result.decision.outcome, Outcome.SUCCESS)

    def test_a_dry_run_reports_the_decision_without_acting(self):
        fixture = Fixture(
            before_review=Review(score=4, issues=("[P2] a.py:1 — Style: Nit.",),
                                 agent_prompt="Fix the nit.")
        )
        result = fixture.run(dry_run=True)
        self.assertIs(result.decision.outcome, Outcome.SUCCESS)
        self.assertFalse(result.pi_ran)


class RepeatedIssueTest(unittest.TestCase):
    """T4.3: the cycle hands the rule both reviews, so a no-op fix is visible.

    The rule itself is tested in `tests/test_driver_decision.py`. What is only
    testable here is the wiring: that the review read *before* Pi ran is the
    baseline the new review is compared against. Passing the wrong one (or none)
    would silently disable detection and the loop would look normal while burning
    its whole budget.
    """

    SAME_ISSUE = "[P1] a.py:1 — Bug: loop never times out"

    def fixture(self, after_issue: str) -> Fixture:
        return Fixture(
            before_review=Review(
                score=4, issues=(self.SAME_ISSUE,), agent_prompt="Fix the loop."
            ),
            after_review=Review(
                score=4, issues=(after_issue,), agent_prompt="Fix the loop."
            ),
        )

    def test_the_same_issue_after_pi_stops_as_repeated(self):
        """The real case: Pi ran, pushed, and the reviewer reported it again."""
        fixture = self.fixture("[P1] a.py:1 — Bug: loop never times out")
        result = fixture.run(iteration=2)
        self.assertTrue(result.pi_ran)
        self.assertIs(result.decision.outcome, Outcome.REPEATED)

    def test_a_shifted_line_number_is_still_the_same_issue(self):
        """Pi's commit moves lines, which must not look like a new finding."""
        fixture = self.fixture("[P1] a.py:57 — Bug: loop never times out")
        result = fixture.run(iteration=2)
        self.assertIs(result.decision.outcome, Outcome.REPEATED)

    def test_a_different_issue_after_pi_continues(self):
        """Progress means the loop keeps its remaining attempts."""
        fixture = self.fixture("[P1] b.py:2 — Bug: commit message missing")
        result = fixture.run(iteration=2)
        self.assertIs(result.decision.outcome, Outcome.CONTINUE)

    def test_a_clean_review_after_pi_is_success_not_repeated(self):
        """Success is decided first, so it cannot be masked by detection."""
        fixture = Fixture(
            before_review=Review(
                score=4, issues=(self.SAME_ISSUE,), agent_prompt="Fix the loop."
            ),
            after_review=Review(score=5, issues=(), agent_prompt="No issues found."),
        )
        result = fixture.run(iteration=2)
        self.assertIs(result.decision.outcome, Outcome.SUCCESS)

    def test_the_first_decision_of_a_cycle_is_never_a_repeat(self):
        """Nothing has been attempted when `before` is read, so nothing is a no-op.

        The entry-side `decide` call gets no `previous`. If it did, a review that
        repeats the previous cycle's findings would stop the loop before Pi ever
        ran, which is the opposite of what detection is for.
        """
        fixture = self.fixture("[P1] a.py:1 — Bug: loop never times out")
        result = fixture.run(iteration=3, dry_run=True)
        self.assertIs(result.decision.outcome, Outcome.CONTINUE)


class DryRunTest(unittest.TestCase):
    def test_dry_run_reads_and_parses_but_changes_nothing(self):
        fixture = Fixture()
        result = fixture.run(dry_run=True)
        self.assertEqual(result.before.score, 4)
        self.assertFalse(result.pi_ran)
        self.assertFalse(result.pushed)
        self.assertIsNone(result.after)
        self.assertFalse(fixture.called("run_pi"))


class CliTest(unittest.TestCase):
    def test_exit_0_on_a_completed_cycle(self):
        with mock.patch.object(
            driver_cycle,
            "run_cycle",
            return_value=CycleResult(
                pr=PR, before=BEFORE_REVIEW, after=AFTER_REVIEW,
                pi_ok=True, pushed=True, pi_ran=True,
            ),
        ):
            self.assertEqual(driver_cycle.main(["--pr", "31"]), 0)

    def test_exit_2_when_there_is_nothing_to_do(self):
        """A not-yet-posted review is "try again", not an environment failure.

        PR-Agent reported this as a bug twice, claiming `NoReview` (a DriverError)
        would be caught by the exit-1 branch first. Python picks the first matching
        except clause, and the exit-2 clause lists `NoReview` explicitly, so the
        two claims were wrong - but the ordering is what makes it wrong, so pin it.
        """
        for error in (
            NoPullRequest("no pr"),
            NoReview("no review yet"),
            ReviewTimeout("no new review"),
            NothingToPush("nothing"),
        ):
            with self.subTest(error=type(error).__name__):
                with mock.patch.object(driver_cycle, "run_cycle", side_effect=error):
                    self.assertEqual(driver_cycle.main(["--pr", "31"]), 2)

    def test_exit_1_when_the_environment_fails(self):
        for error in (
            GhFailed("gh down"),
            PiError("pi down"),
            GitError("git down"),
            IterationLimit("iteration 6 is past the hard limit of 5"),
        ):
            with self.subTest(error=type(error).__name__):
                with mock.patch.object(driver_cycle, "run_cycle", side_effect=error):
                    self.assertEqual(driver_cycle.main(["--pr", "31"]), 1)

    def test_exit_3_when_the_contract_is_broken(self):
        with mock.patch.object(driver_cycle, "run_cycle", side_effect=ParseError("bad")):
            self.assertEqual(driver_cycle.main(["--pr", "31"]), 3)

    def status_output(self, result) -> str:
        """What `main` printed, as one string."""
        with mock.patch.object(driver_cycle, "run_cycle", return_value=result), \
             mock.patch.object(driver_cycle.driver_log, "log") as logged:
            code = driver_cycle.main(["--pr", "31"])
        text = "\n".join(str(call.args[0]) for call in logged.call_args_list)
        return text, code

    def test_a_finished_cycle_prints_the_status(self):
        """T4.4: the last thing printed is what the operator acts on."""
        text, code = self.status_output(
            CycleResult(
                pr=PR, before=BEFORE_REVIEW, after=AFTER_REVIEW,
                pi_ok=True, pushed=True, pi_ran=True,
                decision=decide(AFTER_REVIEW, iteration=1, pr=PR.number),
            )
        )
        self.assertEqual(code, 0)
        self.assertIn("READY FOR MERGE", text)
        self.assertIn(PR.url, text)

    def test_a_human_stop_prints_the_status(self):
        text, code = self.status_output(
            CycleResult(
                pr=PR, before=BEFORE_REVIEW, after=BEFORE_REVIEW,
                pi_ok=True, pushed=False, pi_ran=False,
                decision=decide(BEFORE_REVIEW, iteration=MAX_ITERATIONS, pr=PR.number),
            )
        )
        self.assertEqual(code, 0)
        self.assertIn("HUMAN REVIEW NEEDED", text)
        self.assertIn(PR.url, text)
        self.assertNotIn("READY FOR MERGE", text)

    def test_the_status_is_printed_after_the_delta(self):
        """A reader scrolling to the bottom must land on the verdict."""
        text, _ = self.status_output(
            CycleResult(
                pr=PR, before=BEFORE_REVIEW, after=AFTER_REVIEW,
                pi_ok=True, pushed=True, pi_ran=True,
                decision=decide(AFTER_REVIEW, iteration=1, pr=PR.number),
            )
        )
        self.assertGreater(
            text.index("READY FOR MERGE"), text.index("on " + PR.url)
        )

    def test_a_cycle_without_a_decision_still_prints_the_delta(self):
        """Refusing to invent a verdict is better than printing a wrong one."""
        text, code = self.status_output(
            CycleResult(
                pr=PR, before=BEFORE_REVIEW, after=None,
                pi_ok=False, pushed=False, pi_ran=False,
            )
        )
        self.assertEqual(code, 0)
        self.assertNotIn("READY FOR MERGE", text)
        self.assertIn("dry run", text)

    def test_dry_run_is_passed_through(self):
        with mock.patch.object(
            driver_cycle,
            "run_cycle",
            return_value=CycleResult(
                pr=PR, before=BEFORE_REVIEW, after=None,
                pi_ok=False, pushed=False, pi_ran=False,
            ),
        ) as ran:
            driver_cycle.main(["--pr", "31", "--dry-run"])
        self.assertTrue(ran.call_args.kwargs["dry_run"])


if __name__ == "__main__":
    unittest.main()
