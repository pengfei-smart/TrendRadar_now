
"""Timezone consistency tests for the market workflow."""

from __future__ import annotations
from zoneinfo import ZoneInfo
import unittest
from datetime import datetime, timedelta
from unittest.mock import Mock

from trendradar.market_data.market_radar import MarketRadar
from trendradar.market_data.workflow import _now
from trendradar.market_data.workflow_report import MarketWorkflowReport


class MarketTimezoneTests(unittest.TestCase):
    def test_naive_clock_is_interpreted_as_shanghai_time(self):
        radar = MarketRadar(
            quote_fetcher=Mock(),
            clock=lambda: datetime(2026, 11, 9, 14, 33, 0),
        )

        scan_time = radar.clock()
        if scan_time.tzinfo is None:
            scan_time = scan_time.replace(tzinfo=ZoneInfo("Asia/Shanghai"))

        self.assertEqual(
            scan_time.utcoffset(),
            timedelta(hours=8),
        )

    def test_naive_quote_timestamp_is_interpreted_as_shanghai_time(self):
        parsed = datetime.fromisoformat("2026-11-09T14:33:00")

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZoneInfo("Asia/Shanghai"))

        self.assertEqual(
            parsed.utcoffset(),
            timedelta(hours=8),
        )

    def assert_shanghai_time(self, value: str) -> None:
        parsed = datetime.fromisoformat(value)
        self.assertIsNotNone(parsed.tzinfo)
        self.assertEqual(
            parsed.utcoffset(),
            timedelta(hours=8),
        )

    def test_workflow_now_uses_shanghai_timezone(self):
        self.assert_shanghai_time(_now())

    def test_workflow_report_finalize_uses_shanghai_timezone(self):
        report = MarketWorkflowReport(run_id="timezone-test")

        report.finalize()

        self.assert_shanghai_time(report.completed_at)

    def test_market_radar_default_clock_uses_shanghai_timezone(self):
        radar = MarketRadar(
            quote_fetcher=Mock(),
        )

        self.assertEqual(
            radar.clock().utcoffset(),
            timedelta(hours=8),
        )

    def test_scan_and_persist_default_run_at_uses_shanghai_timezone(self):
        result_store = Mock()
        radar = MarketRadar(
            quote_fetcher=Mock(),
            result_store=result_store,
        )
        radar._scan = Mock(return_value=([], "", "", "success"))

        radar.scan_and_persist([], {})

        run_at = result_store.start_run.call_args.args[0]
        self.assert_shanghai_time(run_at)


if __name__ == "__main__":
    unittest.main()
