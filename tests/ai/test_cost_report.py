import unittest
from decimal import Decimal

from trendradar.ai.cost_report import build_cost_report, estimate_usage_record


class CostReportTests(unittest.TestCase):
    def record(self, **overrides):
        values = {
            "usage_id": 1,
            "feature": "event_review",
            "operation": "review",
            "provider": "litellm",
            "model": "deepseek/deepseek-v4-pro",
            "request_status": "success",
            "prompt_tokens": 1_000_000,
            "completion_tokens": 100_000,
            "prompt_cache_hit_tokens": 200_000,
            "prompt_cache_miss_tokens": 800_000,
            "started_at": "2026-11-19T01:00:00+00:00",
        }
        values.update(overrides)
        return values

    def test_estimates_cost_from_ledger_row_at_peak_time(self):
        row = estimate_usage_record(self.record())
        self.assertEqual("calculated", row.estimate.status)
        self.assertEqual("2026-11-19", row.local_date.isoformat())
        self.assertEqual(Decimal("9.96"), row.estimate.total_cost)

    def test_aggregates_calculated_rows_by_day_feature_and_model(self):
        first = self.record()
        second = self.record(
            usage_id=2,
            feature="translation",
            # 12:00 Beijing is off-peak.
            started_at="2026-11-19T04:00:00+00:00",
        )
        report = build_cost_report([first, second])
        self.assertEqual(2, report.aggregate.calculated_count)
        self.assertEqual(Decimal("14.94"), report.aggregate.total_cost)
        self.assertEqual(Decimal("14.94"), report.aggregate.by_day["2026-11-19"])
        self.assertEqual(Decimal("9.96"), report.aggregate.by_feature["event_review"])
        self.assertEqual(Decimal("4.98"), report.aggregate.by_feature["translation"])
        self.assertEqual(Decimal("14.94"), report.aggregate.by_model["deepseek-v4-pro"])

    def test_partial_and_unsupported_rows_do_not_inflate_total(self):
        partial = self.record(
            usage_id=2,
            prompt_cache_hit_tokens=None,
            prompt_cache_miss_tokens=None,
        )
        unsupported = self.record(usage_id=3, model="some-other-model")
        report = build_cost_report([self.record(), partial, unsupported])
        self.assertEqual(1, report.aggregate.calculated_count)
        self.assertEqual(1, report.aggregate.partial_count)
        self.assertEqual(1, report.aggregate.unsupported_count)
        self.assertEqual(Decimal("9.96"), report.aggregate.total_cost)

    def test_rejects_naive_ledger_timestamp(self):
        with self.assertRaises(ValueError):
            estimate_usage_record(self.record(started_at="2026-11-19T09:00:00"))

    def test_empty_report_is_zero_and_well_formed(self):
        report = build_cost_report([])
        self.assertEqual(0, report.aggregate.calculated_count)
        self.assertEqual(Decimal("0"), report.aggregate.total_cost)
        self.assertEqual({}, report.aggregate.by_day)


if __name__ == "__main__":
    unittest.main()
