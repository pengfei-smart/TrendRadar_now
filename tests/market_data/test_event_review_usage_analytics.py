"""Tests for read-only AI event-review usage analytics."""
from pathlib import Path
import tempfile
import unittest

from trendradar.market_data.event_review_usage_analytics import (
    get_event_review_usage_report,
)
from trendradar.market_data.models import MarketEvent
from trendradar.market_data.storage import MarketStorage


class EventReviewUsageAnalyticsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = MarketStorage(str(Path(self.tmp.name) / "market.db"))
        self.storage.save_events([
            MarketEvent("event-a", "test", "事件A", "2026-11-18"),
            MarketEvent("event-b", "test", "事件B", "2026-11-18"),
        ])
        with self.storage.connect() as connection:
            connection.executemany(
                """INSERT INTO event_review_runs
                (review_run_id,event_id,run_status,board_status,started_at,completed_at)
                VALUES(?,?,'incomplete','incomplete',?,?)""",
                [
                    ("run-a", "event-a", "2026-11-18T09:00:00+08:00", "2026-11-18T09:01:00+08:00"),
                    ("run-b", "event-b", "2026-11-19T09:00:00+08:00", "2026-11-19T09:01:00+08:00"),
                ],
            )
            connection.executemany(
                """INSERT INTO event_review_model_usage
                (review_run_id,event_id,reviewer_role,request_status,provider,model,
                 attempts,prompt_tokens,completion_tokens,total_tokens,usage_complete,
                 call_started_at,call_completed_at,error_code)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    ("run-a","event-a","fact_checker","success","p","model-a",1,100,20,120,1,
                     "2026-11-18T09:00:10+08:00","2026-11-18T09:00:11+08:00",""),
                    ("run-a","event-a","macro_policy_analyst","success","p","model-a",1,80,None,80,0,
                     "2026-11-18T09:00:12+08:00","2026-11-18T09:00:13+08:00",""),
                    ("run-b","event-b","fact_checker","error","p","model-b",1,None,None,None,0,
                     "2026-11-19T09:00:10+08:00","2026-11-19T09:00:11+08:00","provider_error"),
                ],
            )

    def tearDown(self):
        self.tmp.cleanup()

    def test_aggregates_totals_and_breakdowns_without_inventing_missing_usage(self):
        report = get_event_review_usage_report(self.storage)
        self.assertEqual(report.totals.calls, 3)
        self.assertEqual(report.totals.successful_calls, 2)
        self.assertEqual(report.totals.failed_calls, 1)
        self.assertEqual(report.totals.complete_usage_calls, 1)
        self.assertEqual(report.totals.incomplete_usage_calls, 2)
        self.assertEqual(report.totals.missing_usage_calls, 1)
        self.assertEqual(report.totals.partial_usage_calls, 1)
        self.assertEqual(report.totals.prompt_tokens, 180)
        self.assertEqual(report.totals.completion_tokens, 20)
        self.assertEqual(report.totals.total_tokens, 200)
        self.assertEqual(report.totals.prompt_token_observations, 2)
        self.assertEqual([item.key for item in report.by_role], [
            "fact_checker", "macro_policy_analyst",
        ])
        self.assertEqual([item.key for item in report.by_model], ["model-a", "model-b"])
        self.assertEqual([item.key for item in report.by_event], ["event-a", "event-b"])
        self.assertEqual([item.key for item in report.by_day], ["2026-11-18", "2026-11-19"])
        self.assertEqual(report.by_role[0].totals.calls, 2)

    def test_filters_by_event_and_half_open_time_range(self):
        report = get_event_review_usage_report(
            self.storage, event_id="event-a",
            start_at="2026-11-18T01:00:00Z",
            end_at="2026-11-18T01:00:12Z",
        )
        self.assertEqual(report.totals.calls, 1)
        self.assertEqual(report.totals.total_tokens, 120)
        self.assertEqual(report.event_id, "event-a")

    def test_empty_report_returns_zero_totals(self):
        report = get_event_review_usage_report(
            self.storage, event_id="does-not-exist",
        )
        self.assertEqual(report.totals.calls, 0)
        self.assertEqual(report.by_role, ())
        self.assertEqual(report.by_model, ())

    def test_rejects_invalid_filters_and_limits(self):
        with self.assertRaisesRegex(ValueError, "timezone"):
            get_event_review_usage_report(self.storage, start_at="2026-11-18T09:00:00")
        with self.assertRaisesRegex(ValueError, "later"):
            get_event_review_usage_report(
                self.storage, start_at="2026-11-19T00:00:00Z",
                end_at="2026-11-18T00:00:00Z",
            )
        with self.assertRaisesRegex(ValueError, "limit"):
            get_event_review_usage_report(self.storage, limit=0)


if __name__ == "__main__":
    unittest.main()
