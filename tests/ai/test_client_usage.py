import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from trendradar.ai.client import AIClient
from trendradar.ai.usage_ledger import AIUsageLedger


class AIClientUsageTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.ledger = AIUsageLedger(Path(self.temp_dir.name) / "usage.db")
        self.config = {
            "MODEL": "deepseek/deepseek-v4-pro",
            "API_KEY": "test-key",
            "MAX_TOKENS": 100,
        }

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_success_records_actual_model_and_cache_usage(self):
        response = SimpleNamespace(
            model="deepseek/deepseek-v4-flash",
            id="req-123",
            usage=SimpleNamespace(
                prompt_tokens=100, completion_tokens=20, total_tokens=120,
                prompt_cache_hit_tokens=40, prompt_cache_miss_tokens=60,
            ),
            choices=[SimpleNamespace(
                message=SimpleNamespace(content="result")
            )],
        )
        client = AIClient(
            self.config, feature="translation", usage_ledger=self.ledger
        )
        with patch("trendradar.ai.client.completion", return_value=response):
            self.assertEqual("result", client.chat(
                [{"role": "user", "content": "hello"}],
                usage_operation="translate_batch",
            ))
        row = self.ledger.list_records()[0]
        self.assertEqual("translation", row["feature"])
        self.assertEqual("translate_batch", row["operation"])
        self.assertEqual("deepseek/deepseek-v4-flash", row["model"])
        self.assertEqual("req-123", row["request_id"])
        self.assertEqual(40, row["prompt_cache_hit_tokens"])
        self.assertEqual(60, row["prompt_cache_miss_tokens"])

    def test_failure_is_recorded_without_exception_message(self):
        client = AIClient(self.config, usage_ledger=self.ledger)
        with patch(
            "trendradar.ai.client.completion",
            side_effect=RuntimeError("secret prompt and key must not persist"),
        ):
            with self.assertRaises(RuntimeError):
                client.chat([{"role": "user", "content": "private"}])
        row = self.ledger.list_records()[0]
        self.assertEqual("error", row["request_status"])
        self.assertEqual("RuntimeError", row["error_code"])
        self.assertNotIn("secret", str(row))

    def test_nested_cached_tokens_are_supported(self):
        response = {
            "model": "deepseek/deepseek-v4-pro",
            "id": "req-456",
            "usage": {
                "prompt_tokens": 80, "completion_tokens": 10, "total_tokens": 90,
                "prompt_tokens_details": {"cached_tokens": 25},
            },
            "choices": [{"message": {"content": "ok"}}],
        }
        client = AIClient(self.config, usage_ledger=self.ledger)
        with patch("trendradar.ai.client.completion", return_value=response):
            client.chat([{"role": "user", "content": "hello"}])
        row = self.ledger.list_records()[0]
        self.assertEqual(25, row["prompt_cache_hit_tokens"])
        self.assertIsNone(row["prompt_cache_miss_tokens"])

    def test_ledger_failure_does_not_break_ai_response(self):
        class BrokenLedger:
            def append(self, record):
                raise OSError("disk failure")

        response = SimpleNamespace(
            model=self.config["MODEL"],
            id="req-789",
            usage=None,
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
        )
        client = AIClient(self.config, usage_ledger=BrokenLedger())
        with patch("trendradar.ai.client.completion", return_value=response):
            self.assertEqual("ok", client.chat(
                [{"role": "user", "content": "hello"}]
            ))


if __name__ == "__main__":
    unittest.main()
