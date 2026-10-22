"""Tests for the start-to-finish path: no PR yet, all the way to a verdict.

T5.3 is the first time the whole product runs as one command. Everything it uses
is already tested on its own:

    driver_pr.ensure_pull_request   the branch's PR, created only when none is open
    driver_comments.wait_for_contract_comment   the review, content-based
    driver_loop.run_loop            the attempts, up to five
    driver_status.report            the one line a run ends on

What T5.3 adds is the *order*, and that is what these tests pin:

- The PR is ensured **before** the first cycle, and the first cycle is given the
  number that came back.
- A review is waited for **before** the first cycle, so the cycle reads a review
  instead of raising `NoReview` on a PR the workflow has not seen yet.
- The wait starts from the right baseline: nothing for a PR this run created,
  the existing comment for a reused one. A reused PR's comment is a review of an
  earlier iteration, and returning on it would hand the loop a stale verdict.
- Reuse is not a second create path. A branch with an open PR goes through the
  same `ensure_pull_request` call, which is the only place `gh pr create` lives.
- A run that could not create the PR, or never got a review, stops with the exit
  code the rest of the driver already uses instead of starting a loop.

The cycle is injected so a test can script outcomes; `ensure`, the wait and the
log are patched because they talk to gh.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import driver_loop  # noqa: E402
from driver_comments import Comment, ReviewTimeout  # noqa: E402
from driver_cycle import CycleResult  # noqa: E402
from driver_decision import Decision, Outcome  # noqa: E402
from driver_pr import GhFailed, PullRequest  # noqa: E402
from parse_contract_comment import Review  # noqa: E402

BRANCH = "slice-5-live-start"
NEW_PR = PullRequest(
    number=41,
    url="https://github.com/saman-com/aisoft/pull/41",
    state="OPEN",
    head_ref=BRANCH,
)
COMMITS = ["feat: add a clamp helper", "test: cover the helper"]
REVIEW = Review(score=5, issues=(), agent_prompt="No issues found.")
COMMENT = Comment(id=900, body="## Confidence Score\n5/5\n", author="github-actions[bot]")


def cycle_result(outcome: Outcome, iteration: int) -> CycleResult:
    return CycleResult(
        pr=NEW_PR,
        before=REVIEW,
        after=REVIEW,
        pi_ok=True,
        pushed=False,
        pi_ran=False,
        decision=Decision(outcome, f"iteration {iteration}: {outcome.value}", iteration),
    )


class Script:
    """A stand-in for `run_cycle`, so the start step is what is under test."""

    def __init__(self, outcomes: list[Outcome]):
        self.asked: list[int] = []
        self.kwargs: list[dict] = []
        self.outcomes = outcomes

    def __call__(self, **kwargs) -> CycleResult:
        self.asked.append(kwargs["iteration"])
        self.kwargs.append(kwargs)
        return cycle_result(self.outcomes.pop(0), kwargs["iteration"])


class StartTest(unittest.TestCase):
    def run_start(
        self,
        *,
        created: bool = True,
        outcomes: list[Outcome] | None = None,
        ensure_error: Exception | None = None,
        wait_error: Exception | None = None,
        existing: Comment | None = None,
        **kwargs,
    ):
        script = Script(outcomes or [Outcome.SUCCESS])
        ensure = mock.MagicMock(
            side_effect=ensure_error
            if ensure_error is not None
            else None,
            return_value=(NEW_PR, created),
        )
        wait = mock.MagicMock(
            side_effect=wait_error if wait_error is not None else None,
            return_value=COMMENT,
        )
        commits = mock.MagicMock(return_value=list(COMMITS))
        # The comment the PR already has, if any. A reused PR is looked at before
        # waiting so the wait can start from it.
        listing = mock.MagicMock(return_value=[] if existing is None else [existing])
        current = mock.MagicMock(return_value=existing)
        self.listing = listing
        self.current = current
        with mock.patch.object(driver_loop, "ensure_pull_request", ensure), \
             mock.patch.object(driver_loop, "commits_on_branch", commits), \
             mock.patch.object(driver_loop, "wait_for_contract_comment", wait), \
             mock.patch.object(driver_loop, "list_comments", listing), \
             mock.patch.object(driver_loop, "contract_comment", current), \
             mock.patch.object(driver_loop, "current_branch", return_value=BRANCH), \
             mock.patch.object(driver_loop.driver_log, "log") as logged:
            loop = driver_loop.run_loop(
                repo="owner/repo", branch=BRANCH, create=True, cycle=script, **kwargs
            )
        return loop, ensure, wait, commits, script, logged

    def lines(self, logged) -> list[str]:
        return [str(call.args[0]) for call in logged.call_args_list]

    def test_the_pr_is_ensured_before_the_first_cycle_and_its_number_is_used(self):
        loop, ensure, _wait, _commits, script, _logged = self.run_start()
        ensure.assert_called_once()
        self.assertEqual(script.asked, [1])
        self.assertEqual(script.kwargs[0]["pr"], NEW_PR.number)
        self.assertEqual(loop.pr.number, NEW_PR.number)

    def test_a_new_pr_is_created_from_the_branch_and_its_commits(self):
        _loop, ensure, _wait, commits, _script, _logged = self.run_start(created=True)
        kwargs = ensure.call_args.kwargs
        self.assertEqual(kwargs["branch"], BRANCH)
        self.assertEqual(kwargs["base"], "master")
        self.assertEqual(kwargs["commits"], COMMITS)
        commits.assert_called_once_with(BRANCH, base="master")

    def test_the_first_review_is_waited_for_before_any_attempt(self):
        """`run_cycle` raises `NoReview` on a PR the workflow has not seen yet.

        Waiting here is what makes an unattended start possible: the cycle's first
        read has something to read.
        """
        _loop, _ensure, wait, _commits, script, _logged = self.run_start()
        wait.assert_called_once()
        self.assertEqual(wait.call_args.args[:2], ("owner/repo", NEW_PR.number))
        self.assertEqual(script.asked, [1])

    def test_the_wait_uses_the_documented_numbers(self):
        _loop, _ensure, wait, _commits, _script, _logged = self.run_start()
        kwargs = wait.call_args.kwargs
        self.assertEqual(kwargs["timeout"], driver_loop.DEFAULT_TIMEOUT)
        self.assertEqual(kwargs["interval"], driver_loop.DEFAULT_INTERVAL)

    def test_a_new_pr_is_waited_for_without_a_baseline(self):
        """A PR opened a second ago has no comment, so the first one is the review."""
        _loop, _ensure, wait, _commits, _script, _logged = self.run_start(created=True)
        self.assertIsNone(wait.call_args.kwargs["baseline"])
        self.assertFalse(self.listing.called)

    def test_a_reused_pr_waits_from_the_review_it_already_has(self):
        """The comment on a reused PR describes an earlier iteration.

        Waiting with no baseline returns on that stale comment at once, and the
        first cycle then acts on a verdict from a previous round instead of the
        review of the newest push.
        """
        stale = Comment(id=700, body="## Confidence Score\n3/5\n", author="github-actions[bot]")
        _loop, _ensure, wait, _commits, _script, _logged = self.run_start(
            created=False, existing=stale
        )
        self.assertIs(wait.call_args.kwargs["baseline"], stale)
        self.listing.assert_called_once_with("owner/repo", NEW_PR.number)

    def test_a_reused_pr_with_no_review_yet_still_waits_from_scratch(self):
        """Reused is not the same as "already reviewed".

        A PR someone opened by hand has no contract comment, so there is nothing
        to be stale against and the first one to appear is the review to act on.
        """
        _loop, _ensure, wait, _commits, _script, _logged = self.run_start(
            created=False, existing=None
        )
        self.assertIsNone(wait.call_args.kwargs["baseline"])

    def test_an_existing_pr_is_reused_and_not_created_again(self):
        """One call, one answer: `ensure` is the only creation path there is.

        A branch that already has an open PR must go through the same lookup, so
        there is no second place where `gh pr create` could be reached.
        """
        loop, ensure, wait, _commits, script, logged = self.run_start(created=False)
        ensure.assert_called_once()
        wait.assert_called_once()
        self.assertEqual(script.kwargs[0]["pr"], NEW_PR.number)
        self.assertEqual(loop.pr.number, NEW_PR.number)
        self.assertIn("reused", " ".join(self.lines(logged)))
        self.assertFalse(hasattr(driver_loop, "create_pull_request"))

    def test_a_new_pr_says_created(self):
        _loop, _ensure, _wait, _commits, _script, logged = self.run_start(created=True)
        self.assertIn("created", " ".join(self.lines(logged)))

    def test_create_off_keeps_the_loop_where_it_started(self):
        """Without `create`, nothing is looked up and nothing is waited for."""
        script = Script([Outcome.SUCCESS])
        with mock.patch.object(driver_loop, "ensure_pull_request") as ensure, \
             mock.patch.object(driver_loop, "wait_for_contract_comment") as wait, \
             mock.patch.object(driver_loop.driver_log, "log"):
            driver_loop.run_loop(
                repo="owner/repo", pr=31, branch=BRANCH, cycle=script
            )
        self.assertFalse(ensure.called)
        self.assertFalse(wait.called)


class BranchTest(unittest.TestCase):
    def test_the_checked_out_branch_is_used_when_none_is_given(self):
        script = Script([Outcome.SUCCESS])
        with mock.patch.object(
            driver_loop, "ensure_pull_request", return_value=(NEW_PR, True)
        ) as ensure, \
             mock.patch.object(driver_loop, "commits_on_branch", return_value=[]), \
             mock.patch.object(driver_loop, "wait_for_contract_comment"), \
             mock.patch.object(driver_loop, "current_branch", return_value=BRANCH), \
             mock.patch.object(driver_loop.driver_log, "log"):
            driver_loop.run_loop(repo="owner/repo", create=True, cycle=script)
        self.assertEqual(ensure.call_args.kwargs["branch"], BRANCH)
        self.assertEqual(script.kwargs[0]["branch"], BRANCH)

    def test_a_branch_that_is_also_the_base_is_refused_before_gh(self):
        """`--base feature` with `--branch feature` is a PR into itself.

        Git would report no commits and gh would refuse the create, both with a
        message that does not say what the operator got wrong.
        """
        script = Script([Outcome.SUCCESS])
        with mock.patch.object(driver_loop, "ensure_pull_request") as ensure, \
             mock.patch.object(driver_loop, "commits_on_branch") as commits, \
             mock.patch.object(driver_loop, "wait_for_contract_comment") as wait, \
             mock.patch.object(driver_loop.driver_log, "log"):
            with self.assertRaises(driver_loop.LoopError) as caught:
                driver_loop.run_loop(
                    repo="owner/repo", branch=BRANCH, base=BRANCH,
                    create=True, cycle=script,
                )
        self.assertFalse(ensure.called)
        self.assertFalse(commits.called)
        self.assertFalse(wait.called)
        self.assertEqual(script.asked, [])
        self.assertIn(BRANCH, str(caught.exception))

    def test_a_number_for_a_different_pr_is_refused_before_any_attempt(self):
        """The loop commits to the checked-out branch, so it can only act on that PR."""
        script = Script([Outcome.SUCCESS])
        with mock.patch.object(
            driver_loop, "ensure_pull_request", return_value=(NEW_PR, False)
        ), \
             mock.patch.object(driver_loop, "commits_on_branch", return_value=[]), \
             mock.patch.object(driver_loop, "wait_for_contract_comment"), \
             mock.patch.object(driver_loop.driver_log, "log"):
            with self.assertRaises(driver_loop.LoopError) as caught:
                driver_loop.run_loop(
                    repo="owner/repo", branch=BRANCH, pr=99, create=True, cycle=script
                )
        self.assertEqual(script.asked, [])
        self.assertIn("99", str(caught.exception))


class FailureTest(unittest.TestCase):
    def test_a_create_failure_stops_the_run_and_is_exit_one(self):
        def ensure(**_kwargs):
            raise GhFailed("gh pr create failed: no commits between master and branch")

        with mock.patch.object(driver_loop, "ensure_pull_request", ensure), \
             mock.patch.object(driver_loop, "commits_on_branch", return_value=[]), \
             mock.patch.object(driver_loop, "wait_for_contract_comment") as wait, \
             mock.patch.object(driver_loop.driver_log, "log"), \
             mock.patch("sys.stderr"):
            code = driver_loop.main(
                ["--repo", "owner/repo", "--branch", BRANCH, "--create"]
            )
        self.assertEqual(code, 1)
        self.assertFalse(wait.called)

    def test_a_missing_first_review_is_nothing_to_do_and_exit_two(self):
        """A review that has not arrived yet keeps exit 2, like every other wait."""
        def wait(*_args, **_kwargs):
            raise ReviewTimeout("no new review on #41 within 600s")

        with mock.patch.object(
            driver_loop, "ensure_pull_request", return_value=(NEW_PR, True)
        ), \
             mock.patch.object(driver_loop, "commits_on_branch", return_value=[]), \
             mock.patch.object(driver_loop, "wait_for_contract_comment", wait), \
             mock.patch.object(driver_loop.driver_log, "log"), \
             mock.patch("sys.stderr"):
            code = driver_loop.main(
                ["--repo", "owner/repo", "--branch", BRANCH, "--create"]
            )
        self.assertEqual(code, 2)


class CliTest(unittest.TestCase):
    def run_main(self, argv):
        loop = driver_loop.LoopResult(
            iterations=(cycle_result(Outcome.SUCCESS, 1),)
        )
        with mock.patch.object(driver_loop, "run_loop", return_value=loop) as run, \
             mock.patch.object(driver_loop.driver_log, "log"), \
             mock.patch.object(driver_loop, "report"):
            code = driver_loop.main(argv)
        return code, run

    def test_create_is_passed_through(self):
        _code, run = self.run_main(
            ["--repo", "owner/repo", "--branch", BRANCH, "--create"]
        )
        self.assertTrue(run.call_args.kwargs["create"])

    def test_create_is_off_by_default(self):
        _code, run = self.run_main(["--repo", "owner/repo", "--pr", "31"])
        self.assertFalse(run.call_args.kwargs["create"])

    def test_the_base_branch_can_be_chosen(self):
        _code, run = self.run_main(
            ["--repo", "owner/repo", "--branch", BRANCH, "--create", "--base", "main"]
        )
        self.assertEqual(run.call_args.kwargs["base"], "main")


if __name__ == "__main__":
    unittest.main()
