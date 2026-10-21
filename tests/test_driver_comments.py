"""Tests for fetching and waiting on the contract comment.

Two things matter more than the happy path:

- The driver must not read a *stale* review. A push re-triggers the workflow, and
  the old comment stays on the PR until the new one replaces it. Waiting has to
  distinguish "the review I already saw" from "a review written after my push".
- Polling must stop. An unattended loop that waits forever is worse than one that
  reports a timeout, because nobody is watching either way.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from driver_comments import (  # noqa: E402
    CONTRACT_HEADER,
    ReviewTimeout,
    contract_comment,
    wait_for_contract_comment,
)

BODY = "## Confidence Score\n4/5\n\n## Issues\nNone\n\n## Agent Prompt\nNo changes.\n"


def comment(comment_id: int = 1, body: str = BODY, login: str = "github-actions[bot]"):
    return {
        "id": comment_id,
        "body": body,
        "user": {"login": login, "type": "Bot"},
        "updated_at": "2026-10-21T05:00:00Z",
    }


class ContractCommentTest(unittest.TestCase):
    def test_returns_the_contract_comment(self):
        found = contract_comment([comment(7)])
        self.assertIsNotNone(found)
        self.assertEqual(found.id, 7)

    def test_ignores_comments_that_are_not_contract_comments(self):
        self.assertIsNone(contract_comment([comment(1, body="looks good to me")]))

    def test_ignores_another_accounts_contract_comment(self):
        """Only the workflow's own comment is a review.

        A human pasting a contract-shaped comment, or another bot using the same
        headers, must not be read as the review: the loop acts on what it reads.
        """
        self.assertIsNone(contract_comment([comment(1, login="someone")]))

    def test_the_newest_comment_wins(self):
        found = contract_comment([comment(3), comment(9)])
        self.assertEqual(found.id, 9)

    def test_leading_whitespace_still_counts_as_the_header(self):
        found = contract_comment([comment(2, body="\n\n" + BODY)])
        self.assertEqual(found.id, 2)

    def test_no_comments_at_all_is_none(self):
        self.assertIsNone(contract_comment([]))

    def test_a_body_of_the_wrong_type_is_skipped(self):
        broken = {"id": 5, "body": None, "user": {"login": "github-actions[bot]"}}
        self.assertIsNone(contract_comment([broken]))


class WaitTest(unittest.TestCase):
    def test_returns_a_comment_that_already_matches(self):
        with mock.patch("driver_comments.list_comments", return_value=[comment(4)]):
            found = wait_for_contract_comment(
                "owner/repo", 27, newer_than=None, timeout=0, interval=0
            )
        self.assertEqual(found.id, 4)

    def test_keeps_polling_until_a_comment_appears(self):
        replies = [[], [], [comment(5)]]
        with mock.patch("driver_comments.list_comments", side_effect=replies):
            found = wait_for_contract_comment(
                "owner/repo", 27, newer_than=None, timeout=5, interval=0,
                sleep=lambda _seconds: None, clock=fake_clock(0, 1, 2),
            )
        self.assertEqual(found.id, 5)

    def test_a_stale_comment_does_not_end_the_wait(self):
        """The review seen before the push must not be read as the new one."""
        stale = comment(4)
        fresh = comment(6)
        replies = [[stale], [stale], [fresh]]
        with mock.patch("driver_comments.list_comments", side_effect=replies):
            found = wait_for_contract_comment(
                "owner/repo", 27, newer_than=4, timeout=5, interval=0,
                sleep=lambda _seconds: None, clock=fake_clock(0, 1, 2),
            )
        self.assertEqual(found.id, 6)

    def test_times_out_with_a_message_that_says_what_to_check(self):
        with mock.patch("driver_comments.list_comments", return_value=[]):
            with self.assertRaises(ReviewTimeout) as caught:
                wait_for_contract_comment(
                    "owner/repo", 27, newer_than=None, timeout=0, interval=0,
                    sleep=lambda _seconds: None, clock=fake_clock(0, 1),
                )
        message = str(caught.exception)
        self.assertIn("#27", message)
        self.assertIn("Actions", message)

    def test_a_timeout_mentions_the_stale_comment_it_ignored(self):
        """If an old review exists, saying so explains the wait, not just the end."""
        with mock.patch("driver_comments.list_comments", return_value=[comment(4)]):
            with self.assertRaises(ReviewTimeout) as caught:
                wait_for_contract_comment(
                    "owner/repo", 27, newer_than=4, timeout=0, interval=0,
                    sleep=lambda _seconds: None, clock=fake_clock(0, 1),
                )
        self.assertIn("newer than", str(caught.exception))

    def test_waits_zero_times_when_the_comment_already_exists(self):
        with mock.patch("driver_comments.list_comments", return_value=[comment(4)]) as listed:
            wait_for_contract_comment(
                "owner/repo", 27, newer_than=None, timeout=60, interval=15,
                sleep=mock.Mock(),
            )
        self.assertEqual(listed.call_count, 1)


def fake_clock(*values: float):
    """A monotonic clock that yields the given readings, then repeats the last."""
    readings = list(values) or [0.0]

    def clock() -> float:
        return readings.pop(0) if len(readings) > 1 else readings[0]

    return clock


if __name__ == "__main__":
    unittest.main()
