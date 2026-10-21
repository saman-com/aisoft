"""Tests for how the comment body reaches the API (slice 1, T1.3).

The seam under test is ``_write_body(repo, pr, body, comment_id)``.

The body is model-derived text of unbounded length, so it must not travel as a
command-line argument: `gh api -f body=@...` treats a leading `@` as a file
reference, and a long body can exceed ARG_MAX. It is sent as a JSON payload on
stdin instead.

Run with: python3 -m unittest discover -s tests -v
"""

import json
import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import post_contract_comment as poster  # noqa: E402

BODY = "@everyone look at this\n\n## Confidence Score\n4/5\n"


class WriteBodyTest(unittest.TestCase):
    def call(self, comment_id):
        with mock.patch.object(poster, "_gh", return_value="") as gh:
            poster._write_body("owner/repo", 27, BODY, comment_id)
        return gh.call_args

    def payload(self, args):
        return json.loads(args.kwargs["stdin"])

    def test_creates_when_there_is_no_comment(self):
        args = self.call(None)
        argv = list(args.args)
        self.assertIn("POST", argv)
        self.assertIn("repos/owner/repo/issues/27/comments", argv)

    def test_updates_the_existing_comment(self):
        args = self.call(555)
        argv = list(args.args)
        self.assertIn("PATCH", argv)
        self.assertIn("repos/owner/repo/issues/comments/555", argv)

    def test_body_is_sent_on_stdin_as_json(self):
        args = self.call(None)
        self.assertEqual(self.payload(args), {"body": BODY})

    def test_body_is_never_a_command_line_argument(self):
        """A leading `@` or a huge body must not be reinterpreted by gh."""
        args = self.call(555)
        for arg in args.args:
            self.assertNotIn(BODY, arg)
            self.assertFalse(arg.startswith("body="), arg)
        self.assertIn("--input", args.args)

    def test_long_bodies_are_fine(self):
        long_body = "## Confidence Score\n1/5\n" + ("- issue " * 20000)
        with mock.patch.object(poster, "_gh", return_value="") as gh:
            poster._write_body("owner/repo", 27, long_body, None)
        self.assertEqual(json.loads(gh.call_args.kwargs["stdin"])["body"], long_body)


if __name__ == "__main__":
    unittest.main()
