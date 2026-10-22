"""Tests for opening the pull request that the loop then reviews.

Two failures matter more than the happy path:

- **A duplicate PR.** Creating a second PR for a branch splits the review: two
  workflow runs post two contract comments, and the loop reads whichever it finds
  first. There is no cheap undo, so creation refuses when a PR already exists.
- **A silent failure.** `gh pr create` can fail for reasons the operator can fix
  (branch not pushed, no commits between the branches, no permission). The reason
  has to reach stderr unchanged rather than becoming a guess about what happened.

Reuse of an existing PR rather than refusing is T5.2, so these tests pin the
refusal, not a merger of the two behaviours.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import driver_pr  # noqa: E402
from driver_pr import (  # noqa: E402
    COMMIT_LIMIT,
    TITLE_LIMIT,
    GhFailed,
    NoPullRequest,
    PullRequest,
    PullRequestExists,
    build_body,
    build_title,
    create_pull_request,
    ensure_pull_request,
)


def gh_result(stdout: str = "", returncode: int = 0, stderr: str = ""):
    return subprocess.CompletedProcess(
        args=["gh"], returncode=returncode, stdout=stdout, stderr=stderr
    )


GH_JSON = (
    '[{"number": 27, "url": "https://github.com/saman-com/aisoft/pull/27",'
    ' "state": "OPEN", "headRefName": "slice-5-create-pr"}]'
)

NEW_PR_URL = "https://github.com/saman-com/aisoft/pull/36"


class TitleTest(unittest.TestCase):
    """The title is what a reviewer reads first, so it must never be empty."""

    def test_one_commit_gives_its_subject(self):
        self.assertEqual(
            build_title(["feat: create the PR when the branch has none"], "b"),
            "feat: create the PR when the branch has none",
        )

    def test_several_commits_are_summarised_not_joined(self):
        """A joined list is a paragraph. The body carries the detail."""
        self.assertEqual(
            build_title(["add creation", "add tests", "fix lint"], "b"),
            "add creation (+2 more)",
        )

    def test_a_branch_with_no_commits_falls_back_to_its_name(self):
        """`gh pr create` rejects an empty title, so there is always one."""
        self.assertEqual(build_title([], "slice-5-create-pr"), "slice-5-create-pr")

    def test_blank_subjects_are_ignored(self):
        self.assertEqual(build_title(["  ", "", "real subject"], "b"), "real subject")

    def test_a_long_title_is_cut_at_a_word_boundary(self):
        """GitHub truncates silently past its limit, so stay under it ourselves."""
        subject = "word " * 60
        title = build_title([subject], "b")
        self.assertLessEqual(len(title), TITLE_LIMIT + len("..."))
        self.assertTrue(title.endswith("..."))
        self.assertNotIn("wor...", title)

    def test_a_single_very_long_word_is_still_cut(self):
        """No space to break on is not a reason to exceed the limit."""
        title = build_title(["x" * 500], "b")
        self.assertLessEqual(len(title), TITLE_LIMIT)


class BodyTest(unittest.TestCase):
    def test_the_commit_list_is_generated_from_git(self):
        body = build_body(branch="b", base="master", commits=["one", "two"])
        self.assertIn("Opened from `b` into `master`.", body)
        self.assertIn("Commits on this branch (2)", body)
        self.assertIn("- one", body)
        self.assertIn("- two", body)

    def test_an_empty_branch_says_so_instead_of_listing_nothing(self):
        """A heading over an empty list reads like a bug in the PR."""
        body = build_body(branch="b", base="master", commits=[])
        self.assertIn("No commits", body)
        self.assertNotIn("Commits on this branch", body)

    def test_a_supplied_body_is_used_unchanged(self):
        """The caller knows more than the commits do, so do not edit its words."""
        body = build_body(
            branch="b", base="master", commits=["one"], body="What changed, in prose."
        )
        self.assertEqual(body, "What changed, in prose.")

    def test_an_empty_supplied_body_falls_back_to_the_generated_one(self):
        body = build_body(branch="b", base="master", commits=["one"], body="")
        self.assertIn("- one", body)

    def test_a_long_commit_subject_is_cut_in_the_body(self):
        body = build_body(branch="b", base="master", commits=["y" * 400])
        line = next(l for l in body.splitlines() if l.startswith("- "))
        self.assertLessEqual(len(line), COMMIT_LIMIT + 2)


class CreateTest(unittest.TestCase):
    def create(self, *, runs, **kwargs):
        """Call `create_pull_request` with `gh` scripted by `runs`.

        `runs` maps a substring of the argv to the result for that call, so a
        test says what each command answers without depending on call order.
        """
        calls = []

        def fake_run(args):
            calls.append(args)
            for needle, result in runs.items():
                if all(part in args for part in needle.split()):
                    return result
            raise AssertionError(f"unexpected command: {args}")

        with mock.patch.object(driver_pr, "_run", side_effect=fake_run):
            created = create_pull_request(**kwargs)
        return created, calls

    def test_it_creates_a_pr_when_the_branch_has_none(self):
        created, calls = self.create(
            runs={
                "pr list": gh_result("[]"),
                "pr create": gh_result(NEW_PR_URL + "\n"),
            },
            branch="slice-5-create-pr",
            commits=["feat: create the PR"],
        )
        self.assertEqual(created.number, 36)
        self.assertEqual(created.url, NEW_PR_URL)
        self.assertEqual(created.head_ref, "slice-5-create-pr")
        create = next(c for c in calls if "create" in c)
        self.assertIn("--base", create)
        self.assertIn("master", create)
        self.assertIn("--head", create)
        self.assertIn("slice-5-create-pr", create)
        self.assertIn("--title", create)
        self.assertIn("--body", create)

    def test_an_existing_pr_is_refused_not_duplicated(self):
        """The whole point: a second PR splits the review and cannot be undone."""
        found = PullRequest(
            number=27, url="https://github.com/saman-com/aisoft/pull/27",
            state="OPEN", head_ref="slice-5-create-pr",
        )
        with mock.patch.object(driver_pr, "_run", return_value=gh_result(GH_JSON)):
            with self.assertRaises(PullRequestExists) as raised:
                create_pull_request(branch="slice-5-create-pr")
        self.assertEqual(raised.exception.pull_request.number, 27)
        self.assertIn("#27", str(raised.exception))
        self.assertEqual(found.number, 27)

    def test_a_caller_supplied_pr_skips_the_lookup_entirely(self):
        """A caller that already looked must not pay for a second `gh pr list`."""
        found = PullRequest(number=27, url="u", state="OPEN", head_ref="b")
        with mock.patch.object(driver_pr, "_run") as run:
            with self.assertRaises(PullRequestExists):
                create_pull_request(branch="b", existing=found)
        self.assertFalse(run.called)

    def test_no_create_call_happens_when_a_pr_exists(self):
        calls = []
        with mock.patch.object(driver_pr, "_run", side_effect=lambda a: (calls.append(a), gh_result(GH_JSON))[1]):
            with self.assertRaises(PullRequestExists):
                create_pull_request(branch="slice-5-create-pr")
        self.assertFalse(any("create" in call for call in calls))

    def test_a_create_failure_raises_with_ghs_own_message(self):
        """The operator can fix these, and only if gh's reason survives."""
        with mock.patch.object(
            driver_pr, "_run", side_effect=[
                gh_result("[]"),
                gh_result("", returncode=1, stderr="pull request create failed: no commits between master and b"),
            ]
        ):
            with self.assertRaises(GhFailed) as raised:
                create_pull_request(branch="b")
        self.assertIn("no commits between master and b", str(raised.exception))

    def test_a_create_failure_is_never_reported_as_success(self):
        with mock.patch.object(
            driver_pr, "_run", side_effect=[
                gh_result("[]"),
                gh_result("", returncode=1, stderr="boom"),
            ]
        ):
            with self.assertRaises(GhFailed):
                create_pull_request(branch="b")

    def test_an_unreadable_listing_stops_before_creating(self):
        """'I could not check' must not become 'there was nothing there'."""
        with mock.patch.object(
            driver_pr, "_run",
            return_value=gh_result("", returncode=1, stderr="not authenticated"),
        ) as run:
            with self.assertRaises(GhFailed):
                create_pull_request(branch="b")
        self.assertFalse(any("create" in str(c.args) for c in run.mock_calls))

    def test_a_created_pr_whose_url_is_unreadable_falls_back_to_the_lookup(self):
        """The PR exists either way, so a lookup beats inventing a number."""
        with mock.patch.object(
            driver_pr, "_run", side_effect=[
                gh_result("[]"),
                gh_result("something unexpected\n", stderr=""),
                gh_result(GH_JSON),
            ]
        ):
            created = create_pull_request(branch="slice-5-create-pr")
        self.assertEqual(created.number, 27)

    def test_the_base_branch_is_configurable(self):
        _, calls = self.create(
            runs={"pr list": gh_result("[]"), "pr create": gh_result(NEW_PR_URL)},
            branch="b",
            base="develop",
        )
        create = next(c for c in calls if "create" in c)
        self.assertEqual(create[create.index("--base") + 1], "develop")

    def test_a_supplied_title_and_body_reach_gh(self):
        _, calls = self.create(
            runs={"pr list": gh_result("[]"), "pr create": gh_result(NEW_PR_URL)},
            branch="b",
            title="Slice 5 – PR creation",
            body="What changed and why.",
        )
        create = next(c for c in calls if "create" in c)
        self.assertEqual(create[create.index("--title") + 1], "Slice 5 – PR creation")
        self.assertEqual(create[create.index("--body") + 1], "What changed and why.")


