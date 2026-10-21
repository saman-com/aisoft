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
    Comment,
    DEFAULT_INTERVAL,
    DEFAULT_TIMEOUT,
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


def comment_object(raw: dict) -> Comment:
    """The dataclass a caller would hold after reading a review."""
    return Comment(
        id=raw["id"], body=raw["body"], author=raw["user"]["login"],
        updated_at=raw.get("updated_at", ""),
    )


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

    def test_a_human_account_named_like_the_bot_is_not_the_review(self):
        """Only the `[bot]` suffix makes it the workflow's own comment."""
        self.assertIsNone(contract_comment([comment(1, login="github-actions")]))

    def test_the_bot_suffix_is_ignored_when_both_sides_carry_it(self):
        found = contract_comment([comment(2)], author="github-actions[bot]")
        self.assertEqual(found.id, 2)

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

    def test_a_timeout_mentions_the_stale_comment_it_ignored(self):  # noqa: D401
        """If an old review exists, saying so explains the wait, not just the end."""
        with mock.patch("driver_comments.list_comments", return_value=[comment(4)]):
            with self.assertRaises(ReviewTimeout) as caught:
                wait_for_contract_comment(
                    "owner/repo", 27, newer_than=4, timeout=0, interval=0,
                    sleep=lambda _seconds: None, clock=fake_clock(0, 1),
                )
        message = str(caught.exception)
        self.assertIn("unchanged", message)
        self.assertIn("replacing", message)

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


class RewrittenCommentTest(unittest.TestCase):
    """The poster edits one comment in place, so its id never changes.

    Slice 1 settled on a single contract comment per PR, updated with PATCH, so
    after the first iteration the id is fixed. Telling the new review from the old
    one therefore cannot rely on the id alone: the body changes, and so does
    `updated_at`.
    """

    def test_the_edited_comment_counts_as_the_new_review(self):
        stale = comment(4, body=BODY)
        edited = comment(4, body=BODY.replace("4/5", "5/5"))
        edited["updated_at"] = "2026-10-21T05:10:00Z"
        replies = [[stale], [edited]]
        with mock.patch("driver_comments.list_comments", side_effect=replies):
            found = wait_for_contract_comment(
                "owner/repo", 27, baseline=comment_object(stale), timeout=5, interval=0,
                sleep=lambda _seconds: None, clock=fake_clock(0, 1),
            )
        self.assertEqual(found.id, 4)
        self.assertIn("5/5", found.body)

    def test_an_unchanged_comment_does_not_end_the_wait(self):
        """Same id, same body: nothing has been rewritten, so keep waiting."""
        same = comment(4)
        with mock.patch("driver_comments.list_comments", return_value=[same]):
            with self.assertRaises(ReviewTimeout):
                wait_for_contract_comment(
                    "owner/repo", 27, newer_than=4, timeout=0, interval=0,
                    sleep=lambda _seconds: None, clock=fake_clock(0, 1),
                )

    def test_an_edit_is_detected_even_when_the_updated_at_is_the_same_second(self):
        """`updated_at` has one-second resolution; the body is the reliable signal."""
        stale = comment(4, body=BODY)
        edited = comment(4, body=BODY.replace("4/5", "5/5"))
        replies = [[stale], [edited]]
        with mock.patch("driver_comments.list_comments", side_effect=replies):
            found = wait_for_contract_comment(
                "owner/repo", 27, baseline=comment_object(stale), timeout=5, interval=0,
                sleep=lambda _seconds: None, clock=fake_clock(0, 1),
            )
        self.assertIn("5/5", found.body)

    def test_an_id_only_baseline_cannot_see_an_edit(self):
        """The documented limitation, so callers know to pass the whole comment.

        Slice 1 edits one comment in place, so an id-only baseline sees a rewrite
        as "unchanged" and waits out its timeout. T3.4 therefore passes the
        `Comment` it read before pushing.
        """
        stale = comment(4, body=BODY)
        edited = comment(4, body=BODY.replace("4/5", "5/5"))
        with mock.patch("driver_comments.list_comments", side_effect=[[stale], [edited]]):
            with self.assertRaises(ReviewTimeout):
                wait_for_contract_comment(
                    "owner/repo", 27, newer_than=4, timeout=0, interval=0,
                    sleep=lambda _seconds: None, clock=fake_clock(0, 1),
                )

    def test_an_empty_updated_at_is_unknown_not_a_change(self):
        """`updated_at=""` means the caller did not know it, not "it changed".

        A `Comment` built without `updated_at` (the dataclass default) made
        `is_fresh` compare `"" != "2026-..."` and report an untouched comment as a
        new review, which would end the wait on the review it was replacing.
        """
        stale = comment(4, body=BODY)
        with mock.patch("driver_comments.list_comments", return_value=[stale]):
            with self.assertRaises(ReviewTimeout):
                wait_for_contract_comment(
                    "owner/repo", 27,
                    baseline=Comment(id=4, body=BODY, author="github-actions[bot]"),
                    timeout=0, interval=0,
                    sleep=lambda _seconds: None, clock=fake_clock(0, 1),
                )

    def test_a_body_carried_through_a_shell_is_still_the_same_comment(self):
        """`$(cat before.md)` drops the trailing newline, and CRLF may differ."""
        stale = comment(4, body=BODY)
        carried = BODY.rstrip("\n").replace("\n", "\r\n")
        replies = [[stale], [stale]]
        with mock.patch("driver_comments.list_comments", side_effect=replies):
            with self.assertRaises(ReviewTimeout):
                wait_for_contract_comment(
                    "owner/repo", 27, baseline=carried, timeout=0, interval=0,
                    sleep=lambda _seconds: None, clock=fake_clock(0, 1),
                )

    def test_a_real_change_is_still_detected_with_the_baseline_stripped(self):
        stale = comment(4, body=BODY)
        edited = comment(4, body=BODY.replace("4/5", "5/5"))
        with mock.patch("driver_comments.list_comments", side_effect=[[stale], [edited]]):
            found = wait_for_contract_comment(
                "owner/repo", 27, baseline=BODY.rstrip("\n"), timeout=5, interval=0,
                sleep=lambda _seconds: None, clock=fake_clock(0, 1),
            )
        self.assertIn("5/5", found.body)

    def test_a_comment_with_an_older_id_but_a_new_body_is_not_new(self):
        """A stale page must not resurrect a previous iteration's review."""
        with mock.patch("driver_comments.list_comments", return_value=[comment(2, BODY.replace("4/5", "5/5"))]):
            with self.assertRaises(ReviewTimeout):
                wait_for_contract_comment(
                    "owner/repo", 27, newer_than=4, timeout=0, interval=0,
                    sleep=lambda _seconds: None, clock=fake_clock(0, 1),
                )


