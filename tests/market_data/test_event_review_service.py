"""Tests for the single-event review execution and persistence service."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from trendradar.market_data.event_review_board_runner import EventReviewBoardExecutionResult
from trendradar.market_data.event_review_coordinator import assemble_event_review_board
from trendradar.market_data.event_review_repository import EventReviewRepository
from trendradar.market_data.models import MarketEvent
from trendradar.market_data.event_review_service import run_and_persist_event_review
from trendradar.market_data.storage import MarketStorage


class EventReviewServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = MarketStorage(str(Path(self.tmp.name) / "market.db"))
        self.repository = EventReviewRepository(self.storage)
        self.storage.save_events([
            MarketEvent("event-service", "test", "测试事件", "2026-11-18T09:00:00+08:00")
        ])
        self.base = datetime(2026, 11, 18, 9, 0, tzinfo=timezone.utc)
        self.ticks = iter((self.base, self.base + timedelta(seconds=5)))

    def tearDown(self):
        self.tmp.cleanup()

    def _incomplete_execution(self):
        board = assemble_event_review_board(
            event_id="event-service", evidence=(), reviewer_assessments=()
        )
        return EventReviewBoardExecutionResult(
            board=board, reviewer_results=(), chief_result=None
        )

    def test_executes_once_then_persists_blocked_outcome(self):
        execution = self._incomplete_execution()
        with patch(
            "trendradar.market_data.event_review_service.run_event_review_board",
            return_value=execution,
        ) as run_board:
            result = run_and_persist_event_review(
                provider=object(), repository=self.repository,
                reviewer_model="reviewer-test", chief_model="chair-test",
                event_id="event-service", event_payload={"event_time": "2026-11-18T09:00:00Z"},
                evidence=(), clock=lambda: next(self.ticks),
                review_run_id="service-run-1",
            )

        run_board.assert_called_once()
        self.assertEqual(result.execution, execution)
        self.assertEqual(result.persisted.review_run_id, "service-run-1")
        self.assertEqual(result.persisted.run_status, "incomplete")
        self.assertEqual(result.persisted.quality_result.status, "blocked")
        audit = self.repository.get_run(review_run_id="service-run-1")
        self.assertIsNotNone(audit)
        self.assertEqual(audit.run["event_id"], "event-service")
        self.assertEqual(audit.quality_result["status"], "blocked")

    def test_rejects_evidence_for_another_event_before_model_call(self):
        from trendradar.market_data.event_review_models import ReviewEvidence
        evidence = (
            ReviewEvidence(
                evidence_id="ev-other", event_id="another-event",
                source="source", source_url="", excerpt="excerpt",
                evidence_kind="context", published_at="2026-11-18T08:00:00Z",
                captured_at="2026-11-18T08:01:00Z",
                supports_dimensions=("scope",),
            ),
        )
        with patch(
            "trendradar.market_data.event_review_service.run_event_review_board"
        ) as run_board:
            with self.assertRaisesRegex(ValueError, "match event_id"):
                run_and_persist_event_review(
                    provider=object(), repository=self.repository,
                    reviewer_model="reviewer-test", chief_model="chair-test",
                    event_id="event-service", event_payload={}, evidence=evidence,
                    clock=lambda: next(self.ticks),
                )
        run_board.assert_not_called()
        with self.storage.connect() as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM event_review_runs"
            ).fetchone()[0], 0)

    def test_requires_timezone_aware_clock(self):
        with patch(
            "trendradar.market_data.event_review_service.run_event_review_board"
        ) as run_board:
            with self.assertRaisesRegex(ValueError, "timezone-aware"):
                run_and_persist_event_review(
                    provider=object(), repository=self.repository,
                    reviewer_model="reviewer-test", chief_model="chair-test",
                    event_id="event-service", event_payload={}, evidence=(),
                    clock=lambda: datetime(2026, 11, 18, 9, 0),
                )
        run_board.assert_not_called()


if __name__ == "__main__":
    unittest.main()