class EnsureTest(unittest.TestCase):
    """"Ensure a PR exists": the call a loop makes, which does not care who opened it.

    The failure this prevents is a second open PR on one branch. Two workflow runs
    then post two contract comments and two loops fix the same code while reading
    different reviews, and there is no cheap undo.
    """

    def ensure(self, *, listed, create_result=None, **kwargs):
        """Call `ensure_pull_request` with gh's listing and create scripted."""
        calls = []

        def fake_run(args):
            calls.append(args)
            if "list" in args:
                # One call answers both questions, so the state is `all`.
                if "all" not in args:
                    raise AssertionError(f"expected --state all, got {args}")
                return gh_result(json.dumps(listed)) if listed else gh_result("[]")
            if "create" in args:
                return create_result if create_result is not None else gh_result(NEW_PR_URL + "\n")
            raise AssertionError(f"unexpected command: {args}")

        with mock.patch.object(driver_pr, "_run", side_effect=fake_run):
            pr, created = ensure_pull_request(**kwargs)
        return pr, created, calls

    def test_an_open_pr_is_reused_not_created(self):
        pr, created, calls = self.ensure(
            listed=[{"number": 36, "url": "https://example.test/pull/36",
                     "state": "OPEN", "headRefName": "b"}],
            branch="b",
        )
        self.assertEqual(pr.number, 36)
        self.assertFalse(created)
        self.assertFalse(any("create" in c for c in calls))

    def test_one_list_call_answers_both_questions(self):
        """Asking twice leaves a moment in which the workflow could post."""
        _pr, _created, calls = self.ensure(listed=[], branch="b")
        self.assertEqual(len([c for c in calls if "list" in c]), 1)

    def test_no_pr_at_all_creates_one(self):
        pr, created, calls = self.ensure(listed=[], branch="b", commits=["one"])
        self.assertEqual(pr.number, 36)
        self.assertTrue(created)
        self.assertTrue(any("create" in c for c in calls))

    def test_a_merged_pr_does_not_block_a_new_one(self):
        """A merged PR then new commits is the normal second round."""
        pr, created, calls = self.ensure(
            listed=[{"number": 30, "url": "https://example.test/pull/30",
                     "state": "MERGED", "headRefName": "b"}],
            branch="b", commits=["more work"],
        )
        self.assertEqual(pr.number, 36)
        self.assertTrue(created)
        self.assertTrue(any("create" in c for c in calls))

    def test_a_closed_pr_does_not_block_a_new_one(self):
        """Closing a PR is not a veto on the branch."""
        pr, created, _calls = self.ensure(
            listed=[{"number": 30, "url": "https://example.test/pull/30",
                     "state": "CLOSED", "headRefName": "b"}],
            branch="b", commits=["retry"],
        )
        self.assertEqual(pr.number, 36)
        self.assertTrue(created)

    def test_an_open_pr_wins_over_an_older_merged_one(self):
        """Otherwise the loop would read reviews of a branch that already landed."""
        pr, created, calls = self.ensure(
            listed=[
                {"number": 30, "url": "https://example.test/pull/30",
                 "state": "MERGED", "headRefName": "b"},
                {"number": 36, "url": "https://example.test/pull/36",
                 "state": "OPEN", "headRefName": "b"},
            ],
            branch="b",
        )
        self.assertEqual(pr.number, 36)
        self.assertFalse(created)
        self.assertFalse(any("create" in c for c in calls))

    def test_a_reused_pr_keeps_its_title_and_body(self):
        """Rewriting a description someone may have written by hand is not ours."""
        pr, _created, calls = self.ensure(
            listed=[{"number": 36, "url": "https://example.test/pull/36",
                     "state": "OPEN", "headRefName": "b"}],
            branch="b", title="a title the caller suggested", commits=["one"],
        )
        self.assertEqual(pr.number, 36)
        self.assertFalse(any("edit" in str(c) or "create" in c for c in calls))

    def test_a_list_failure_is_never_a_create(self):
        """"I could not check" must not become "there was nothing there"."""
        with mock.patch.object(driver_pr, "_run",
                              return_value=gh_result("", 1, "not authenticated")) as run:
            with self.assertRaises(GhFailed):
                ensure_pull_request(branch="b")
        self.assertFalse(any("create" in str(c) for c in run.mock_calls))

    def test_ensure_refuses_the_default_branch_before_calling_gh(self):
        for name in ("master", "main"):
            with self.subTest(branch=name):
                with mock.patch.object(driver_pr, "_run") as run:
                    with self.assertRaises(GhFailed):
                        ensure_pull_request(branch=name)
                self.assertFalse(run.called)

    def test_two_calls_in_a_row_leave_one_pr(self):
        """The idempotency claim, stated as a sequence rather than a single call.

        The second call sees the PR the first one created, which is exactly what
        a caller retrying a failed run does.
        """
        listed: list[dict] = []

        def fake_run(args):
            if "list" in args:
                return gh_result(json.dumps(listed))
            listed.append({"number": 36, "url": NEW_PR_URL,
                           "state": "OPEN", "headRefName": "b"})
            return gh_result(NEW_PR_URL + "\n")

        with mock.patch.object(driver_pr, "_run", side_effect=fake_run):
            first, first_created = ensure_pull_request(branch="b", commits=["one"])
            second, second_created = ensure_pull_request(branch="b", commits=["one"])
        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertEqual(first.number, second.number)
        self.assertEqual(len(listed), 1)


