"""Tests for locating the pull request that belongs to the current branch.

The driver runs unattended, so it has to tell "this branch has no PR yet" apart
from "gh is not working". The first is a normal, actionable stop; the second is
an environment problem, and treating it as "no PR" would make Slice 5 create a
duplicate PR on top of an existing one.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from driver_pr import (  # noqa: E402
    GhFailed,
    NoPullRequest,
    PullRequest,
    current_branch,
    find_pull_request,
)


def gh_result(stdout: str = "", returncode: int = 0, stderr: str = ""):
    return subprocess.CompletedProcess(
        args=["gh"], returncode=returncode, stdout=stdout, stderr=stderr
    )


# `gh pr list --json` returns an array, even when it holds a single PR.
GH_JSON = (
    '[{"number": 27, "url": "https://github.com/saman-com/aisoft/pull/27",'
    ' "state": "OPEN", "headRefName": "slice-1-pr-agent-review"}]'
)


class CurrentBranchTest(unittest.TestCase):
    def test_returns_the_branch_name(self):
        with mock.patch("driver_pr._run", return_value=gh_result("feature\n")):
            self.assertEqual(current_branch(), "feature")

    def test_a_detached_head_is_an_error(self):
        """Empty output means no branch, and no branch means no PR to find."""
        with mock.patch("driver_pr._run", return_value=gh_result("")):
            with self.assertRaises(GhFailed):
                current_branch()


class FindPullRequestTest(unittest.TestCase):
    def test_an_open_pr_is_returned(self):
        with mock.patch("driver_pr._run", return_value=gh_result(GH_JSON)):
            pr = find_pull_request("slice-1-pr-agent-review")
        self.assertEqual(pr.number, 27)
        self.assertEqual(pr.url, "https://github.com/saman-com/aisoft/pull/27")
        self.assertEqual(pr.head_ref, "slice-1-pr-agent-review")

    def test_an_empty_listing_means_no_pr(self):
        with mock.patch("driver_pr._run", return_value=gh_result("")):
            with self.assertRaises(NoPullRequest):
                find_pull_request("brand-new-branch")

    def test_the_error_names_the_branch_and_says_what_to_do(self):
        with mock.patch("driver_pr._run", return_value=gh_result("")):
            with self.assertRaises(NoPullRequest) as caught:
                find_pull_request("brand-new-branch")
        message = str(caught.exception)
        self.assertIn("brand-new-branch", message)
        self.assertIn("gh pr create", message)

    def test_an_empty_array_means_no_pr(self):
        """This is what real gh prints for a branch with no open PR."""
        with mock.patch("driver_pr._run", return_value=gh_result("[]\n")):
            with self.assertRaises(NoPullRequest):
                find_pull_request("brand-new-branch")

    def test_a_failing_gh_is_not_reported_as_no_pr(self):
        """Conflating the two would let Slice 5 create a duplicate PR."""
        with mock.patch(
            "driver_pr._run",
            return_value=gh_result(returncode=1, stderr="gh: not logged in"),
        ):
            with self.assertRaises(GhFailed) as caught:
                find_pull_request("feature")
        self.assertIn("not logged in", str(caught.exception))

    def test_unparseable_output_is_a_failure_not_an_absent_pr(self):
        with mock.patch("driver_pr._run", return_value=gh_result("not json")):
            with self.assertRaises(GhFailed):
                find_pull_request("feature")

    def test_a_closed_pr_is_not_treated_as_the_current_pr(self):
        """A closed PR cannot receive reviews, so the branch needs a new one."""
        closed = GH_JSON.replace('"OPEN"', '"CLOSED"')
        with mock.patch("driver_pr._run", return_value=gh_result(closed)):
            with self.assertRaises(NoPullRequest):
                find_pull_request("feature")


class PullRequestModelTest(unittest.TestCase):
    def test_display_name_includes_the_number(self):
        pr = PullRequest(number=27, url="u", state="OPEN", head_ref="b")
        self.assertEqual(pr.display, "#27")


if __name__ == "__main__":
    unittest.main()
