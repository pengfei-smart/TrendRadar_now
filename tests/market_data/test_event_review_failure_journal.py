"""Tests for the independent append-only review failure journal."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from trendradar.market_data.event_review_failure_journal import append_review_failure


class EventReviewFailureJournalTests(unittest.TestCase):
    def test_appends_multiple_jsonl_records(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "nested" / "failures.jsonl"
            append_review_failure(
                journal_path=path,
                record={"event_id": "event-1", "database_error_type": "OSError"},
            )
            append_review_failure(
                journal_path=path,
                record={"event_id": "event-2", "database_error_type": "OperationalError"},
            )
            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 2)
            self.assertEqual(json.loads(lines[0])["record"]["event_id"], "event-1")
            self.assertEqual(json.loads(lines[1])["record"]["event_id"], "event-2")

    def test_rejects_oversized_record(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "failures.jsonl"
            with self.assertRaisesRegex(ValueError, "16 KiB"):
                append_review_failure(
                    journal_path=path,
                    record={"detail": "x" * 20_000},
                )
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
