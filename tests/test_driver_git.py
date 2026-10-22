"""Tests for staging, committing and pushing Pi's changes.

T3.2 looks simple and is not, because the thing being committed was written by an
agent. The cases that matter are the ones where "it worked" would be a lie:

- Pi changed nothing. Committing an empty change would re-trigger the review, get
  the same score, and burn an iteration doing nothing. That has to be a distinct
  outcome, not a success.
- Pi switched branch or left a detached HEAD. Pushing then lands the fix on the
  wrong branch, or nowhere.
- The commit or push failed (missing git identity, rejected push). Reporting that
  as success tells the loop to wait for a review that will never arrive.
- Someone reaches for `--force`. A force-push rewrites a branch a human may have
  based work on, and a rebase is never what the loop needs.

The command sequence is pinned exactly by the fixtures below, so adding a git call
means updating the test that covers it.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import driver_git  # noqa: E402
from driver_git import (  # noqa: E402
    DEFAULT_BRANCHES,
    GitError,
    NothingToPush,
    build_message,
    commits_on_branch,
)

BRANCH = "slice-3-single-fix-cycle"
MESSAGE = "fix: address PR-Agent feedback (iteration 1)"


def result(stdout: str = "", returncode: int = 0, stderr: str = ""):
    completed = mock.MagicMock()
    completed.stdout = stdout
    completed.stderr = stderr
    completed.returncode = returncode
    return completed


class Fixture:
    """The git commands one run is allowed to make, keyed by argv.

    A command that is not listed raises KeyError, so the test fails loudly if the
    implementation starts calling git in a way no test describes.
    """

    def __init__(self, *, branch=BRANCH, status="", head="newsha", remote="oldsha",
                 post_push=None, commit_ok=True, push_ok=True, message=None,
                 tracking_missing=False, ls_remote=None, ls_remote_ok=True):
        self.calls: dict[tuple, mock.MagicMock] = {
            ("rev-parse", "--abbrev-ref", "HEAD"): result(branch + "\n"),
            ("status", "--porcelain"): result(status),
            ("add", "-A"): result(),
            ("commit", "-m", message or build_message(iteration=1)): (
                result() if commit_ok else result(stderr="Author identity unknown", returncode=128)
            ),
            ("rev-parse", "HEAD"): result(head + "\n"),
            ("rev-parse", "--verify", f"refs/remotes/origin/{branch}"): (
                result(remote + "\n") if remote else result(returncode=1)
            ),
            ("push", "origin", f"HEAD:refs/heads/{branch}"): (
                result() if push_ok else result(stderr="! [rejected] (non-fast-forward)", returncode=1)
            ),
            ("rev-parse", f"refs/remotes/origin/{branch}"): (
                result(returncode=1, stderr="Needed a single revision")
                if tracking_missing
                else result((post_push or head) + "\n")
            ),
        }
        self.calls[("ls-remote", "origin", f"refs/heads/{branch}")] = (
            result(f"{ls_remote if ls_remote is not None else remote}\trefs/heads/{branch}\n")
            if ls_remote_ok
            else result(returncode=128, stderr="could not read from remote")
        )
        self.seen: list[list[str]] = []

    def _git(self, args, **_kwargs):
        self.seen.append(list(args))
        return self.calls[tuple(args)]

    def run(self, **kwargs):
        with mock.patch.object(driver_git, "_git", side_effect=self._git):
            return driver_git.commit_and_push(iteration=1, **kwargs)

    def pushed(self) -> list[list[str]]:
        return [args for args in self.seen if args and args[0] == "push"]


class MessageTest(unittest.TestCase):
    def test_names_the_iteration_and_the_source(self):
        message = build_message(iteration=1)
        self.assertEqual(message.splitlines()[0], MESSAGE)

    def test_includes_the_score_when_known(self):
        self.assertIn("4/5", build_message(iteration=2, score=4))

    def test_omits_the_score_when_unknown(self):
        self.assertNotIn("/5", build_message(iteration=1))

    def test_mentions_the_issue_count(self):
        self.assertIn("2 issues", build_message(iteration=3, score=3, issues=2))

    def test_a_single_issue_is_singular(self):
        self.assertIn("1 issue", build_message(iteration=1, issues=1))
        self.assertNotIn("1 issues", build_message(iteration=1, issues=1))

    def test_the_subject_line_stays_short(self):
        """A long subject is truncated in every git UI."""
        subject = build_message(iteration=4, score=2, issues=7).splitlines()[0]
        self.assertLessEqual(len(subject), 72)


class GuardTest(unittest.TestCase):
    def test_refuses_the_default_branch(self):
        """The loop fixes a feature branch; changing master is a human's decision."""
        for branch in DEFAULT_BRANCHES:
            with self.subTest(branch=branch):
                with self.assertRaises(GitError) as caught:
                    Fixture(branch=branch).run()
                self.assertIn("default branch", str(caught.exception))

    def test_refuses_a_detached_head(self):
        with self.assertRaises(GitError) as caught:
            Fixture(branch="HEAD").run()
        self.assertIn("not on a branch", str(caught.exception))

    def test_refuses_when_pi_switched_branch(self):
        """Pi is an agent with a shell; it can run `git checkout`."""
        with self.assertRaises(GitError) as caught:
            Fixture(branch="some-other-branch").run(expected_branch=BRANCH)
        message = str(caught.exception)
        self.assertIn("some-other-branch", message)
        self.assertIn(BRANCH, message)

    def test_a_guard_failure_happens_before_anything_is_committed(self):
        fixture = Fixture(branch="master")
        with self.assertRaises(GitError):
            fixture.run()
        self.assertEqual(fixture.seen, [["rev-parse", "--abbrev-ref", "HEAD"]])


