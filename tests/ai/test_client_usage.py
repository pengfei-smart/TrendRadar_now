from litellm.exceptions import RateLimitError
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from trendradar.ai.client import AIClient
from trendradar.ai.request_guard import (
    AIRequestGuard,
    AIRequestNotAllowedError,
)
from trendradar.ai.usage_ledger import AIUsageLedger


class AIClientUsageTests(unittest.TestCase):

    def _make_client(self, *args, request_guard=None, **kwargs):
        if request_guard is None:
            request_guard = self.allow_guard
        return AIClient(
            *args,
            request_guard=request_guard,
            **kwargs,
        )

    def test_fallback_models_are_never_passed_to_litellm(self):
        response = SimpleNamespace(
            model=self.config["MODEL"],
            id="no-fallback-123",
            usage=None,
            choices=[SimpleNamespace(
                message=SimpleNamespace(content="ok")
            )],
        )

        client = self._make_client(
            {
                **self.config,
                "FALLBACK_MODELS": ["deepseek/deepseek-v4-pro"],
            },
            usage_ledger=self.ledger,
        )

        with patch(
            "trendradar.ai.client.completion",
            return_value=response,
        ) as mock_completion:
            self.assertEqual(
                "ok",
                client.chat(
                    [{"role": "user", "content": "hello"}],
                    fallbacks=["deepseek/deepseek-v4-pro"],
                ),
            )

        self.assertNotIn("fallbacks", mock_completion.call_args.kwargs)

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.ledger = AIUsageLedger(Path(self.temp_dir.name) / "usage.db")
        self.allow_guard = SimpleNamespace(check=lambda: None)
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
        client = self._make_client(
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
        self.assertEqual(1, row["attempts"])
        self.assertTrue(row["attempts_known"])

    def test_failure_is_recorded_without_exception_message(self):
        client = self._make_client(self.config, usage_ledger=self.ledger)
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
        self.assertEqual(1, row["attempts"])
        self.assertTrue(row["attempts_known"])

    def test_nested_cached_tokens_are_supported(self):
        response = SimpleNamespace(
            model="deepseek/deepseek-v4-pro",
            id="req-456",
            usage={
                "prompt_tokens": 80, "completion_tokens": 10, "total_tokens": 90,
                "prompt_tokens_details": {"cached_tokens": 25},
            },
            choices=[SimpleNamespace(
                message=SimpleNamespace(content="ok")
            )],
        )
        client = self._make_client(self.config, usage_ledger=self.ledger)
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
        client = self._make_client(
            self.config,
            feature="translation",
            usage_ledger=BrokenLedger(),
        )

        with self.assertLogs(
            "trendradar.ai.client", level="WARNING"
        ) as captured:
            with patch(
                "trendradar.ai.client.completion",
                return_value=response,
            ):
                self.assertEqual("ok", client.chat(
                    [{"role": "user", "content": "hello"}],
                    usage_operation="translate_batch",
                ))

        log_text = "\n".join(captured.output)
        self.assertIn("feature=translation", log_text)
        self.assertIn("operation=translate_batch", log_text)
        self.assertIn("error_type=OSError", log_text)
        self.assertNotIn("disk failure", log_text)
        self.assertNotIn("test-key", log_text)
        self.assertNotIn("hello", log_text)


    def test_missing_guard_blocks_completion_and_usage_record(self):
        client = AIClient(
            self.config,
            usage_ledger=self.ledger,
        )

        with patch("trendradar.ai.client.completion") as mock_completion:
            with self.assertRaises(AIRequestNotAllowedError):
                client.chat([
                    {"role": "user", "content": "hello"}
                ])

        mock_completion.assert_not_called()
        self.assertEqual((), self.ledger.list_records())

    def test_guard_denial_blocks_completion_and_usage_record(self):
        scheduler = SimpleNamespace(
            is_ai_allowed_now=lambda: False
        )
        guard = AIRequestGuard(scheduler)

        client = self._make_client(
            self.config,
            usage_ledger=self.ledger,
            request_guard=guard,
        )

        with patch("trendradar.ai.client.completion") as mock_completion:
            with self.assertRaises(AIRequestNotAllowedError):
                client.chat([
                    {"role": "user", "content": "hello"}
                ])

        mock_completion.assert_not_called()
        self.assertEqual((), self.ledger.list_records())

    def test_guard_allows_completion_and_usage_record(self):
        scheduler = SimpleNamespace(
            is_ai_allowed_now=lambda: True
        )
        guard = AIRequestGuard(scheduler)

        response = SimpleNamespace(
            model=self.config["MODEL"],
            id="guard-allowed-123",
            usage=None,
            choices=[SimpleNamespace(
                message=SimpleNamespace(content="ok")
            )],
        )

        client = self._make_client(
            self.config,
            usage_ledger=self.ledger,
            request_guard=guard,
        )

        with patch(
            "trendradar.ai.client.completion",
            return_value=response,
        ) as mock_completion:
            self.assertEqual(
                "ok",
                client.chat([
                    {"role": "user", "content": "hello"}
                ]),
            )

        mock_completion.assert_called_once()
        records = self.ledger.list_records()
        self.assertEqual(1, len(records))
        self.assertEqual("success", records[0]["request_status"])
        self.assertEqual("guard-allowed-123", records[0]["request_id"])


    def test_retry_once_then_success_records_two_attempts(self):

        response = SimpleNamespace(
            model=self.config["MODEL"],
            id="retry-success",
            usage=None,
            choices=[SimpleNamespace(
                message=SimpleNamespace(content="ok")
            )],
        )

        client = self._make_client(
            {**self.config, "NUM_RETRIES": 3},
            usage_ledger=self.ledger,
        )

        with patch(
            "trendradar.ai.client.completion",
            side_effect=[RateLimitError(message="rate limited", llm_provider="openai", model="test-model"), response],
        ) as mock_completion, patch("trendradar.ai.client.time.sleep"):
            self.assertEqual(
                "ok",
                client.chat([{"role": "user", "content": "hello"}]),
            )

        self.assertEqual(2, mock_completion.call_count)

        for call in mock_completion.call_args_list:
            self.assertEqual(0, call.kwargs["num_retries"])
            self.assertEqual(0, call.kwargs["max_retries"])

        records = self.ledger.list_records()
        self.assertEqual(1, len(records))
        self.assertEqual("success", records[0]["request_status"])
        self.assertEqual(2, records[0]["attempts"])
        self.assertTrue(records[0]["attempts_known"])

    def test_retry_exhaustion_stops_after_four_attempts(self):

        client = self._make_client(
            {**self.config, "NUM_RETRIES": 3},
            usage_ledger=self.ledger,
        )

        with patch(
            "trendradar.ai.client.completion",
            side_effect=RateLimitError(message="rate limited", llm_provider="openai", model="test-model"),
        ) as mock_completion, patch("trendradar.ai.client.time.sleep"):
            with self.assertRaises(RateLimitError):
                client.chat([{"role": "user", "content": "hello"}])

        self.assertEqual(4, mock_completion.call_count)

        records = self.ledger.list_records()
        self.assertEqual(1, len(records))
        self.assertEqual("error", records[0]["request_status"])
        self.assertEqual(4, records[0]["attempts"])
        self.assertTrue(records[0]["attempts_known"])
        self.assertEqual("RateLimitError", records[0]["error_code"])

    def test_guard_denial_between_retries_records_only_real_attempts(self):

        class DenyOnSecondCheck:
            def __init__(self):
                self.check_count = 0

            def check(self):
                self.check_count += 1
                if self.check_count == 2:
                    raise AIRequestNotAllowedError("window closed")

        guard = DenyOnSecondCheck()

        client = self._make_client(
            {**self.config, "NUM_RETRIES": 3},
            usage_ledger=self.ledger,
            request_guard=guard,
        )

        with patch(
            "trendradar.ai.client.completion",
            side_effect=RateLimitError(message="rate limited", llm_provider="openai", model="test-model"),
        ) as mock_completion, patch("trendradar.ai.client.time.sleep"):
            with self.assertRaises(AIRequestNotAllowedError):
                client.chat([{"role": "user", "content": "hello"}])

        self.assertEqual(2, guard.check_count)
        self.assertEqual(1, mock_completion.call_count)

        records = self.ledger.list_records()
        self.assertEqual(1, len(records))
        self.assertEqual("error", records[0]["request_status"])
        self.assertEqual(1, records[0]["attempts"])
        self.assertTrue(records[0]["attempts_known"])
        self.assertEqual("RateLimitError", records[0]["error_code"])

    def test_non_retryable_error_is_not_retried(self):
        client = self._make_client(
            {**self.config, "NUM_RETRIES": 3},
            usage_ledger=self.ledger,
        )

        with patch(
            "trendradar.ai.client.completion",
            side_effect=RuntimeError("unexpected error"),
        ) as mock_completion, patch("trendradar.ai.client.time.sleep") as mock_sleep:
            with self.assertRaises(RuntimeError):
                client.chat([{"role": "user", "content": "hello"}])

        mock_completion.assert_called_once()
        mock_sleep.assert_not_called()

        records = self.ledger.list_records()
        self.assertEqual(1, len(records))
        self.assertEqual(1, records[0]["attempts"])
        self.assertTrue(records[0]["attempts_known"])

    def test_custom_same_name_rate_limit_error_is_not_retried(self):
        class RateLimitError(Exception):
            pass

        client = self._make_client(
            {**self.config, "NUM_RETRIES": 3},
            usage_ledger=self.ledger,
        )

        with patch(
            "trendradar.ai.client.completion",
            side_effect=RateLimitError("custom error"),
        ) as mock_completion, patch(
            "trendradar.ai.client.time.sleep"
        ) as mock_sleep:
            with self.assertRaises(RateLimitError):
                client.chat([{"role": "user", "content": "hello"}])

        mock_completion.assert_called_once()
        mock_sleep.assert_not_called()

        records = self.ledger.list_records()
        self.assertEqual(1, len(records))
        self.assertEqual("error", records[0]["request_status"])
        self.assertEqual(1, records[0]["attempts"])
        self.assertEqual("RateLimitError", records[0]["error_code"])
