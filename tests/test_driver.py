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
