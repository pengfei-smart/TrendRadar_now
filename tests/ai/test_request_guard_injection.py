"""Integration tests for shared AI RequestGuard injection."""

import unittest

from trendradar.ai.analyzer import AIAnalyzer
from trendradar.ai.filter import AIFilter
from trendradar.ai.translator import AITranslator


class AIRequestGuardInjectionTests(unittest.TestCase):

    def test_analyzer_filter_and_translator_share_guard_instance(self):
        shared_guard = object()
        ai_config = {
            "MODEL": "deepseek/example-model",
            "API_KEY": "test-key",
        }

        analyzer = AIAnalyzer(
            ai_config,
            {},
            lambda: None,
            request_guard=shared_guard,
        )
        ai_filter = AIFilter(
            ai_config,
            {},
            lambda: None,
            request_guard=shared_guard,
        )
        translator = AITranslator(
            {},
            ai_config,
            request_guard=shared_guard,
        )

        self.assertIs(analyzer.client._request_guard, shared_guard)
        self.assertIs(ai_filter.client._request_guard, shared_guard)
        self.assertIs(translator.client._request_guard, shared_guard)