class DocumentedNumbersTest(unittest.TestCase):
    """Slice 4 must not invent its own timeout and interval."""

    def test_the_default_timeout_is_ten_minutes(self):
        self.assertEqual(DEFAULT_TIMEOUT, 600.0)

    def test_the_default_interval_is_fifteen_seconds(self):
        self.assertEqual(DEFAULT_INTERVAL, 15.0)

    def test_agent_md_states_both_numbers(self):
        text = (Path(__file__).resolve().parents[1] / "AGENT.md").read_text()
        self.assertIn("600", text)
        self.assertIn("15", text)
        self.assertIn("DEFAULT_TIMEOUT", text)

    def test_the_interval_is_short_enough_to_finish_inside_the_timeout(self):
        self.assertLess(DEFAULT_INTERVAL, DEFAULT_TIMEOUT / 4)


class BaselineTest(unittest.TestCase):
    def test_a_first_read_needs_no_baseline(self):
        with mock.patch("driver_comments.list_comments", return_value=[comment(4)]):
            found = wait_for_contract_comment(
                "owner/repo", 27, newer_than=None, timeout=0, interval=0
            )
        self.assertEqual(found.id, 4)

    def test_the_baseline_is_the_comment_just_read_when_none_was_given(self):
        """T3.4: read the old review, push, then wait for it to be replaced.

        Passing only the id would be wrong here, because the id survives the
        rewrite. The body read before the push is the baseline.
        """
        stale = comment(4, body=BODY)
        edited = comment(4, body=BODY.replace("4/5", "5/5"))
        replies = [[stale], [edited]]
        with mock.patch("driver_comments.list_comments", side_effect=replies):
            found = wait_for_contract_comment(
                "owner/repo", 27, baseline=comment_object(stale), timeout=5, interval=0,
                sleep=lambda _seconds: None, clock=fake_clock(0, 1),
            )
        self.assertIn("5/5", found.body)



if __name__ == "__main__":
    unittest.main()
