import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace

from trendradar.ai.usage_ledger import AIUsageLedger
from trendradar.ai.request_guard import AIRequestNotAllowedError
from trendradar.market_data.ai_model_provider import (
    ChatModelError,
    ChatModelRequest,
    ChatModelResponse,
    LiteLLMChatProvider as _LiteLLMChatProvider,
    parse_json_object,
)


class AllowTestGuard:
    """仅供单元测试使用的显式放行 Guard。"""

    def check(self):
        return None


def LiteLLMChatProvider(*args, **kwargs):
    """测试工厂：默认使用测试专用授权，允许显式传入其他 Guard。"""
    kwargs.setdefault("request_guard", AllowTestGuard())
    return _LiteLLMChatProvider(*args, **kwargs)


def fake_response(content='{"ok": true}', finish_reason="stop", request_id="req-1"):
    return SimpleNamespace(
        id=request_id,
        choices=[SimpleNamespace(
            message=SimpleNamespace(content=content),
            finish_reason=finish_reason,
        )],
    )


class RetryableStatusError(RuntimeError):
    def __init__(self, status_code):
        super().__init__("provider failure")
        self.status_code = status_code


class AiModelProviderTests(unittest.TestCase):
    def test_request_validates_required_fields_and_bounds(self):
        with self.assertRaises(ValueError):
            ChatModelRequest(model="", system_prompt="system", user_prompt="user")
        with self.assertRaises(ValueError):
            ChatModelRequest(model="model", system_prompt=" ", user_prompt="user")
        with self.assertRaises(ValueError):
            ChatModelRequest(model="model", system_prompt="system", user_prompt="user",
                             timeout_seconds=0)
        with self.assertRaises(ValueError):
            ChatModelRequest(model="model", system_prompt="system", user_prompt="user",
                             temperature=2.1)
        with self.assertRaises(ValueError):
            ChatModelRequest(model="model", system_prompt="system", user_prompt="user",
                             response_format="text")

    def test_missing_request_guard_rejects_request_before_completion(self):
        calls = []

        def completion(**kwargs):
            calls.append(kwargs)
            return fake_response()

        provider = _LiteLLMChatProvider(
            completion_callable=completion,
        )

        with self.assertRaises(AIRequestNotAllowedError):
            provider.complete(ChatModelRequest(
                model="provider/model-x",
                system_prompt="system",
                user_prompt="user",
            ))

        self.assertEqual(calls, [])
    def test_successful_call_returns_provider_model_and_request_metadata(self):
        calls = []

        def completion(**kwargs):
            calls.append(kwargs)
            return fake_response()

        provider = LiteLLMChatProvider(completion_callable=completion)
        result = provider.complete(ChatModelRequest(
            model="provider/model-x",
            system_prompt="system",
            user_prompt="user",
        ))

        self.assertIsInstance(result, ChatModelResponse)
        self.assertEqual(result.content, '{"ok": true}')
        self.assertEqual(result.provider, "litellm")
        self.assertEqual(result.model, "provider/model-x")
        self.assertEqual(result.request_id, "req-1")
        self.assertEqual(result.attempts, 1)
        self.assertEqual(calls[0]["response_format"], {"type": "json_object"})
        self.assertEqual(calls[0]["messages"][0]["role"], "system")

    def test_extracts_token_usage_when_provider_supplies_it(self):
        raw = fake_response()
        raw.usage = SimpleNamespace(
            prompt_tokens=123,
            completion_tokens=45,
            total_tokens=168,
        )
        provider = LiteLLMChatProvider(completion_callable=lambda **kwargs: raw)
        result = provider.complete(ChatModelRequest(
            model="provider/model-x",
            system_prompt="system",
            user_prompt="user",
        ))
        self.assertEqual(result.prompt_tokens, 123)
        self.assertEqual(result.completion_tokens, 45)
        self.assertEqual(result.total_tokens, 168)

    def test_missing_or_invalid_token_usage_remains_unknown(self):
        raw = fake_response()
        raw.usage = SimpleNamespace(
            prompt_tokens=None,
            completion_tokens=-1,
            total_tokens=True,
        )
        provider = LiteLLMChatProvider(completion_callable=lambda **kwargs: raw)
        result = provider.complete(ChatModelRequest(
            model="provider/model-x",
            system_prompt="system",
            user_prompt="user",
        ))
        self.assertIsNone(result.prompt_tokens)
        self.assertIsNone(result.completion_tokens)
        self.assertIsNone(result.total_tokens)

    def test_extracts_token_usage_from_mapping_response(self):
        raw = {
            "id": "req-map",
            "choices": [{
                "message": {"content": '{"ok": true}'},
                "finish_reason": "stop",
            }],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            },
        }
        provider = LiteLLMChatProvider(completion_callable=lambda **kwargs: raw)
        result = provider.complete(ChatModelRequest(
            model="provider/model-x",
            system_prompt="system",
            user_prompt="user",
        ))
        self.assertEqual(
            (result.prompt_tokens, result.completion_tokens, result.total_tokens),
            (10, 5, 15),
        )


    def test_records_event_review_usage_and_cache_breakdown_in_unified_ledger(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            ledger = AIUsageLedger(Path(temp_dir) / "ai_usage.db")
            raw = fake_response(request_id="req-review")
            raw.model = "deepseek/deepseek-v4-pro-0813"
            raw.usage = SimpleNamespace(
                prompt_tokens=100,
                completion_tokens=25,
                total_tokens=125,
                prompt_tokens_details=SimpleNamespace(cached_tokens=30),
                prompt_cache_miss_tokens=70,
            )
            provider = LiteLLMChatProvider(
                completion_callable=lambda **kwargs: raw,
                usage_ledger=ledger,
            )
            response = provider.complete(ChatModelRequest(
                model="deepseek/deepseek-v4-pro",
                system_prompt="private system prompt",
                user_prompt="private user prompt",
                usage_operation="reviewer:fact_checker",
            ))

            self.assertEqual(response.model, "deepseek/deepseek-v4-pro-0813")
            self.assertEqual(response.prompt_cache_hit_tokens, 30)
            self.assertEqual(response.prompt_cache_miss_tokens, 70)
            rows = ledger.list_records()
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row["feature"], "event_review")
            self.assertEqual(row["operation"], "reviewer:fact_checker")
            self.assertEqual(row["model"], "deepseek/deepseek-v4-pro-0813")
            self.assertEqual(row["request_id"], "req-review")
            self.assertEqual(row["prompt_cache_hit_tokens"], 30)
            self.assertEqual(row["prompt_cache_miss_tokens"], 70)
            self.assertEqual(row["attempts"], 1)
            self.assertEqual(row["attempts_known"], 1)
            self.assertNotIn("private system prompt", str(row))
            self.assertNotIn("private user prompt", str(row))

    def test_ledger_failure_logs_safely_without_breaking_review_response(self):
        class BrokenLedger:
            def append(self, record):
                raise OSError("private disk failure")

        provider = LiteLLMChatProvider(
            completion_callable=lambda **kwargs: fake_response(
                content='{"approved": true}',
                request_id="req-ledger-failure",
            ),
            usage_ledger=BrokenLedger(),
        )

        with self.assertLogs(
            "trendradar.market_data.ai_model_provider",
            level="WARNING",
        ) as captured:
            response = provider.complete(ChatModelRequest(
                model="deepseek/deepseek-v4-pro",
                system_prompt="private system prompt",
                user_prompt="private user prompt",
                usage_operation="reviewer:fact_checker",
            ))

        self.assertEqual(response.content, '{"approved": true}')

        log_text = "\n".join(captured.output)
        self.assertIn("feature=event_review", log_text)
        self.assertIn("operation=reviewer:fact_checker", log_text)
        self.assertIn("error_type=OSError", log_text)
        self.assertNotIn("private disk failure", log_text)
        self.assertNotIn("private system prompt", log_text)
        self.assertNotIn("private user prompt", log_text)


    def test_failed_event_review_request_is_recorded_without_token_estimates(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            ledger = AIUsageLedger(Path(temp_dir) / "ai_usage.db")
            provider = LiteLLMChatProvider(
                completion_callable=lambda **kwargs: (_ for _ in ()).throw(
                    ConnectionError("private transport detail")
                ),
                max_attempts=2,
                retry_delay_seconds=0,
                sleep_callable=lambda _: None,
                usage_ledger=ledger,
            )
            with self.assertRaises(ChatModelError):
                provider.complete(ChatModelRequest(
                    model="deepseek/deepseek-v4-pro",
                    system_prompt="system",
                    user_prompt="user",
                    usage_operation="chief_reviewer",
                ))
            row = ledger.list_records()[0]
            self.assertEqual(row["request_status"], "error")
            self.assertEqual(row["operation"], "chief_reviewer")
            self.assertEqual(row["attempts"], 2)
            self.assertEqual(row["attempts_known"], 1)
            self.assertIsNone(row["prompt_tokens"])
            self.assertIsNone(row["prompt_cache_hit_tokens"])
            self.assertEqual(row["error_code"], "ConnectionError")
            self.assertNotIn("private transport detail", str(row))

    def test_guard_denial_before_first_attempt_does_not_call_api_or_record_usage(self):
        class DenyGuard:
            def check(self):
                raise AIRequestNotAllowedError("AI request is not allowed")

        calls = []

        def completion(**kwargs):
            calls.append(kwargs)
            return fake_response()

        with tempfile.TemporaryDirectory() as temp_dir:
            ledger = AIUsageLedger(Path(temp_dir) / "ai_usage.db")

            provider = LiteLLMChatProvider(
                completion_callable=completion,
                usage_ledger=ledger,
                request_guard=DenyGuard(),
            )

            with self.assertRaises(AIRequestNotAllowedError):
                provider.complete(ChatModelRequest(
                    model="deepseek/deepseek-v4-pro",
                    system_prompt="system",
                    user_prompt="user",
                    usage_operation="chief_reviewer",
                ))

            self.assertEqual(calls, [])
            self.assertEqual(ledger.list_records(), ())

    def test_guard_denial_between_retries_records_only_real_attempt(self):
        class SequenceGuard:
            def __init__(self):
                self.check_calls = 0

            def check(self):
                self.check_calls += 1

                if self.check_calls == 2:
                    raise AIRequestNotAllowedError(
                        "AI request is not allowed"
                    )

        calls = []
        guard = SequenceGuard()

        def completion(**kwargs):
            calls.append(kwargs)
            raise RetryableStatusError(503)

        with tempfile.TemporaryDirectory() as temp_dir:
            ledger = AIUsageLedger(Path(temp_dir) / "ai_usage.db")

            provider = LiteLLMChatProvider(
                completion_callable=completion,
                max_attempts=2,
                retry_delay_seconds=0,
                sleep_callable=lambda _: None,
                usage_ledger=ledger,
                request_guard=guard,
            )

            with self.assertRaises(AIRequestNotAllowedError):
                provider.complete(ChatModelRequest(
                    model="deepseek/deepseek-v4-pro",
                    system_prompt="system",
                    user_prompt="user",
                    usage_operation="chief_reviewer",
                ))

            self.assertEqual(len(calls), 1)
            self.assertEqual(guard.check_calls, 2)

            records = ledger.list_records()
            self.assertEqual(len(records), 1)

            row = records[0]
            self.assertEqual(row["request_status"], "error")
            self.assertEqual(row["operation"], "chief_reviewer")
            self.assertEqual(row["attempts"], 1)
            self.assertEqual(row["error_code"], "RetryableStatusError")

    def test_retries_transient_server_error_then_succeeds(self):
        calls = []
        sleeps = []

        def completion(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise RetryableStatusError(503)
            return fake_response()

        provider = LiteLLMChatProvider(
            completion_callable=completion,
            max_attempts=3,
            retry_delay_seconds=0.1,
            sleep_callable=sleeps.append,
        )
        result = provider.complete(ChatModelRequest(
            model="model", system_prompt="system", user_prompt="user",
        ))

        self.assertEqual(len(calls), 2)
        self.assertEqual(sleeps, [0.1])
        self.assertEqual(result.attempts, 2)

    def test_retries_connection_error_with_bounded_attempts(self):
        calls = []
        sleeps = []

        def completion(**kwargs):
            calls.append(kwargs)
            raise ConnectionError("temporary network failure")

        provider = LiteLLMChatProvider(
            completion_callable=completion,
            max_attempts=3,
            retry_delay_seconds=0,
            sleep_callable=sleeps.append,
        )
        with self.assertRaises(ChatModelError):
            provider.complete(ChatModelRequest(
                model="model", system_prompt="system", user_prompt="user",
            ))
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(sleeps), 2)

    def test_does_not_retry_non_retryable_provider_error(self):
        calls = []

        def completion(**kwargs):
            calls.append(kwargs)
            raise ValueError("invalid model configuration")

        provider = LiteLLMChatProvider(
            completion_callable=completion,
            max_attempts=3,
            sleep_callable=lambda _: self.fail("must not sleep"),
        )
        with self.assertRaises(ChatModelError) as context:
            provider.complete(ChatModelRequest(
                model="model", system_prompt="system", user_prompt="user",
            ))
        self.assertEqual(len(calls), 1)
        self.assertIn("ValueError", str(context.exception))
        self.assertNotIn("invalid model configuration", str(context.exception))

    def test_rejects_invalid_response_shape(self):
        provider = LiteLLMChatProvider(
            completion_callable=lambda **kwargs: {"unexpected": "shape"},
        )
        with self.assertRaises(ChatModelError):
            provider.complete(ChatModelRequest(
                model="model", system_prompt="system", user_prompt="user",
            ))

    def test_rejects_empty_model_content(self):
        provider = LiteLLMChatProvider(
            completion_callable=lambda **kwargs: fake_response(content=" "),
        )
        with self.assertRaises(ChatModelError):
            provider.complete(ChatModelRequest(
                model="model", system_prompt="system", user_prompt="user",
            ))

    def test_parse_json_object_accepts_object(self):
        self.assertEqual(parse_json_object('{"score": 0.5}'), {"score": 0.5})

    def test_parse_json_object_rejects_invalid_json_and_markdown_fences(self):
        fence = "```"
        samples = ("not json", fence + 'json\n{"score": 0.5}\n' + fence)
        for content in samples:
            with self.subTest(content=content):
                with self.assertRaises(ValueError):
                    parse_json_object(content)

    def test_parse_json_object_rejects_non_object_json(self):
        for content in ('[]', 'null', '"text"', '1'):
            with self.subTest(content=content):
                with self.assertRaises(ValueError):
                    parse_json_object(content)

    def test_parse_json_object_rejects_empty_content(self):
        with self.assertRaises(ValueError):
            parse_json_object("")

    def test_provider_rejects_wrong_request_type(self):
        provider = LiteLLMChatProvider(completion_callable=lambda **kwargs: fake_response())
        with self.assertRaises(TypeError):
            provider.complete({"model": "model"})


    def test_passes_explicit_api_credentials_and_base_without_exposing_them(self):
        calls = []
        secret = "test-secret-never-log"

        def completion(**kwargs):
            calls.append(kwargs)
            return fake_response()

        provider = LiteLLMChatProvider(
            completion_callable=completion,
            api_key=secret,
            api_base="https://api.example.test/v1",
        )
        provider.complete(ChatModelRequest(
            model="openai/example-model",
            system_prompt="system",
            user_prompt="user",
        ))
        self.assertEqual(calls[0]["api_key"], secret)
        self.assertEqual(calls[0]["api_base"], "https://api.example.test/v1")
        self.assertNotIn(secret, repr(provider))
    def test_guard_denial_blocks_completion_and_usage_record(self):
        class DeniedGuard:
            def check(self):
                raise AIRequestNotAllowedError("outside allowed window")

        with tempfile.TemporaryDirectory() as temp_dir:
            ledger = AIUsageLedger(Path(temp_dir) / "ai_usage.db")
            calls = []

            def completion(**kwargs):
                calls.append(kwargs)
                return fake_response()

            provider = LiteLLMChatProvider(
                completion_callable=completion,
                usage_ledger=ledger,
                request_guard=DeniedGuard(),
            )

            with self.assertRaises(AIRequestNotAllowedError):
                provider.complete(ChatModelRequest(
                    model="deepseek/deepseek-v4-pro",
                    system_prompt="system",
                    user_prompt="user",
                    usage_operation="reviewer:fact_checker",
                ))

            self.assertEqual(len(calls), 0)
            self.assertEqual(ledger.list_records(), ())

    def test_guard_rechecks_before_retry_and_records_only_real_attempt(self):
        class RetryThenDeniedGuard:
            def __init__(self):
                self.check_count = 0

            def check(self):
                self.check_count += 1
                if self.check_count >= 2:
                    raise AIRequestNotAllowedError(
                        "window closed before retry"
                    )

        with tempfile.TemporaryDirectory() as temp_dir:
            ledger = AIUsageLedger(Path(temp_dir) / "ai_usage.db")
            guard = RetryThenDeniedGuard()
            calls = []

            def completion(**kwargs):
                calls.append(kwargs)
                raise RetryableStatusError(503)

            provider = LiteLLMChatProvider(
                completion_callable=completion,
                max_attempts=3,
                retry_delay_seconds=0,
                sleep_callable=lambda _: None,
                usage_ledger=ledger,
                request_guard=guard,
            )

            with self.assertRaises(AIRequestNotAllowedError):
                provider.complete(ChatModelRequest(
                    model="deepseek/deepseek-v4-pro",
                    system_prompt="system",
                    user_prompt="user",
                    usage_operation="reviewer:fact_checker",
                ))

            self.assertEqual(guard.check_count, 2)
            self.assertEqual(len(calls), 1)

            rows = ledger.list_records()
            self.assertEqual(len(rows), 1)

            row = rows[0]
            self.assertEqual(row["request_status"], "error")
            self.assertEqual(row["attempts"], 1)
            self.assertEqual(row["attempts_known"], 1)
            self.assertEqual(row["error_code"], "RetryableStatusError")

if __name__ == "__main__":
    unittest.main()
