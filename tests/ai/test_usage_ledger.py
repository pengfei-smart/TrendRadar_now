import sqlite3
from contextlib import closing
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from trendradar.ai.usage_ledger import AIUsageLedger, AIUsageRecord


class AIUsageLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "usage.db"
        self.ledger = AIUsageLedger(self.path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def record(self, **overrides):
        values = {
            "feature": "analysis",
            "operation": "summarize",
            "provider": "litellm",
            "model": "deepseek/deepseek-v4-pro",
            "request_status": "success",
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
            "prompt_cache_hit_tokens": 30,
            "prompt_cache_miss_tokens": 70,
            "started_at": datetime(2026, 11, 19, 1, tzinfo=timezone.utc),
            "completed_at": datetime(2026, 11, 19, 1, 0, 2, tzinfo=timezone.utc),
        }
        values.update(overrides)
        return AIUsageRecord(**values)

    def test_append_and_read_provider_reported_usage(self):
        usage_id = self.ledger.append(self.record())
        rows = self.ledger.list_records()
        self.assertEqual(usage_id, rows[0]["usage_id"])
        self.assertEqual(30, rows[0]["prompt_cache_hit_tokens"])
        self.assertEqual(70, rows[0]["prompt_cache_miss_tokens"])
        self.assertEqual("deepseek/deepseek-v4-pro", rows[0]["model"])

    def test_unknown_cache_breakdown_remains_unknown(self):
        self.ledger.append(self.record(
            prompt_cache_hit_tokens=None, prompt_cache_miss_tokens=None
        ))
        row = self.ledger.list_records()[0]
        self.assertIsNone(row["prompt_cache_hit_tokens"])
        self.assertIsNone(row["prompt_cache_miss_tokens"])

    def test_error_record_can_be_saved_without_token_estimates(self):
        self.ledger.append(self.record(
            request_status="error", prompt_tokens=None, completion_tokens=None,
            total_tokens=None, error_code="TimeoutError",
        ))
        row = self.ledger.list_records()[0]
        self.assertEqual("error", row["request_status"])
        self.assertIsNone(row["total_tokens"])

    def test_prompt_and_response_are_not_columns(self):
        with closing(sqlite3.connect(self.path)) as connection, connection:
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(ai_usage_records)")
            }
        self.assertNotIn("prompt", columns)
        self.assertNotIn("response", columns)
        self.assertNotIn("api_key", columns)

    def test_records_are_append_only(self):
        usage_id = self.ledger.append(self.record())
        with closing(sqlite3.connect(self.path)) as connection, connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE ai_usage_records SET model='changed' WHERE usage_id=?",
                    (usage_id,),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "DELETE FROM ai_usage_records WHERE usage_id=?", (usage_id,)
                )

    def test_rejects_naive_timestamps_and_invalid_counts(self):
        with self.assertRaises(ValueError):
            self.record(started_at=datetime(2026, 11, 19))
        with self.assertRaises(ValueError):
            self.record(prompt_tokens=-1)

    def test_iter_records_streams_in_ascending_id_order(self):
        first = self.ledger.append(self.record())
        second = self.ledger.append(self.record(operation="translate"))
        rows = list(self.ledger.iter_records(batch_size=1))
        self.assertEqual([first, second], [row["usage_id"] for row in rows])

    def test_iter_records_rejects_invalid_batch_size(self):
        with self.assertRaises(ValueError):
            list(self.ledger.iter_records(batch_size=0))
        with self.assertRaises(ValueError):
            list(self.ledger.iter_records(batch_size=501))

    def test_pagination_is_bounded(self):
        with self.assertRaises(ValueError):
            self.ledger.list_records(limit=501)
        with self.assertRaises(ValueError):
            self.ledger.list_records(offset=-1)


if __name__ == "__main__":
    unittest.main()