class NothingToPushTest(unittest.TestCase):
    def test_clean_tree_and_nothing_unpushed_is_not_success(self):
        with self.assertRaises(NothingToPush) as caught:
            Fixture(status="", head="abc123", remote="abc123").run()
        self.assertIn("no changes", str(caught.exception))

    def test_pi_committing_its_own_work_still_gets_pushed(self):
        """A clean tree does not mean nothing to do: Pi may have committed itself."""
        pushed = Fixture(status="", head="newsha", remote="oldsha").run()
        self.assertEqual(pushed.commit, "newsha")
        self.assertFalse(pushed.made_commit)

    def test_a_branch_with_no_remote_copy_is_pushed(self):
        pushed = Fixture(status=" M a.py\n", head="newsha", remote="").run()
        self.assertEqual(pushed.branch, BRANCH)

    def test_a_stale_tracking_ref_does_not_hide_an_unpushed_commit(self):
        """HEAD matching the local ref says nothing about where the remote is.

        A tracking ref updated by a fetch of a different commit can match HEAD
        while the remote is behind; calling that NothingToPush would skip a real
        push of Pi's work.
        """
        fixture = Fixture(status="", head="newsha", remote="newsha", ls_remote="oldsha")
        pushed = fixture.run()
        self.assertEqual(pushed.commit, "newsha")
        self.assertFalse(pushed.made_commit)

    def test_a_clean_tree_is_still_pushed_when_the_remote_is_unreadable(self):
        """An unanswerable remote must not turn into a skipped push."""
        fixture = Fixture(status="", head="newsha", remote="newsha", ls_remote_ok=False)
        pushed = fixture.run()
        self.assertEqual(pushed.commit, "newsha")
        self.assertEqual(fixture.pushed(), [["push", "origin", f"HEAD:refs/heads/{BRANCH}"]])


class CommitTest(unittest.TestCase):
    def test_stages_everything_then_commits(self):
        fixture = Fixture(status=" M scripts/driver.py\n", head="sha1", remote="sha0")
        committed = fixture.run()
        self.assertTrue(committed.made_commit)
        self.assertEqual(committed.commit, "sha1")
        self.assertIn(["add", "-A"], fixture.seen)

    def test_a_clean_tree_is_not_committed(self):
        fixture = Fixture(status="", head="newsha", remote="oldsha")
        fixture.run()
        self.assertNotIn(["add", "-A"], fixture.seen)

    def test_a_failed_commit_raises_with_gits_own_message(self):
        with self.assertRaises(GitError) as caught:
            Fixture(status=" M a.py\n", commit_ok=False).run()
        self.assertIn("Author identity unknown", str(caught.exception))
        self.assertIn("git config", str(caught.exception))

    def test_nothing_is_pushed_when_the_commit_failed(self):
        fixture = Fixture(status=" M a.py\n", commit_ok=False)
        with self.assertRaises(GitError):
            fixture.run()
        self.assertEqual(fixture.pushed(), [])


