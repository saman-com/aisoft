"""Tests for picking the comment to update (slice 1, T1.3).

The seam under test is ``pick_contract_comment(comments, author) -> int | None``.

A PR that is pushed to several times must still carry exactly one contract
comment. ``gh pr comment`` appends a new one every run, so the poster looks for
the existing comment first and edits it instead. Finding it by "starts with the
first contract header" rather than by a hidden marker keeps the posted body
byte-identical to CONTRACT.md, and makes the poster's rule the same one the
Slice 2 parser uses.

Run with: python3 -m unittest discover -s tests -v
"""

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

from post_contract_comment import pick_contract_comment  # noqa: E402

BOT = "github-actions[bot]"


def comment(comment_id, body, author=BOT):
    return {"id": comment_id, "body": body, "user": {"login": author}}


class PickContractCommentTest(unittest.TestCase):
    def test_no_comments_yet(self):
        self.assertIsNone(pick_contract_comment([], BOT))

    def test_picks_the_contract_comment(self):
        comments = [
            comment(1, "Thanks for the PR!"),
            comment(2, "## Confidence Score\n3/5\n\n## Issues\nNone"),
        ]
        self.assertEqual(pick_contract_comment(comments, BOT), 2)

    def test_ignores_comments_from_other_authors(self):
        """A human quoting the format must not be edited."""
        comments = [comment(7, "## Confidence Score\n5/5", author="some-reviewer")]
        self.assertIsNone(pick_contract_comment(comments, BOT))

    def test_updates_the_latest_when_several_exist(self):
        """Older runs already posted duplicates; converge on the newest."""
        comments = [
            comment(2, "## Confidence Score\n2/5"),
            comment(5, "## Confidence Score\n4/5"),
        ]
        self.assertEqual(pick_contract_comment(comments, BOT), 5)

    def test_body_may_have_leading_whitespace(self):
        comments = [comment(3, "\n## Confidence Score\n4/5")]
        self.assertEqual(pick_contract_comment(comments, BOT), 3)

    def test_bot_login_variants_are_matched(self):
        """The workflow token posts as ``github-actions`` with or without [bot]."""
        comments = [comment(4, "## Confidence Score\n4/5", author="github-actions")]
        self.assertEqual(pick_contract_comment(comments, "github-actions[bot]"), 4)

    def test_malformed_entries_are_skipped(self):
        comments = [{"id": 9}, comment(10, "## Confidence Score\n1/5")]
        self.assertEqual(pick_contract_comment(comments, BOT), 10)


if __name__ == "__main__":
    unittest.main()
