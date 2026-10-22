"""Tests for the multi-iteration loop that drives the cycles.

Slice 4 exists so a PR can go from "not good enough" to "ready" without a person
watching. Everything the loop needs is already tested on its own: the cycle
(T3.4), the stopping rule (T4.2), the repeat detection (T4.3) and the status
(T4.4). What is left is the *repetition*, and that is what these tests pin:

- The loop stops on every outcome that is not CONTINUE, and on nothing else.
- It hands the next iteration the next number, starting at 1.
- It never asks for a sixth attempt, whatever a cycle claims.
- It prints the final status exactly once, and it is the last thing printed.

The cycle is injected rather than patched, so a test can script a whole run as a
list of results. That keeps these tests about the repetition and not about Pi,
git or gh.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import driver_cycle  # noqa: E402
import driver_loop  # noqa: E402
from driver_comments import DEFAULT_INTERVAL, DEFAULT_TIMEOUT, Comment  # noqa: E402
from driver_cycle import CycleResult  # noqa: E402
from driver_decision import Decision, Outcome  # noqa: E402
from driver_comments import ReviewTimeout  # noqa: E402
from driver_git import NothingToPush  # noqa: E402
from driver_iteration import MAX_ITERATIONS  # noqa: E402
from driver_pi import PiError  # noqa: E402
from driver_pr import PullRequest  # noqa: E402
from parse_contract_comment import Review  # noqa: E402

PR = PullRequest(
    number=31,
    url="https://github.com/saman-com/aisoft/pull/31",
    state="OPEN",
    head_ref="slice-4-live-test",
)

BEFORE_REVIEW = Review(score=3, issues=("a.py:1-2 — Bug: wrong",), agent_prompt="Fix a.py.")
AFTER_REVIEW = Review(score=4, issues=("b.py:9 — Bug: also wrong",), agent_prompt="Fix b.py.")
CLEAN_REVIEW = Review(score=5, issues=(), agent_prompt="No issues found.")


def result(outcome: Outcome, iteration: int, *, after=None) -> CycleResult:
    """One cycle result carrying `outcome`, which is all the loop reads."""
    return CycleResult(
        pr=PR,
        before=BEFORE_REVIEW,
        after=after if after is not None else AFTER_REVIEW,
        pi_ok=True,
        pushed=True,
        pi_ran=True,
        decision=Decision(outcome, f"iteration {iteration}: {outcome.value}", iteration),
    )


class Script:
    """A stand-in for `run_cycle` that hands back scripted results.

    Records the iteration it was asked for, so a test can assert the numbering
    rather than infer it from the count of calls.
    """

    def __init__(self, outcomes: list[Outcome], *, after=None):
        self.asked: list[int] = []
        self.kwargs: list[dict] = []
        self.outcomes = outcomes
        self.after = after

    def __call__(self, **kwargs) -> CycleResult:
        self.asked.append(kwargs["iteration"])
        self.kwargs.append(kwargs)
        outcome = self.outcomes.pop(0)
        return result(outcome, kwargs["iteration"], after=self.after)

    def run(self, **kwargs):
        kwargs.setdefault("repo", "owner/repo")
        return driver_loop.run_loop(cycle=self, **kwargs)


class StoppingTest(unittest.TestCase):
    def test_a_clean_review_stops_after_one_cycle(self):
        script = Script([Outcome.SUCCESS])
        loop = script.run()
        self.assertEqual(script.asked, [1])
        self.assertEqual(loop.final.outcome, Outcome.SUCCESS)

    def test_running_out_of_attempts_stops_the_loop(self):
        script = Script([Outcome.HUMAN])
        loop = script.run()
        self.assertEqual(script.asked, [1])
        self.assertEqual(loop.final.outcome, Outcome.HUMAN)

    def test_a_repeated_fix_stops_the_loop(self):
        script = Script([Outcome.REPEATED])
        loop = script.run()
        self.assertEqual(script.asked, [1])
        self.assertEqual(loop.final.outcome, Outcome.REPEATED)

    def test_every_outcome_but_continue_ends_the_loop_at_once(self):
        """The stopping rule in one place: CONTINUE is the only "again".

        Each scripted run reports the same outcome forever, so a loop that failed
        to stop would run all five attempts instead of one.
        """
        for outcome in Outcome:
            if outcome is Outcome.CONTINUE:
                continue
            with self.subTest(outcome=outcome):
                script = Script([outcome] * 5)
                script.run()
                self.assertEqual(script.asked, [1])

    def test_it_keeps_going_while_the_rule_says_continue(self):
        script = Script([Outcome.CONTINUE, Outcome.CONTINUE, Outcome.SUCCESS])
        loop = script.run()
        self.assertEqual(script.asked, [1, 2, 3])
        self.assertEqual(loop.final.outcome, Outcome.SUCCESS)
        self.assertEqual(len(loop.iterations), 3)


class AlreadyCleanTest(unittest.TestCase):
    """The loop on a PR that needs nothing: stop at once, spend no attempt.

    Scripting `SUCCESS` only proves the loop reads the outcome correctly. These
    go through the real `run_cycle` with a clean review, so the path under test is
    the one a live run takes: the cycle decides before Pi, returns `SUCCESS`, and
    the loop stops. It is also the case the review of PR #35 raised, where a dry
    run on an already-clean PR was claimed to print `NOT FINISHED`.
    """

    # Score 5 with nothing listed, and score 4 with only a P2. Both are success,
    # which is why the rule cannot be a score comparison on its own.
    CLEAN = {
        "five": Review(score=5, issues=(), agent_prompt="No issues found."),
        "four_with_a_nit": Review(
            score=4,
            issues=("[P2] a.py:1 — Nit: rename this",),
            agent_prompt="Optional cleanup.",
        ),
    }

    def run_real_cycle(self, review, *, dry_run):
        """`run_loop` with the real `run_cycle` and only its edges faked."""
        comment = Comment(id=6328598899, body="## Confidence Score\n5/5\n", author="bot")
        pi = mock.MagicMock()
        push = mock.MagicMock()
        wait = mock.MagicMock()
        with mock.patch.object(driver_cycle, "resolve", return_value=PR), \
             mock.patch.object(driver_cycle, "list_comments", return_value=[]), \
             mock.patch.object(driver_cycle, "contract_comment", return_value=comment), \
             mock.patch.object(driver_cycle, "parse_comment", return_value=review), \
             mock.patch.object(driver_cycle, "run_pi", pi), \
             mock.patch.object(driver_cycle, "commit_and_push", push), \
             mock.patch.object(
                 driver_cycle, "wait_for_contract_comment", wait
             ), \
             mock.patch.object(driver_loop.driver_log, "log"):
            loop = driver_loop.run_loop(
                repo="owner/repo", pr=31, dry_run=dry_run, quiet=True
            )
        return loop, pi, push, wait

    def test_a_clean_pr_stops_with_success_and_never_runs_pi(self):
        for name, review in self.CLEAN.items():
            with self.subTest(review=name):
                loop, pi, push, wait = self.run_real_cycle(review, dry_run=False)
                self.assertEqual(loop.final.outcome, Outcome.SUCCESS)
                self.assertFalse(pi.called)
                self.assertFalse(push.called)
                self.assertFalse(wait.called)

    def test_a_clean_pr_costs_exactly_one_cycle(self):
        """It stops after reading, before any fix is attempted.

        One cycle is the read that established the PR was clean. A second would
        re-read the same review and reach the same verdict, and `decide` counts
        the attempts already made, so the count in the verdict must still be 0.
        """
        loop, _pi, _push, _wait = self.run_real_cycle(
            self.CLEAN["five"], dry_run=False
        )
        self.assertEqual(len(loop.iterations), 1)
        self.assertEqual(loop.final.iteration, 0)
        self.assertEqual(loop.scores(), ["5/5"])

    def test_a_dry_run_on_a_clean_pr_reports_success(self):
        """The dry-run break must not pre-empt the outcome check.

        `run_cycle` returns the entry decision for a dry run, and for a clean
        review that decision is `SUCCESS`. Printing `NOT FINISHED` here would tell
        a reader the PR needs another attempt when it does not.
        """
        for name, review in self.CLEAN.items():
            with self.subTest(review=name):
                loop, pi, push, wait = self.run_real_cycle(review, dry_run=True)
                self.assertEqual(loop.final.outcome, Outcome.SUCCESS)
                self.assertEqual(len(loop.iterations), 1)
                self.assertFalse(pi.called)
                self.assertFalse(push.called)
                self.assertFalse(wait.called)

    def test_a_dry_run_on_a_clean_pr_says_ready_not_unfinished(self):
        loop, *_ = self.run_real_cycle(self.CLEAN["five"], dry_run=True)
        with mock.patch.object(driver_loop.driver_log, "log") as logged:
            status = driver_loop.announce(loop)
        lines = [str(call.args[0]) for call in logged.call_args_list]
        self.assertTrue(status.is_ready)
        self.assertIn("READY FOR MERGE", lines[-1])
        self.assertFalse(any("NOT FINISHED" in line for line in lines))


class NumberingTest(unittest.TestCase):
    def test_the_first_cycle_is_attempt_one(self):
        script = Script([Outcome.SUCCESS])
        script.run()
        self.assertEqual(script.asked, [1])

    def test_each_continue_hands_over_the_next_number(self):
        script = Script([Outcome.CONTINUE] * 4 + [Outcome.SUCCESS])
        script.run()
        self.assertEqual(script.asked, [1, 2, 3, 4, 5])

    def test_a_resumed_loop_starts_where_it_was_told(self):
        script = Script([Outcome.CONTINUE, Outcome.SUCCESS])
        script.run(start_iteration=3)
        self.assertEqual(script.asked, [3, 4])

    def test_it_never_asks_for_an_attempt_past_the_limit(self):
        """A cycle that keeps saying CONTINUE must not buy a sixth attempt.

        The cycle's own guard refuses iteration 6, so asking would surface as a
        crash in the middle of an unattended run. The loop is the last place that
        number is written down, so it is where it must not be written.
        """
        script = Script([Outcome.CONTINUE] * 10)
        with self.assertRaises(driver_loop.LoopError) as caught:
            script.run()
        self.assertEqual(script.asked, [1, 2, 3, 4, 5])
        self.assertLessEqual(max(script.asked), MAX_ITERATIONS)
        self.assertIn(str(MAX_ITERATIONS), str(caught.exception))

    def test_continuing_on_the_last_attempt_is_reported_not_ignored(self):
        """CONTINUE at 5 means the rule and the limit disagree.

        `decide` cannot return CONTINUE once five attempts have been made, so this
        state is a bug rather than a slow run. Printing "another attempt is due"
        for an attempt that cannot happen would send the operator back to a loop
        that refuses to start.
        """
        script = Script([Outcome.CONTINUE] * 5)
        with self.assertRaises(driver_loop.LoopError) as caught:
            script.run()
        self.assertIn("disagree", str(caught.exception))


class WiringTest(unittest.TestCase):
    def test_the_cycle_gets_the_repository_and_branch(self):
        script = Script([Outcome.SUCCESS])
        script.run(branch="slice-4-live-test", pr=31)
        self.assertEqual(script.kwargs[0]["repo"], "owner/repo")
        self.assertEqual(script.kwargs[0]["branch"], "slice-4-live-test")
        self.assertEqual(script.kwargs[0]["pr"], 31)

    def test_the_wait_numbers_are_the_documented_ones(self):
        script = Script([Outcome.SUCCESS])
        script.run()
        self.assertEqual(script.kwargs[0]["timeout"], DEFAULT_TIMEOUT)
        self.assertEqual(script.kwargs[0]["interval"], DEFAULT_INTERVAL)

    def test_it_passes_no_baseline_and_no_review_to_wait_for(self):
        """The cycle already waits for the review written about its own push.

        A second wait between cycles would spend another timeout on a review that
        has already arrived. `run_cycle` returns only after reading it, so the
        next cycle's `read_before` is the newest review by construction and the
        loop passes nothing extra to make that happen.
        """
        script = Script([Outcome.CONTINUE, Outcome.SUCCESS])
        script.run()
        self.assertEqual(
            script.kwargs[0].keys() - {"iteration"},
            script.kwargs[1].keys() - {"iteration"},
        )

    def test_a_dry_run_does_not_try_five_times(self):
        """Nothing changes in a dry run, so a second pass would be identical."""
        script = Script([Outcome.CONTINUE] * 5)
        script.run(dry_run=True)
        self.assertEqual(script.asked, [1])
        self.assertTrue(script.kwargs[0]["dry_run"])


class PrintingTest(unittest.TestCase):
    def output(self, loop) -> list[str]:
        with mock.patch.object(driver_loop.driver_log, "log") as logged:
            driver_loop.announce(loop)
        return [str(call.args[0]) for call in logged.call_args_list]

    def test_the_status_is_printed_once(self):
        loop = driver_loop.run_loop(
            repo="owner/repo", cycle=Script([Outcome.CONTINUE, Outcome.SUCCESS])
        )
        lines = self.output(loop)
        self.assertEqual(len(lines), 1)
        self.assertIn("READY FOR MERGE", lines[0])

    def test_the_status_is_the_last_line(self):
        """A reader scrolling to the bottom must land on the verdict."""
        loop = driver_loop.run_loop(
            repo="owner/repo", cycle=Script([Outcome.CONTINUE, Outcome.SUCCESS])
        )
        lines = self.output(loop)
        self.assertIn("READY FOR MERGE", lines[-1])

    def test_a_failed_loop_does_not_print_ready(self):
        loop = driver_loop.run_loop(repo="owner/repo", cycle=Script([Outcome.HUMAN]))
        text = "\n".join(self.output(loop))
        self.assertIn("HUMAN REVIEW NEEDED", text)
        self.assertNotIn("READY FOR MERGE", text)

    def test_a_cycle_without_a_decision_is_an_error_not_a_verdict(self):
        """Refusing to guess is better than printing the wrong status."""
        empty = CycleResult(
            pr=PR, before=BEFORE_REVIEW, after=None, pi_ok=False, pushed=False, pi_ran=False
        )
        with self.assertRaises(driver_loop.LoopError):
            driver_loop.run_loop(repo="owner/repo", cycle=lambda **_kw: empty)


class StallTest(unittest.TestCase):
    def test_a_cycle_that_changed_nothing_ends_the_loop(self):
        """Pi ran, edited nothing, so there is no push and no review to wait for.

        Waiting would poll for 600s for a review of a commit that does not exist.
        """
        script = Script([Outcome.CONTINUE])

        def cycle(**kwargs):
            script.asked.append(kwargs["iteration"])
            raise NothingToPush("nothing to push")

        with self.assertRaises(driver_loop.LoopError) as caught:
            driver_loop.run_loop(repo="owner/repo", cycle=cycle)
        self.assertIn("changed nothing", str(caught.exception))
        self.assertEqual(script.asked, [1])

    def test_a_stall_is_not_reported_as_nothing_to_do(self):
        """Exit 2 means "no PR / no review / timed out", and this is none of them.

        The run spent an attempt on a real finding and got no diff, which a person
        has to look at. Reporting it as "nothing to do" would file it with the
        cases where running again later is the right answer.
        """

        def cycle(**_kwargs):
            raise NothingToPush("nothing to push")

        with mock.patch.object(driver_loop, "run_cycle", side_effect=cycle), \
             mock.patch.object(driver_loop.driver_log, "log"):
            code = driver_loop.main(["--pr", "31", "--repo", "owner/repo"])
        self.assertEqual(code, 1)


class MainTest(unittest.TestCase):
    def run_main(self, loop_result=None, error=None, argv=None):
        calls = {"count": 0}

        def fake_loop(**_kwargs):
            calls["count"] += 1
            if error is not None:
                raise error
            return loop_result

        with mock.patch.object(driver_loop, "run_loop", side_effect=fake_loop), \
             mock.patch.object(driver_loop.driver_log, "log") as logged, \
             mock.patch.object(driver_loop, "report") as reported:
            code = driver_loop.main(argv or ["--pr", "31", "--repo", "owner/repo"])
        return code, logged, reported, calls

    def test_a_successful_loop_exits_zero(self):
        loop = driver_loop.LoopResult(iterations=(result(Outcome.SUCCESS, 1),))
        code, _logged, reported, _ = self.run_main(loop)
        self.assertEqual(code, 0)
        reported.assert_called_once()

    def test_a_human_stop_still_exits_zero(self):
        """The exit code answers "did the tool work", not "is the PR good"."""
        loop = driver_loop.LoopResult(iterations=(result(Outcome.HUMAN, 5),))
        code, _, _, _ = self.run_main(loop)
        self.assertEqual(code, 0)

    def test_a_timeout_mid_loop_exits_two(self):
        code, _logged, _, calls = self.run_main(error=ReviewTimeout("no review"))
        self.assertEqual(code, 2)
        self.assertEqual(calls["count"], 1)

    def test_a_crash_mid_loop_still_logs_the_attempts_that_ran(self):
        """A crash on attempt 3 must not lose the record of 1 and 2.

        Each attempt is logged as it finishes rather than collected and printed
        at the end, so a run that dies halfway still explains what it did. The
        evidence a failed run is judged on is exactly those lines.
        """
        attempts = {"count": 0}

        def cycle(**_kwargs):
            attempts["count"] += 1
            if attempts["count"] == 3:
                raise PiError("pi exploded on attempt 3")
            return result(Outcome.CONTINUE, attempts["count"])

        with mock.patch.object(driver_loop, "run_cycle", side_effect=cycle), \
             mock.patch.object(driver_loop.driver_log, "log") as logged:
            code = driver_loop.main(["--pr", "31", "--repo", "owner/repo"])
        lines = [str(call.args[0]) for call in logged.call_args_list]
        self.assertEqual(code, 1)
        self.assertTrue(any("iteration 1/5:" in line for line in lines))
        self.assertTrue(any("iteration 2/5:" in line for line in lines))
        self.assertFalse(any("iteration 3/5:" in line for line in lines))
        self.assertFalse(any("READY FOR MERGE" in line for line in lines))

    def test_a_pi_failure_exits_one(self):
        code, _, _, _ = self.run_main(error=PiError("pi exploded"))
        self.assertEqual(code, 1)

    def test_the_loop_is_started_at_the_requested_iteration(self):
        loop = driver_loop.LoopResult(iterations=(result(Outcome.SUCCESS, 3),))
        seen = {}

        def fake_loop(**kwargs):
            seen.update(kwargs)
            return loop

        with mock.patch.object(driver_loop, "run_loop", side_effect=fake_loop), \
             mock.patch.object(driver_loop.driver_log, "log"):
            code = driver_loop.main(
                ["--pr", "31", "--iteration", "3", "--repo", "owner/repo"]
            )
        self.assertEqual(code, 0)
        self.assertEqual(seen["start_iteration"], 3)
        self.assertEqual(seen["pr"], 31)
        self.assertEqual(seen["repo"], "owner/repo")

    def test_a_dry_run_is_passed_through(self):
        loop = driver_loop.LoopResult(iterations=(result(Outcome.CONTINUE, 1),))
        seen = {}

        def fake_loop(**kwargs):
            seen.update(kwargs)
            return loop

        with mock.patch.object(driver_loop, "run_loop", side_effect=fake_loop), \
             mock.patch.object(driver_loop.driver_log, "log"):
            driver_loop.main(["--pr", "31", "--repo", "owner/repo", "--dry-run"])
        self.assertTrue(seen["dry_run"])

    def test_a_dry_run_says_nothing_was_attempted(self):
        """A dry run always stops on "another attempt is due".

        It changed nothing, so the verdict is honest and also misleading: read on
        its own it looks like a run that stopped one attempt short. One line
        before the status says no attempt was made.
        """
        loop = driver_loop.LoopResult(iterations=(result(Outcome.CONTINUE, 1),))
        with mock.patch.object(driver_loop, "run_loop", return_value=loop), \
             mock.patch.object(driver_loop.driver_log, "log") as logged:
            driver_loop.main(["--pr", "31", "--repo", "owner/repo", "--dry-run"])
        lines = [str(call.args[0]) for call in logged.call_args_list]
        self.assertIn("no attempt was made", lines[0])
        self.assertIn("NOT FINISHED", lines[-1])

    def test_a_dry_run_that_stops_on_continue_exits_zero(self):
        """A dry run that changed nothing is a run that completed, so exit 0.

        The PR is not ready and the headline says so. The exit code describes the
        run, not the PR: a dry run did what it was asked to do. Anything else
        would make "dry run a PR that needs work" look like a failure in a script
        that only checks the exit code.
        """
        loop = driver_loop.LoopResult(iterations=(result(Outcome.CONTINUE, 1),))
        with mock.patch.object(driver_loop, "run_loop", return_value=loop), \
             mock.patch.object(driver_loop.driver_log, "log") as logged:
            code = driver_loop.main(["--pr", "31", "--repo", "owner/repo", "--dry-run"])
        lines = [str(call.args[0]) for call in logged.call_args_list]
        self.assertEqual(code, 0)
        self.assertIn("no attempt was made", lines[0])
        self.assertIn("NOT FINISHED", lines[-1])

    def test_an_iteration_past_the_limit_is_refused_before_any_cycle_runs(self):
        """`--iteration 6` must not start a run whose first cycle would refuse it.

        The real `run_loop` is used here and only the cycle is replaced, because
        this is about the seam between the two: the limit is checked on the way
        in, so no attempt, no Pi run and no push can happen first.
        """
        script = Script([Outcome.SUCCESS])
        with mock.patch.object(driver_loop, "run_cycle", script), \
             mock.patch.object(driver_loop.driver_log, "log"):
            code = driver_loop.main(
                ["--pr", "31", "--repo", "owner/repo", "--iteration", "6"]
            )
        self.assertEqual(code, 1)
        self.assertEqual(script.asked, [])


if __name__ == "__main__":
    unittest.main()
