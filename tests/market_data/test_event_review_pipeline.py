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
from trendradar.market_data.event_review_pipeline import review_persisted_market_event
from trendradar.market_data.event_review_repository import EventReviewRepository
from trendradar.market_data.models import MarketEvent
from trendradar.market_data.storage import MarketStorage


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


if __name__ == "__main__":
    unittest.main()