class PushTest(unittest.TestCase):
    def test_push_names_the_branch_explicitly(self):
        fixture = Fixture(status=" M a.py\n")
        fixture.run()
        self.assertEqual(fixture.pushed(), [["push", "origin", f"HEAD:refs/heads/{BRANCH}"]])

    def test_push_never_contains_a_force_flag(self):
        fixture = Fixture(status=" M a.py\n")
        fixture.run()
        flat = " ".join(" ".join(args) for args in fixture.seen)
        for forbidden in ("--force", "-f ", "+refs"):
            self.assertNotIn(forbidden, flat)

    def test_a_failed_push_raises(self):
        with self.assertRaises(GitError) as caught:
            Fixture(status=" M a.py\n", push_ok=False).run()
        self.assertIn("non-fast-forward", str(caught.exception))

    def test_a_failed_push_names_the_commit_that_stayed_local(self):
        """The commit is recoverable only if the operator knows it exists."""
        with self.assertRaises(GitError) as caught:
            Fixture(status=" M a.py\n", head="deadbeefcafe", push_ok=False).run()
        self.assertIn("deadbeef", str(caught.exception))
        self.assertIn("not pushed", str(caught.exception))

    def test_a_failed_push_of_an_existing_commit_has_no_stray_note(self):
        with self.assertRaises(GitError) as caught:
            Fixture(status="", head="newsha", remote="oldsha", push_ok=False).run()
        self.assertNotIn("not pushed", str(caught.exception))

    def test_the_push_is_verified_against_the_remote_ref(self):
        """A push that reports success but did not land must not look like success."""
        fixture = Fixture(
            status=" M a.py\n", head="newsha", post_push="stale", ls_remote="stale"
        )
        with self.assertRaises(GitError) as caught:
            fixture.run()
        self.assertIn(f"origin/{BRANCH}", str(caught.exception))

    def test_a_missing_tracking_ref_is_checked_against_the_remote(self):
        """A single-branch clone never creates the ref for a newly pushed branch.

        Pushing that branch still lands, so aborting on the absent local ref would
        fail a push that worked - verified against a real single-branch clone.
        """
        fixture = Fixture(status=" M a.py\n", head="newsha", tracking_missing=True,
                          ls_remote="newsha")
        pushed = fixture.run()
        self.assertEqual(pushed.commit, "newsha")

    def test_a_missing_tracking_ref_does_not_hide_a_failed_push(self):
        fixture = Fixture(status=" M a.py\n", head="newsha", tracking_missing=True,
                          ls_remote="oldsha")
        with self.assertRaises(GitError):
            fixture.run()

    def test_an_unreachable_remote_aborts_rather_than_assuming_success(self):
        fixture = Fixture(status=" M a.py\n", head="newsha", tracking_missing=True,
                          ls_remote_ok=False)
        with self.assertRaises(GitError):
            fixture.run()

    def test_the_remote_is_not_asked_when_the_tracking_ref_matches(self):
        """The common case must not pay for a network round trip."""
        fixture = Fixture(status=" M a.py\n", head="newsha", post_push="newsha")
        fixture.run()
        self.assertEqual(
            [args for args in fixture.seen if args and args[0] == "ls-remote"], []
        )

    def test_success_reports_the_branch_and_commit(self):
        pushed = Fixture(status=" M a.py\n", head="newsha").run()
        self.assertEqual(pushed.branch, BRANCH)
        self.assertEqual(pushed.remote, "origin")


class MainTest(unittest.TestCase):
    def test_exit_codes_match_the_other_drivers(self):
        with mock.patch.object(driver_git, "commit_and_push", return_value=driver_git.PushResult(
            branch=BRANCH, commit="abcdef1234", made_commit=True
        )):
            self.assertEqual(driver_git.main(["--iteration", "1"]), 0)
        with mock.patch.object(driver_git, "commit_and_push", side_effect=GitError("boom")):
            self.assertEqual(driver_git.main(["--iteration", "1"]), 1)
        with mock.patch.object(driver_git, "commit_and_push", side_effect=NothingToPush("none")):
            self.assertEqual(driver_git.main(["--iteration", "1"]), 2)

    def test_nothing_to_push_is_exit_2_not_a_failure(self):
        """Distinct from exit 1: the loop must not treat a no-op as an error."""
        with mock.patch.object(driver_git, "commit_and_push", side_effect=NothingToPush("none")):
            self.assertEqual(driver_git.main(["--iteration", "2"]), 2)

    def test_iteration_is_required(self):
        with self.assertRaises(SystemExit):
            driver_git.main([])


