"""Tests for the read-only driver entry point.

The driver ties Slice 2 together: find the PR, fetch the review comment, parse it,
and print what it found. Slice 3 will act on the result, so the parts that matter
here are the boundaries that later slices depend on - which exit code means which
outcome, and that a failure never looks like a usable review.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import driver  # noqa: E402
from driver_comments import Comment, ReviewTimeout  # noqa: E402
from driver_pr import GhFailed, NoPullRequest, PullRequest  # noqa: E402
from parse_contract_comment import parse_comment  # noqa: E402

PR = PullRequest(
    number=27,
    url="https://github.com/saman-com/aisoft/pull/27",
    state="OPEN",
    head_ref="slice-2-local-parser",
)

BODY = """## Confidence Score
4/5

## Issues
- scripts/driver.py:42-50 — Missing timeout: The poll loop never times out.

## Agent Prompt
Add a timeout to the polling loop in scripts/driver.py.
"""


class ReadOnceTest(unittest.TestCase):
    def test_returns_the_parsed_review(self):
        with mock.patch.object(driver, "list_comments", return_value=[]), \
             mock.patch.object(driver, "contract_comment", return_value=Comment(9, BODY, "github-actions[bot]")):
            review, comment = driver.read_review("owner/repo", 27)
        self.assertEqual(review.score, 4)
        self.assertEqual(comment.id, 9)

    def test_an_unparseable_comment_raises(self):
        broken = Comment(9, "## Confidence Score\n4/5\n", "github-actions[bot]")
        with mock.patch.object(driver, "list_comments", return_value=[]), \
             mock.patch.object(driver, "contract_comment", return_value=broken):
            with self.assertRaises(driver.ParseError):
                driver.read_review("owner/repo", 27)

    def test_no_comment_yet_is_its_own_outcome(self):
        with mock.patch.object(driver, "list_comments", return_value=[]), \
             mock.patch.object(driver, "contract_comment", return_value=None):
            with self.assertRaises(driver.NoReview):
                driver.read_review("owner/repo", 27)


class MainTest(unittest.TestCase):
    def run_main(self, argv, **patches):
        with mock.patch.dict(patches):
            return driver.main(argv)

    def test_prints_the_score_and_the_prompt_size(self):
        with mock.patch.object(driver, "list_comments", return_value=[]), \
             mock.patch.object(driver, "contract_comment", return_value=Comment(9, BODY, "bot")), \
             mock.patch.object(driver, "resolve", return_value=PR), \
             mock.patch("builtins.print") as printed:
            code = driver.main(["--pr", "27", "--repo", "owner/repo"])
        self.assertEqual(code, 0)
        out = "\n".join(str(call.args[0]) for call in printed.call_args_list if call.args)
        self.assertIn("4/5", out)
        self.assertIn("1 issue", out)

    def test_a_clean_review_prints_the_ready_status(self):
        """T4.4: the read-only view uses the same status as the cycle.

        Before T4.4 it printed `decide`'s sentence alone, which said the same
        thing in a different shape from the cycle's. One renderer, so a run can
        be grepped for one phrase whatever produced it.
        """
        clean = BODY.replace("4/5", "5/5").replace(
            "- scripts/driver.py:42-50 — Missing timeout: The poll loop never times out.",
            "None",
        )
        with mock.patch.object(driver, "resolve", return_value=PR), \
             mock.patch.object(driver, "list_comments", return_value=[]), \
             mock.patch.object(driver, "contract_comment", return_value=Comment(9, clean, "bot")), \
             mock.patch("builtins.print") as printed:
            code = driver.main(["--pr", "27", "--repo", "owner/repo"])
        self.assertEqual(code, 0)
        out = "\n".join(str(call.args[0]) for call in printed.call_args_list if call.args)
        self.assertIn("READY FOR MERGE (5/5)", out)
        # `--pr` skips the lookup, so there is no real URL to print. The status
        # must fall back to the number rather than printing gh's placeholder.
        self.assertIn("#27", out)
        self.assertNotIn("given on the command line", out.split("READY FOR MERGE")[1])

    def test_a_still_open_review_is_not_printed_as_ready(self):
        """A CONTINUE is not a final status, so no verdict line is printed."""
        with mock.patch.object(driver, "resolve", return_value=PR), \
             mock.patch.object(driver, "list_comments", return_value=[]), \
             mock.patch.object(driver, "contract_comment", return_value=Comment(9, BODY, "bot")), \
             mock.patch("builtins.print") as printed:
            driver.main(["--pr", "27", "--repo", "owner/repo", "--iteration", "1"])
        out = "\n".join(str(call.args[0]) for call in printed.call_args_list if call.args)
        self.assertNotIn("READY FOR MERGE", out)

    def test_no_pr_exits_2(self):
        with mock.patch.object(driver, "resolve", side_effect=NoPullRequest("none")):
            self.assertEqual(driver.main(["--branch", "x", "--repo", "o/r"]), 2)

    def test_gh_failure_exits_1(self):
        with mock.patch.object(driver, "resolve", side_effect=GhFailed("boom")):
            self.assertEqual(driver.main(["--branch", "x", "--repo", "o/r"]), 1)

    def test_missing_comment_exits_2(self):
        with mock.patch.object(driver, "resolve", return_value=PR), \
             mock.patch.object(driver, "list_comments", return_value=[]), \
             mock.patch.object(driver, "contract_comment", return_value=None):
            self.assertEqual(driver.main(["--pr", "27", "--repo", "owner/repo"]), 2)

    def test_parse_failure_exits_3(self):
        broken = Comment(9, "not a contract comment", "bot")
        with mock.patch.object(driver, "resolve", return_value=PR), \
             mock.patch.object(driver, "list_comments", return_value=[]), \
             mock.patch.object(driver, "contract_comment", return_value=broken):
            self.assertEqual(driver.main(["--pr", "27", "--repo", "owner/repo"]), 3)

    def test_a_timeout_exits_2_with_the_reason_printed(self):
        with mock.patch.object(driver, "resolve", return_value=PR), \
             mock.patch.object(driver, "wait_for_contract_comment", side_effect=ReviewTimeout("no new review")), \
             mock.patch("builtins.print") as printed:
            code = driver.main(["--pr", "27", "--repo", "owner/repo", "--wait"])
        self.assertEqual(code, 2)
        self.assertIn("no new review", str(printed.call_args_list))

    def test_a_parse_failure_names_the_pr_it_resolved_from_the_branch(self):
        """`--branch` users must still see which PR is broken, not "PR #0"."""
        broken = Comment(9, "not a contract comment", "bot")
        with mock.patch.object(driver, "resolve", return_value=PR), \
             mock.patch.object(driver, "list_comments", return_value=[]), \
             mock.patch.object(driver, "contract_comment", return_value=broken), \
             mock.patch("sys.stderr") as stderr:
            code = driver.main(["--branch", "slice-2-local-parser", "--repo", "o/r"])
        self.assertEqual(code, 3)
        self.assertIn("PR #27", str(stderr.write.call_args_list))

    def test_the_comment_just_read_becomes_the_baseline_for_a_wait(self):
        """T3.3/T3.4: an id-only baseline cannot see an in-place rewrite.

        `--wait` without `--newer-than` must wait for the review to be *replaced*,
        so the baseline is whatever was read before the push. Passing only an id
        would let a rewritten comment look unchanged and time out.
        """
        before = Comment(9, BODY, "bot")
        fresh = Comment(9, BODY.replace("4/5", "5/5"), "bot")
        with mock.patch.object(driver, "resolve", return_value=PR), \
             mock.patch.object(driver, "list_comments", return_value=[
                 {"id": 9, "body": BODY, "user": {"login": "github-actions[bot]"}}
             ]), \
             mock.patch.object(driver, "contract_comment", return_value=before), \
             mock.patch.object(
                 driver, "parse_comment",
                 side_effect=lambda body, **_kw: parse_comment(fresh.body),
             ), \
             mock.patch.object(driver, "wait_for_contract_comment", return_value=fresh) as waited:
            code = driver.main([
                "--pr", "27", "--repo", "owner/repo", "--wait",
                "--baseline-body", BODY,
            ])
        self.assertEqual(code, 0)
        self.assertEqual(waited.call_args.kwargs.get("baseline"), BODY)

    def test_wait_is_optional_and_off_by_default(self):
        """Reading the current review must not block for ten minutes."""
        with mock.patch.object(driver, "resolve", return_value=PR), \
             mock.patch.object(driver, "list_comments", return_value=[]), \
             mock.patch.object(driver, "contract_comment", return_value=Comment(9, BODY, "bot")), \
             mock.patch.object(driver, "wait_for_contract_comment") as waited:
            driver.main(["--pr", "27", "--repo", "owner/repo"])
        waited.assert_not_called()


if __name__ == "__main__":
    unittest.main()
