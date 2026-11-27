"""End-to-end database-to-review pipeline tests with a deterministic fake model."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from trendradar.market_data.ai_model_provider import ChatModelResponse
from trendradar.market_data.event_review_models import IMPACT_DIMENSIONS, REVIEWER_ROLES
from trendradar.market_data.event_review_pipeline import (
    review_persisted_market_event,
    review_persisted_market_event_with_runtime,
)
from trendradar.market_data.event_review_runtime import build_event_review_runtime
from trendradar.market_data.event_review_repository import EventReviewRepository
from trendradar.market_data.models import MarketEvent
from trendradar.market_data.storage import MarketStorage


class AllowTestGuard:
    """Allow AI requests only inside deterministic offline tests."""

    def check(self):
        return None



class DeterministicReviewProvider:
    provider_name = "pipeline-fake"

    def __init__(self, evidence_id):
        self.evidence_id = evidence_id
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        if "你是TrendRadar AI事件审查委员会的首席审查官" in request.system_prompt:
            payload = {
                "status": "accepted",
                "final_dimension_scores": {
                    "severity": 0.5, "scope": 0.5,
                    "duration": 0.25, "surprise": None,
                },
                "rationale": "四位审查员均引用同一可追溯官方来源，当前没有发现重大事实冲突；超预期维度保持未知。",
                "reviewer_roles_considered": list(REVIEWER_ROLES),
                "dissenting_views": [],
                "unresolved_issues": [],
                "required_followups": [],
            }
        else:
            payload = {
                "recommendation": "support",
                "dimension_scores": {
                    "severity": 0.5, "scope": 0.5,
                    "duration": 0.25, "surprise": None,
                },
                "dimension_rationales": {
                    dimension: "仅依据本次提供的可追溯事件材料作有限判断。"
                    for dimension in IMPACT_DIMENSIONS
                },
                "evidence_ids": [self.evidence_id],
                "confidence": 0.7,
                "summary": "已完成角色职责范围内的审查。",
                "concerns": [],
            }
        return ChatModelResponse(
            content=json.dumps(payload, ensure_ascii=False),
            provider=self.provider_name,
            model=request.model,
            request_id=f"fake-{len(self.requests)}",
            attempts=1,
            prompt_tokens=100 + len(self.requests),
            completion_tokens=20,
            total_tokens=120 + len(self.requests),
        )


class EventReviewPipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = MarketStorage(str(Path(self.tmp.name) / "market.db"))
        self.repository = EventReviewRepository(self.storage)
        self.event_id = "pipeline-event-1"
        self.source = "fed_monetary"
        self.url = "https://example.test/fed-event"
        self.storage.save_events([
            MarketEvent(
                event_id=self.event_id, source=self.source,
                title="Federal Reserve announces policy decision",
                published_at="2026-11-18T08:30:00-05:00",
                event_type="monetary_policy", direction="neutral",
                region="US", url=self.url,
                summary="Official policy statement for integration testing.",
            )
        ])
        digest = hashlib.sha256(
            f"{self.event_id}|{self.source}|{self.url}".encode("utf-8")
        ).hexdigest()[:24]
        self.evidence_id = "event-source-" + digest
        self.base = datetime(2026, 11, 18, 14, 0, tzinfo=timezone.utc)
        self.ticks = iter((self.base, self.base + timedelta(seconds=8)))

    def tearDown(self):
        self.tmp.cleanup()

    def test_reads_event_runs_five_roles_and_persists_passed_audit(self):
        provider = DeterministicReviewProvider(self.evidence_id)
        result = review_persisted_market_event(
            storage=self.storage,
            repository=self.repository,
            provider=provider,
            reviewer_model="fake/reviewer",
            chief_model="fake/chief",
            event_id=self.event_id,
            clock=lambda: next(self.ticks),
            review_run_id="pipeline-run-1",
        )

        self.assertEqual(result.execution.status, "adjudicated")
        self.assertEqual(result.persisted.run_status, "adjudicated")
        self.assertEqual(result.persisted.quality_result.status, "passed")
        self.assertTrue(result.persisted.quality_result.eligible_for_scoring)
        self.assertEqual(len(provider.requests), 5)
        self.assertEqual(
            [request.model for request in provider.requests],
            ["fake/reviewer"] * 4 + ["fake/chief"],
        )
        self.assertTrue(all(
            "仅表示文章发布时间" in request.user_prompt
            for request in provider.requests
        ))
        audit = self.repository.get_run(review_run_id="pipeline-run-1")
        self.assertEqual(audit.run["event_id"], self.event_id)
        self.assertEqual(len(audit.evidence), 1)
        self.assertEqual(len(audit.reviewer_assessments), 4)
        self.assertEqual(audit.adjudication["status"], "accepted")
        self.assertEqual(audit.quality_result["status"], "passed")
        self.assertEqual(len(audit.model_usage), 5)
        self.assertTrue(all(item["usage_complete"] == 1 for item in audit.model_usage))
        self.assertEqual(audit.model_usage[0]["prompt_tokens"], 101)
        self.assertEqual(audit.model_usage[-1]["reviewer_role"], "chief_reviewer")
        self.assertEqual(audit.model_usage[-1]["total_tokens"], 125)

    def test_guarded_runtime_pipeline_uses_per_event_budget_and_persists(self):
        fake = DeterministicReviewProvider(self.evidence_id)
        runtime = build_event_review_runtime(
            {"MODEL": "fake/reviewer", "API_KEY": "test-secret"},
            request_guard=AllowTestGuard(),
        )
        runtime.global_guard._provider = fake
        result = review_persisted_market_event_with_runtime(
            storage=self.storage, repository=self.repository, runtime=runtime,
            event_id=self.event_id, clock=lambda: next(self.ticks),
            review_run_id="guarded-runtime-run",
        )
        self.assertEqual(result.execution.status, "adjudicated")
        self.assertEqual(result.persisted.quality_result.status, "passed")
        self.assertEqual(len(fake.requests), 5)
        audit = self.repository.get_run(review_run_id="guarded-runtime-run")
        self.assertEqual(audit.run["run_status"], "adjudicated")

    def test_runtime_budget_exhaustion_blocks_and_persists_review(self):
        fake = DeterministicReviewProvider(self.evidence_id)
        runtime = build_event_review_runtime(
            {"MODEL": "fake/reviewer", "API_KEY": "test-secret"},
            request_guard=AllowTestGuard(),
            max_model_calls_per_event=2,
        )
        runtime.global_guard._provider = fake
        result = review_persisted_market_event_with_runtime(
            storage=self.storage, repository=self.repository, runtime=runtime,
            event_id=self.event_id, clock=lambda: next(self.ticks),
            review_run_id="guarded-budget-exhausted-run",
        )
        self.assertEqual(len(fake.requests), 2)
        self.assertEqual(result.execution.status, "incomplete")
        self.assertEqual(result.persisted.quality_result.status, "blocked")
        self.assertFalse(result.persisted.quality_result.eligible_for_scoring)
        audit = self.repository.get_run(
            review_run_id="guarded-budget-exhausted-run"
        )
        self.assertEqual(audit.run["run_status"], "incomplete")
        self.assertEqual(len(audit.reviewer_assessments), 4)

    def test_runtime_circuit_breaker_blocks_followup_provider_calls(self):
        fake = UnexpectedFailureProvider()
        runtime = build_event_review_runtime(
            {"MODEL": "fake/reviewer", "API_KEY": "test-secret"},
            request_guard=AllowTestGuard(),
            circuit_failure_threshold=1,
            circuit_cooldown_seconds=30,
        )
        runtime.global_guard._provider = fake
        result = review_persisted_market_event_with_runtime(
            storage=self.storage, repository=self.repository, runtime=runtime,
            event_id=self.event_id, clock=lambda: next(self.ticks),
            review_run_id="guarded-circuit-open-run",
        )
        self.assertEqual(fake.calls, 1)
        self.assertEqual(result.execution.status, "incomplete")
        self.assertEqual(result.persisted.quality_result.status, "blocked")
        self.assertFalse(result.persisted.quality_result.eligible_for_scoring)
        self.assertEqual(len(result.execution.reviewer_results), 4)
        audit = self.repository.get_run(
            review_run_id="guarded-circuit-open-run"
        )
        self.assertEqual(audit.quality_result["status"], "blocked")

    def test_guarded_runtime_rejects_non_runtime_argument(self):
        with self.assertRaisesRegex(TypeError, "EventReviewRuntime"):
            review_persisted_market_event_with_runtime(
                storage=self.storage, repository=self.repository,
                runtime=object(), event_id=self.event_id,
            )

    def test_rejects_repository_pointing_to_another_database(self):
        other_storage = MarketStorage(str(Path(self.tmp.name) / "other.db"))
        other_repository = EventReviewRepository(other_storage)
        provider = DeterministicReviewProvider(self.evidence_id)

        with self.assertRaisesRegex(ValueError, "same MarketStorage"):
            review_persisted_market_event(
                storage=self.storage,
                repository=other_repository,
                provider=provider,
                reviewer_model="fake/reviewer",
                chief_model="fake/chief",
                event_id=self.event_id,
            )
        self.assertEqual(provider.requests, [])



class UnexpectedFailureProvider:
    provider_name = "unexpected-failure"

    def __init__(self):
        self.calls = 0

    def complete(self, request):
        self.calls += 1
        raise RuntimeError("SDK internal error with sensitive request details")


class EventReviewPipelineFailurePersistenceTests(unittest.TestCase):
    def test_unexpected_provider_failures_are_persisted_as_incomplete(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = MarketStorage(str(Path(temp_dir) / "market.db"))
            repository = EventReviewRepository(storage)
            storage.save_events([
                MarketEvent(
                    event_id="provider-failure-event", source="unknown_feed",
                    title="Test event", published_at="2026-11-18T08:00:00Z",
                )
            ])
            ticks = iter((
                datetime(2026, 11, 18, 8, 0, tzinfo=timezone.utc),
                datetime(2026, 11, 18, 8, 1, tzinfo=timezone.utc),
            ))
            result = review_persisted_market_event(
                storage=storage, repository=repository,
                provider=UnexpectedFailureProvider(),
                reviewer_model="fake/reviewer", chief_model="fake/chief",
                event_id="provider-failure-event",
                clock=lambda: next(ticks),
                review_run_id="provider-failure-run",
            )

            self.assertEqual(result.execution.status, "incomplete")
            self.assertEqual(result.persisted.run_status, "incomplete")
            self.assertEqual(result.persisted.quality_result.status, "blocked")
            self.assertEqual(len(result.execution.reviewer_results), 4)
            self.assertTrue(all(
                item.error_code == "unexpected_execution_error"
                for item in result.execution.reviewer_results
            ))
            audit = repository.get_run(review_run_id="provider-failure-run")
            self.assertEqual(len(audit.reviewer_assessments), 4)
            self.assertEqual(audit.quality_result["status"], "blocked")
            self.assertNotIn(
                "sensitive",
                audit.reviewer_assessments[0]["concerns"][0],
            )


if __name__ == "__main__":
    unittest.main()
