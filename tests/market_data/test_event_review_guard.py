from __future__ import annotations

import unittest
import threading

from trendradar.market_data.ai_model_provider import (
    ChatModelError, ChatModelRequest, ChatModelResponse,
)
from trendradar.market_data.event_review_guard import (
    EventReviewBudget, EventReviewBudgetProvider, GlobalModelCallGuard,
)


def request(*, max_tokens=100, timeout=10):
    return ChatModelRequest(
        model="provider/model",
        system_prompt="system",
        user_prompt="user",
        max_tokens=max_tokens,
        timeout_seconds=timeout,
    )


class FakeProvider:
    def __init__(self, outcomes=None):
        self.outcomes = list(outcomes or [])
        self.calls = 0

    def complete(self, request):
        self.calls += 1
        outcome = self.outcomes.pop(0) if self.outcomes else None
        if isinstance(outcome, Exception):
            raise outcome
        return ChatModelResponse("{}", "fake", request.model)


class EventReviewBudgetTests(unittest.TestCase):
    def test_limits_logical_calls_and_failed_calls_consume_budget(self):
        fake = FakeProvider([ChatModelError("failed")])
        guarded = EventReviewBudgetProvider(
            fake, EventReviewBudget(max_model_calls=2)
        )
        with self.assertRaises(ChatModelError):
            guarded.complete(request())
        guarded.complete(request())
        with self.assertRaisesRegex(ChatModelError, "budget exhausted"):
            guarded.complete(request())
        self.assertEqual(guarded.calls_used, 2)
        self.assertEqual(fake.calls, 2)

    def test_rejects_output_tokens_and_timeout_above_budget(self):
        guarded = EventReviewBudgetProvider(
            FakeProvider(),
            EventReviewBudget(max_output_tokens_per_call=200, max_timeout_seconds=20),
        )
        with self.assertRaisesRegex(ChatModelError, "token budget"):
            guarded.complete(request(max_tokens=201))
        with self.assertRaisesRegex(ChatModelError, "timeout budget"):
            guarded.complete(request(timeout=21))
        self.assertEqual(guarded.calls_used, 0)

    def test_separate_event_wrappers_have_independent_budgets(self):
        fake = FakeProvider()
        shared = GlobalModelCallGuard(fake)
        first = EventReviewBudgetProvider(shared, EventReviewBudget(max_model_calls=1))
        second = EventReviewBudgetProvider(shared, EventReviewBudget(max_model_calls=1))
        first.complete(request())
        second.complete(request())
        self.assertEqual(fake.calls, 2)


class GlobalModelCallGuardTests(unittest.TestCase):
    def test_opens_after_consecutive_failures_and_recovers_after_cooldown(self):
        clock = [10.0]
        fake = FakeProvider([ChatModelError("one"), ChatModelError("two")])
        guarded = GlobalModelCallGuard(
            fake, failure_threshold=2, cooldown_seconds=5,
            monotonic=lambda: clock[0],
        )
        with self.assertRaises(ChatModelError):
            guarded.complete(request())
        with self.assertRaises(ChatModelError):
            guarded.complete(request())
        with self.assertRaisesRegex(ChatModelError, "circuit breaker"):
            guarded.complete(request())
        self.assertEqual(fake.calls, 2)
        clock[0] = 15.0
        guarded.complete(request())
        self.assertEqual(fake.calls, 3)

    def test_rejects_concurrent_call_without_exceeding_provider_limit(self):
        entered = threading.Event()
        release = threading.Event()

        class BlockingProvider:
            def __init__(self):
                self.calls = 0

            def complete(self, request):
                self.calls += 1
                entered.set()
                if not release.wait(timeout=3):
                    raise TimeoutError("test provider release timed out")
                return ChatModelResponse("{}", "blocking", request.model)

        fake = BlockingProvider()
        guarded = GlobalModelCallGuard(fake, max_concurrent_calls=1)
        worker_errors = []

        def invoke():
            try:
                guarded.complete(request())
            except Exception as exc:
                worker_errors.append(exc)

        worker = threading.Thread(target=invoke)
        worker.start()
        self.assertTrue(entered.wait(timeout=2))
        try:
            with self.assertRaisesRegex(ChatModelError, "concurrency limit"):
                guarded.complete(request())
            self.assertEqual(fake.calls, 1)
        finally:
            release.set()
            worker.join(timeout=3)

        self.assertFalse(worker.is_alive())
        self.assertEqual(worker_errors, [])
        self.assertEqual(fake.calls, 1)

    def test_success_resets_consecutive_failure_count(self):
        fake = FakeProvider([ChatModelError("one"), None, ChatModelError("two")])
        guarded = GlobalModelCallGuard(fake, failure_threshold=2)
        with self.assertRaises(ChatModelError):
            guarded.complete(request())
        guarded.complete(request())
        with self.assertRaises(ChatModelError):
            guarded.complete(request())
        guarded.complete(request())
        self.assertEqual(fake.calls, 4)


if __name__ == "__main__":
    unittest.main()