class CommitsOnBranchTest(unittest.TestCase):
    """The commit list a pull request body is built from.

    A PR body that lists the branch's work is only useful if the list is the work
    the base branch does not have. `<remote>/<branch>..<branch>` is empty by
    construction, so the range has to be against the base.
    """

    def run_log(self, *results):
        seen = []

        def fake_git(args, **_kwargs):
            seen.append(list(args))
            return results[len(seen) - 1]

        # A failed range is reported on stderr, which these cases trigger on
        # purpose; keep it out of the test output.
        with mock.patch.object(driver_git, "_git", side_effect=fake_git), \
             mock.patch("sys.stderr"):
            subjects = commits_on_branch(BRANCH)
        return subjects, seen

    def test_the_range_is_against_the_base_branch(self):
        _, seen = self.run_log(result("one\n"))
        self.assertEqual(seen[0], ["log", "--reverse", "--format=%s", "origin/master.." + BRANCH])

    def test_the_base_branch_is_configurable(self):
        with mock.patch.object(driver_git, "_git", return_value=result("one\n")) as git:
            commits_on_branch(BRANCH, base="develop")
        self.assertIn("origin/develop.." + BRANCH, git.call_args.args[0])

    def test_the_remote_is_configurable(self):
        with mock.patch.object(driver_git, "_git", return_value=result("one\n")) as git:
            commits_on_branch(BRANCH, remote="upstream")
        self.assertIn("upstream/master.." + BRANCH, git.call_args.args[0])

    def test_subjects_are_returned_oldest_first(self):
        """A reader follows the branch from where it left the base."""
        subjects, _ = self.run_log(result("first\nsecond\nthird\n"))
        self.assertEqual(subjects, ["first", "second", "third"])

    def test_blank_lines_are_dropped(self):
        subjects, _ = self.run_log(result("one\n\n  \ntwo\n"))
        self.assertEqual(subjects, ["one", "two"])

    def test_a_missing_base_ref_is_not_replaced_by_the_branch_history(self):
        """The fallback would list commits the base may already have.

        The body claims these are the commits the base does not have, so guessing
        with the branch's own history makes the description contradict the diff.
        An empty list is the honest answer, and git is not asked a second
        question whose answer would be about a different range.
        """
        subjects, seen = self.run_log(result(returncode=128, stderr="unknown revision"))
        self.assertEqual(subjects, [])
        self.assertEqual(len(seen), 1)

    def test_a_missing_base_ref_says_so_instead_of_going_quiet(self):
        """An empty list is also what a branch with no work ahead looks like.

        Without a warning the two are indistinguishable, and the operator reads an
        empty commit list as "there is nothing to describe" rather than "the base
        was never fetched".
        """
        with mock.patch.object(driver_git, "_git",
                               return_value=result(returncode=128, stderr="unknown revision")), \
             mock.patch("sys.stderr") as err:
            commits_on_branch(BRANCH)
        said = "".join(c.args[0] for c in err.write.call_args_list)
        self.assertIn("unknown revision", said)
        self.assertIn("git fetch", said)
        self.assertIn("origin/master", said)

    def test_an_unreadable_history_returns_nothing_rather_than_raising(self):
        """Failing to list commits must not stop a PR from being opened."""
        subjects, _ = self.run_log(result(returncode=128))
        self.assertEqual(subjects, [])


class LastCommitTimeTest(unittest.TestCase):
    """Whether a review is already current depends on when the newest push landed."""

    def test_the_pushed_commit_time_is_returned(self):
        with mock.patch.object(driver_git, "_git", return_value=result("1700000000\n")) as git:
            self.assertEqual(driver_git.last_commit_time(BRANCH), 1_700_000_000.0)
        self.assertEqual(
            git.call_args.args[0],
            ["log", "-1", "--format=%ct", "refs/remotes/origin/" + BRANCH],
        )

    def test_a_missing_tracking_ref_falls_back_to_the_local_branch(self):
        """`actions/checkout` makes single-branch clones without those refs."""
        seen = []

        def fake_git(args, **_kwargs):
            seen.append(list(args))
            return result("1700000000\n") if len(seen) == 2 else result(returncode=128)

        with mock.patch.object(driver_git, "_git", side_effect=fake_git):
            self.assertEqual(driver_git.last_commit_time(BRANCH), 1_700_000_000.0)
        self.assertEqual(seen[1], ["log", "-1", "--format=%ct", BRANCH])

    def test_an_unreadable_history_is_unknown_not_old(self):
        """A zero would make any comment look current; None keeps the wait."""
        with mock.patch.object(driver_git, "_git", return_value=result(returncode=128)):
            self.assertIsNone(driver_git.last_commit_time(BRANCH))

    def test_unparseable_output_is_unknown(self):
        with mock.patch.object(driver_git, "_git", return_value=result("yesterday\n")):
            self.assertIsNone(driver_git.last_commit_time(BRANCH))


class GitHelperTest(unittest.TestCase):
    def test_git_is_never_run_through_a_shell(self):
        with mock.patch.object(driver_git.subprocess, "run", return_value=result("x")) as run:
            driver_git._git(["status", "--porcelain"])
        self.assertFalse(run.call_args.kwargs.get("shell", False))

    def test_a_missing_git_binary_is_a_clear_error(self):
        with mock.patch.object(driver_git.subprocess, "run", side_effect=FileNotFoundError("no git")):
            with self.assertRaises(GitError) as caught:
                driver_git._git(["status"])
        self.assertIn("git", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
