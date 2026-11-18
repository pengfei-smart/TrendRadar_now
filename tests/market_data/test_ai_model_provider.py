import unittest
from types import SimpleNamespace

from trendradar.market_data.ai_model_provider import (
    ChatModelError,
    ChatModelRequest,
    ChatModelResponse,
    LiteLLMChatProvider,
    parse_json_object,
)


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


if __name__ == "__main__":
    unittest.main()
