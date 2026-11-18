"""Tests for atomic persistence of AI event-review board runs."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from trendradar.market_data.event_chief_reviewer_agent import ChiefReviewerExecutionResult
from trendradar.market_data.event_review_board_runner import EventReviewBoardExecutionResult
from trendradar.market_data.event_review_coordinator import assemble_event_review_board
from trendradar.market_data.event_review_models import (
    IMPACT_DIMENSIONS,
    REVIEWER_ROLES,
    ChiefAdjudication,
    ReviewEvidence,
    ReviewerAssessment,
)
from trendradar.market_data.event_review_repository import EventReviewRepository
from trendradar.market_data.event_reviewer_agent import ReviewerExecutionResult
from trendradar.market_data.models import MarketEvent
from trendradar.market_data.storage import MarketStorage


class EventReviewRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = MarketStorage(str(Path(self.tmp.name) / "market.db"))
        self.storage.save_events([
            MarketEvent("event-1", "test", "测试事件", "2026-11-18T09:00:00+08:00")
        ])
        self.repository = EventReviewRepository(self.storage)

    def tearDown(self):
        self.tmp.cleanup()

    def _execution(self, *, adjudicated=True):
        evidence = (
            ReviewEvidence(
                evidence_id="ev-primary", event_id="event-1", source="官方公告",
                source_url="https://example.org/notice", excerpt="官方事件证据",
                evidence_kind="primary", published_at="2026-11-18T08:30:00+08:00",
                captured_at="2026-11-18T09:01:00+08:00",
                supports_dimensions=("severity", "scope", "duration"),
            ),
        )
        assessments = tuple(
            ReviewerAssessment(
                event_id="event-1", reviewer_role=role,
                recommendation="support", completion_status="complete",
                dimension_scores=tuple(
                    (dimension, None if dimension == "surprise" else 0.5)
                    for dimension in IMPACT_DIMENSIONS
                ),
                dimension_rationales=tuple(
                    (dimension, "基于当前证据的审慎判断")
                    for dimension in IMPACT_DIMENSIONS
                ),
                evidence_ids=("ev-primary",), confidence=0.8,
                summary="审查完成", model_provider="test-provider",
                model_name="test-model", model_version="test-model-v1",
                prompt_version="reviewer-test-v1",
            )
            for role in REVIEWER_ROLES
        )
        adjudication = None
        if adjudicated:
            adjudication = ChiefAdjudication(
                event_id="event-1", status="accepted",
                final_dimension_scores=tuple(
                    (dimension, None if dimension == "surprise" else 0.5)
                    for dimension in IMPACT_DIMENSIONS
                ),
                rationale="证据支持当前审慎结论",
                reviewer_roles_considered=REVIEWER_ROLES,
                chair_model_provider="test-provider",
                chair_model_name="chair-model",
                chair_model_version="chair-model-v1",
                prompt_version="chair-test-v1",
            )
        board = assemble_event_review_board(
            event_id="event-1", evidence=evidence,
            reviewer_assessments=assessments, adjudication=adjudication,
        )
        reviewer_results = tuple(
            ReviewerExecutionResult(assessment=item) for item in assessments
        )
        chief_result = (
            ChiefReviewerExecutionResult(adjudication=adjudication)
            if adjudicated else None
        )
        return EventReviewBoardExecutionResult(
            board=board, reviewer_results=reviewer_results,
            chief_result=chief_result,
        )

    def test_persists_complete_run_and_all_child_records_atomically(self):
        result = self.repository.save_run(
            execution=self._execution(),
            event_payload={"event_time": "2026-11-18T09:00:00+08:00"},
            started_at="2026-11-18T09:02:00+08:00",
            completed_at="2026-11-18T09:03:00+08:00",
            review_run_id="run-accepted",
        )
        self.assertEqual(result.run_status, "adjudicated")
        self.assertEqual(result.board_status, "adjudicated")
        self.assertEqual(result.quality_result.status, "passed")
        with self.storage.connect() as connection:
            run = connection.execute(
                "SELECT * FROM event_review_runs WHERE review_run_id='run-accepted'"
            ).fetchone()
            self.assertEqual(run["run_status"], "adjudicated")
            self.assertEqual(run["rubric_version"], "event-impact-rubric-v1")
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM event_review_evidence WHERE review_run_id='run-accepted'"
            ).fetchone()[0], 1)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM event_reviewer_assessments WHERE review_run_id='run-accepted'"
            ).fetchone()[0], 4)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM event_review_adjudications WHERE review_run_id='run-accepted'"
            ).fetchone()[0], 1)
            quality = connection.execute(
                "SELECT status,eligible_for_scoring,reasons_json "
                "FROM event_review_quality_results WHERE review_run_id='run-accepted'"
            ).fetchone()
            self.assertEqual(tuple(quality), ("passed", 1, "[]"))
            scores = json.loads(connection.execute(
                "SELECT final_dimension_scores_json FROM event_review_adjudications "
                "WHERE review_run_id='run-accepted'"
            ).fetchone()[0])
            self.assertIsNone(scores["surprise"])

    def test_persists_incomplete_run_as_blocked_without_chair_row(self):
        execution = self._execution(adjudicated=False)
        # Simulate an incomplete board with one reviewer execution failure.
        failed = execution.reviewer_results[-1]
        from dataclasses import replace
        failed_assessment = replace(
            failed.assessment, completion_status="error",
            recommendation="abstain", evidence_ids=(),
            concerns=("provider unavailable",),
        )
        reviewer_results = execution.reviewer_results[:-1] + (
            ReviewerExecutionResult(
                assessment=failed_assessment,
                error_code="provider_error",
                error_message="provider unavailable",
            ),
        )
        board = assemble_event_review_board(
            event_id="event-1", evidence=execution.board.evidence,
            reviewer_assessments=tuple(item.assessment for item in reviewer_results),
        )
        execution = EventReviewBoardExecutionResult(
            board=board, reviewer_results=reviewer_results, chief_result=None,
        )
        result = self.repository.save_run(
            execution=execution, event_payload={},
            started_at="2026-11-18T09:02:00+08:00",
            completed_at="2026-11-18T09:03:00+08:00",
            review_run_id="run-incomplete",
        )
        self.assertEqual(result.run_status, "incomplete")
        self.assertEqual(result.quality_result.status, "blocked")
        with self.storage.connect() as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM event_review_adjudications WHERE review_run_id='run-incomplete'"
            ).fetchone()[0], 0)
            run = connection.execute(
                "SELECT error_code FROM event_review_runs WHERE review_run_id='run-incomplete'"
            ).fetchone()
            self.assertIn("provider_error", run["error_code"])
            quality = connection.execute(
                "SELECT eligible_for_scoring FROM event_review_quality_results "
                "WHERE review_run_id='run-incomplete'"
            ).fetchone()
            self.assertEqual(quality["eligible_for_scoring"], 0)

    def test_duplicate_run_id_rolls_back_without_mutating_first_run(self):
        args = dict(
            execution=self._execution(), event_payload={},
            started_at="2026-11-18T09:02:00+08:00",
            completed_at="2026-11-18T09:03:00+08:00",
            review_run_id="run-unique",
        )
        self.repository.save_run(**args)
        with self.assertRaises(Exception):
            self.repository.save_run(**args)
        with self.storage.connect() as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM event_review_runs WHERE review_run_id='run-unique'"
            ).fetchone()[0], 1)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM event_review_evidence WHERE review_run_id='run-unique'"
            ).fetchone()[0], 1)

    def test_lists_event_history_newest_first_with_quality_summary(self):
        base = dict(
            execution=self._execution(), event_payload={},
            completed_at="2026-11-18T09:03:00+08:00",
        )
        self.repository.save_run(
            **base, started_at="2026-11-18T09:02:00+08:00",
            review_run_id="run-older",
        )
        self.repository.save_run(
            **base, started_at="2026-11-18T10:02:00+08:00",
            review_run_id="run-newer",
        )
        history = self.repository.list_runs(event_id="event-1", limit=10)
        self.assertEqual(
            [item.review_run_id for item in history],
            ["run-newer", "run-older"],
        )
        self.assertTrue(all(item.eligible_for_scoring for item in history))
        self.assertTrue(all(item.quality_status == "passed" for item in history))
        self.assertEqual(
            [item.review_run_id for item in self.repository.list_runs(
                event_id="event-1", limit=1, offset=1
            )],
            ["run-older"],
        )

    def test_loads_audit_record_with_decoded_evidence_reviews_and_chair(self):
        self.repository.save_run(
            execution=self._execution(), event_payload={},
            started_at="2026-11-18T09:02:00+08:00",
            completed_at="2026-11-18T09:03:00+08:00",
            review_run_id="run-audit",
        )
        record = self.repository.get_run(review_run_id="run-audit")
        self.assertIsNotNone(record)
        self.assertEqual(record.run["event_id"], "event-1")
        self.assertEqual(len(record.evidence), 1)
        self.assertEqual(record.evidence[0]["supports_dimensions"], [
            "severity", "scope", "duration"
        ])
        self.assertEqual(len(record.reviewer_assessments), 4)
        fact_checker = next(
            item for item in record.reviewer_assessments
            if item["reviewer_role"] == "fact_checker"
        )
        self.assertEqual(fact_checker["evidence_ids"], ["ev-primary"])
        self.assertEqual(
            fact_checker["dimension_scores"]["surprise"], None
        )
        self.assertEqual(record.adjudication["status"], "accepted")
        self.assertEqual(record.adjudication["reviewer_roles_considered"], list(REVIEWER_ROLES))
        self.assertEqual(record.quality_result["status"], "passed")
        self.assertTrue(record.quality_result["eligible_for_scoring"])

    def test_audit_lookup_returns_none_for_unknown_run_and_validates_paging(self):
        self.assertIsNone(self.repository.get_run(review_run_id="not-found"))
        self.assertEqual(self.repository.list_runs(event_id="event-1"), ())
        with self.assertRaisesRegex(ValueError, "limit"):
            self.repository.list_runs(event_id="event-1", limit=0)
        with self.assertRaisesRegex(ValueError, "offset"):
            self.repository.list_runs(event_id="event-1", offset=-1)
        with self.assertRaisesRegex(ValueError, "event_id"):
            self.repository.list_runs(event_id=" ")

    def test_rejects_naive_or_reversed_run_timestamps_before_writing(self):
        execution = self._execution()
        with self.assertRaisesRegex(ValueError, "timezone"):
            self.repository.save_run(
                execution=execution, event_payload={},
                started_at="2026-11-18T09:02:00",
                completed_at="2026-11-18T09:03:00+08:00",
                review_run_id="run-naive",
            )
        with self.assertRaisesRegex(ValueError, "earlier"):
            self.repository.save_run(
                execution=execution, event_payload={},
                started_at="2026-11-18T09:03:00+08:00",
                completed_at="2026-11-18T09:02:00+08:00",
                review_run_id="run-reversed",
            )
        with self.storage.connect() as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM event_review_runs"
            ).fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
