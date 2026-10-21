"""Tests for selecting the review record out of the JSONL sink (slice 1, T1.3).

The seam under test is ``latest_review(jsonl_path) -> dict``.

The file sink appends one JSON record per tool run, so the last line is not
necessarily a review. The renderer has to select by record type, not position.

Run with: python3 -m unittest discover -s tests -v
"""

import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

from render_contract_comment import ContractError, latest_review  # noqa: E402

REVIEW = {"score": 90, "key_issues_to_review": []}
OTHER = {"score": 10, "key_issues_to_review": []}


class LatestReviewTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self._tmp.name) / "review.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, *records):
        lines = [json.dumps(record) if isinstance(record, dict) else record for record in records]
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_reads_a_single_review(self):
        self.write({"type": "review", "payload": REVIEW})
        self.assertEqual(latest_review(self.path), REVIEW)

    def test_picks_the_last_review(self):
        self.write(
            {"type": "review", "payload": {"score": 50}},
            {"type": "review", "payload": REVIEW},
        )
        self.assertEqual(latest_review(self.path), REVIEW)

    def test_ignores_records_from_other_tools(self):
        """A later describe/improve record must not be mistaken for the review."""
        self.write(
            {"type": "review", "payload": REVIEW},
            {"type": "describe", "payload": {"title": "x"}},
            {"type": "improve", "payload": {"suggestions": []}},
        )
        self.assertEqual(latest_review(self.path), REVIEW)

    def test_skips_a_review_record_with_no_payload(self):
        self.write(
            {"type": "review", "payload": REVIEW},
            {"type": "review", "payload": {}},
        )
        self.assertEqual(latest_review(self.path), REVIEW)

    def test_missing_file_is_a_contract_error(self):
        with self.assertRaises(OSError):
            latest_review(pathlib.Path(self._tmp.name) / "absent.jsonl")

    def test_empty_file_is_a_contract_error(self):
        self.path.write_text("\n\n", encoding="utf-8")
        with self.assertRaises(ContractError):
            latest_review(self.path)

    def test_no_review_records_is_a_contract_error(self):
        self.write({"type": "describe", "payload": {"title": "x"}})
        with self.assertRaises(ContractError):
            latest_review(self.path)

    def test_only_empty_reviews_is_a_contract_error(self):
        self.write({"type": "review", "payload": {}})
        with self.assertRaises(ContractError):
            latest_review(self.path)

    def test_malformed_line_is_reported_with_its_position(self):
        self.write({"type": "review", "payload": REVIEW}, "not json")
        with self.assertRaises(ContractError) as caught:
            latest_review(self.path)
        self.assertIn("record 2", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
