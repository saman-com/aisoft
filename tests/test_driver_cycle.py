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
from driver_cycle import CycleResult, run_cycle  # noqa: E402
from driver_git import GitError, NothingToPush, PushResult  # noqa: E402
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

    def run(self, **kwargs):
        patchers = [
            mock.patch.object(driver_cycle, name, spy) for name, spy in self.spies.items()
        ]
        for patcher in patchers:
            patcher.start()
        try:
            return run_cycle(repo="owner/repo", iteration=1, **kwargs)
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
        for error in (
            NoPullRequest("no pr"),
            ReviewTimeout("no new review"),
            NothingToPush("nothing"),
        ):
            with self.subTest(error=type(error).__name__):
                with mock.patch.object(driver_cycle, "run_cycle", side_effect=error):
                    self.assertEqual(driver_cycle.main(["--pr", "31"]), 2)

    def test_exit_1_when_the_environment_fails(self):
        for error in (GhFailed("gh down"), PiError("pi down"), GitError("git down")):
            with self.subTest(error=type(error).__name__):
                with mock.patch.object(driver_cycle, "run_cycle", side_effect=error):
                    self.assertEqual(driver_cycle.main(["--pr", "31"]), 1)

    def test_exit_3_when_the_contract_is_broken(self):
        with mock.patch.object(driver_cycle, "run_cycle", side_effect=ParseError("bad")):
            self.assertEqual(driver_cycle.main(["--pr", "31"]), 3)

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
