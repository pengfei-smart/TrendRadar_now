"""Tests for constructing a review runtime from resolved TrendRadar AI config."""
from __future__ import annotations

import unittest

from trendradar.market_data.event_review_runtime import build_event_review_runtime


class EventReviewRuntimeTests(unittest.TestCase):
    def test_uses_existing_ai_model_key_and_optional_api_base(self):
        runtime = build_event_review_runtime({
            "MODEL": "deepseek/example-model",
            "API_KEY": "runtime-secret",
            "API_BASE": "https://api.example.test",
        })
        self.assertEqual(runtime.reviewer_model, "deepseek/example-model")
        self.assertEqual(runtime.chief_model, "deepseek/example-model")
        self.assertNotIn("runtime-secret", repr(runtime.provider))

    def test_supports_separate_reviewer_and_chief_models(self):
        runtime = build_event_review_runtime(
            {"MODEL": "deepseek/default", "API_KEY": "secret"},
            reviewer_model="deepseek/reviewer",
            chief_model="openai/chair",
        )
        self.assertEqual(runtime.reviewer_model, "deepseek/reviewer")
        self.assertEqual(runtime.chief_model, "openai/chair")

    def test_rejects_placeholder_api_key_without_exposing_it(self):
        with self.assertRaisesRegex(ValueError, "AI API key is missing") as raised:
            build_event_review_runtime({
                "MODEL": "deepseek/example-model",
                "API_KEY": "AI_API_KEY",
            })
        self.assertNotIn("AI_API_KEY", str(raised.exception))

    def test_rejects_missing_model_and_key(self):
        with self.assertRaisesRegex(ValueError, "reviewer_model"):
            build_event_review_runtime({"API_KEY": "secret"})
        with self.assertRaisesRegex(ValueError, "AI API key"):
            build_event_review_runtime({"MODEL": "deepseek/example-model"})

    def test_builds_fresh_per_event_budget_providers_with_shared_global_guard(self):
        runtime = build_event_review_runtime({
            "MODEL": "deepseek/example-model",
            "API_KEY": "secret",
        })
        first = runtime.provider_for_event()
        second = runtime.provider_for_event()
        self.assertIsNot(first, second)
        self.assertIs(first._provider, runtime.global_guard)
        self.assertIs(second._provider, runtime.global_guard)
        self.assertEqual(first.budget.max_model_calls, 5)
        self.assertEqual(first.budget.max_output_tokens_per_call, 3000)
        self.assertEqual(first.budget.max_timeout_seconds, 60.0)

    def test_runtime_accepts_explicit_global_safety_limits(self):
        runtime = build_event_review_runtime(
            {"MODEL": "deepseek/example-model", "API_KEY": "secret"},
            max_model_calls_per_event=4,
            max_output_tokens_per_call=1800,
            max_timeout_seconds=30,
            max_concurrent_calls=2,
            circuit_failure_threshold=3,
            circuit_cooldown_seconds=90,
        )
        guarded = runtime.provider_for_event()
        self.assertEqual(guarded.budget.max_model_calls, 4)
        self.assertEqual(guarded.budget.max_output_tokens_per_call, 1800)
        self.assertEqual(guarded.budget.max_timeout_seconds, 30)

    def test_rejects_invalid_api_base_type(self):
        with self.assertRaisesRegex(TypeError, "API base"):
            build_event_review_runtime({
                "MODEL": "deepseek/example-model",
                "API_KEY": "secret",
                "API_BASE": 123,
            })


if __name__ == "__main__":
    unittest.main()