class DefaultBranchTest(unittest.TestCase):
    def test_opening_a_pr_from_master_is_refused(self):
        """The base cannot also be the head, and gh would reject it less clearly."""
        for name in ("master", "main"):
            with self.subTest(branch=name):
                with mock.patch.object(driver_pr, "_run") as run:
                    with self.assertRaises(GhFailed):
                        create_pull_request(branch=name)
                self.assertFalse(run.called)


class CliTest(unittest.TestCase):
    def test_create_reports_the_new_pr_and_exits_zero(self):
        with mock.patch.object(driver_pr, "current_branch", return_value="b"), \
             mock.patch.object(driver_pr, "commits_on_branch", return_value=["one"]), \
             mock.patch.object(driver_pr, "list_pull_requests", return_value=[]), \
             mock.patch.object(driver_pr, "_run", return_value=gh_result(NEW_PR_URL)), \
             mock.patch("sys.stdout") as out, \
             mock.patch("sys.stderr"):
            code = driver_pr.main(["--create", "--branch", "b"])
        self.assertEqual(code, 0)
        printed = "".join(c.args[0] for c in out.write.call_args_list)
        self.assertIn(NEW_PR_URL, printed)

    def test_an_existing_pr_is_exit_zero_and_says_reused(self):
        """Asking for a PR and finding one open is success, not a failure.

        The found PR is printed the way a lookup prints it, and stderr says which
        path was taken, so a caller that wants to know whether a PR was opened
        can read it without comparing PR counts before and after.
        """
        found = PullRequest(number=27, url="https://example.test/pull/27",
                            state="OPEN", head_ref="slice-5-create-pr")
        with mock.patch.object(driver_pr, "current_branch", return_value="slice-5-create-pr"), \
             mock.patch.object(driver_pr, "commits_on_branch", return_value=[]), \
             mock.patch.object(driver_pr, "list_pull_requests", return_value=[found]), \
             mock.patch.object(driver_pr, "_run") as run, \
             mock.patch("sys.stdout") as out, \
             mock.patch("sys.stderr") as err:
            code = driver_pr.main(["--create", "--branch", "slice-5-create-pr"])
        self.assertEqual(code, 0)
        self.assertFalse(run.called)
        printed = "".join(c.args[0] for c in out.write.call_args_list)
        self.assertIn("https://example.test/pull/27", printed)
        said = "".join(c.args[0] for c in err.write.call_args_list)
        self.assertIn("reused", said)

    def test_a_created_pr_whose_url_gh_did_not_print_still_reports_it(self):
        """The fallback path end to end: create prints nothing useful, look it up."""
        found = PullRequest(number=36, url=NEW_PR_URL, state="OPEN", head_ref="b")
        with mock.patch.object(driver_pr, "current_branch", return_value="b"), \
             mock.patch.object(driver_pr, "commits_on_branch", return_value=["one"]), \
             mock.patch.object(driver_pr, "list_pull_requests", return_value=[]), \
             mock.patch.object(driver_pr, "find_pull_request", return_value=found), \
             mock.patch.object(driver_pr, "_run", return_value=gh_result("no url\n")), \
             mock.patch("sys.stdout") as out, \
             mock.patch("sys.stderr"):
            code = driver_pr.main(["--create", "--branch", "b"])
        self.assertEqual(code, 0)
        printed = "".join(c.args[0] for c in out.write.call_args_list)
        self.assertIn(NEW_PR_URL, printed)

    def test_a_lookup_without_create_and_no_pr_exits_two(self):
        with mock.patch.object(driver_pr, "find_pull_request", side_effect=NoPullRequest("none")), \
             mock.patch("sys.stderr"):
            code = driver_pr.main(["--branch", "b"])
        self.assertEqual(code, 2)

    def test_a_create_failure_exits_one(self):
        with mock.patch.object(driver_pr, "current_branch", return_value="b"), \
             mock.patch.object(driver_pr, "commits_on_branch", return_value=[]), \
             mock.patch.object(driver_pr, "list_pull_requests", return_value=[]), \
             mock.patch.object(driver_pr, "_run", return_value=gh_result("", 1, "no commits between")), \
             mock.patch("sys.stderr"):
            code = driver_pr.main(["--create", "--branch", "b"])
        self.assertEqual(code, 1)

    def test_a_list_failure_with_create_exits_one_not_two(self):
        """The whole point of the distinction: a broken gh is never "no PR"."""
        with mock.patch.object(driver_pr, "current_branch", return_value="b"), \
             mock.patch.object(driver_pr, "commits_on_branch", return_value=[]), \
             mock.patch.object(driver_pr, "list_pull_requests",
                               side_effect=GhFailed("not authenticated")), \
             mock.patch("sys.stderr") as err:
            code = driver_pr.main(["--create", "--branch", "b"])
        self.assertEqual(code, 1)
        said = "".join(c.args[0] for c in err.write.call_args_list)
        self.assertIn("not authenticated", said)


class NoPullRequestTest(unittest.TestCase):
    def test_an_empty_list_is_no_pr_not_a_failure(self):
        """The distinction the caller depends on: empty list means go create one."""
        with mock.patch.object(driver_pr, "_run", return_value=gh_result("[]")):
            with self.assertRaises(NoPullRequest):
                driver_pr.find_pull_request("b")


if __name__ == "__main__":
    unittest.main()
