"""Tests for traceable event-review model cost analytics."""
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from trendradar.market_data.event_review_cost_analytics import (
    get_event_review_cost_report,
)
from trendradar.market_data.event_review_pricing_repository import (
    EventReviewPricingRepository,
)
from trendradar.market_data.models import MarketEvent
from trendradar.market_data.storage import MarketStorage


class EventReviewCostAnalyticsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = MarketStorage(str(Path(self.tmp.name) / "market.db"))
        self.storage.save_events([
            MarketEvent("event-a", "test", "事件A", "2026-11-19"),
        ])
        self.pricing = EventReviewPricingRepository(self.storage)
        self.pricing.add_price(
            provider="openai",
            model="openai/model-x",
            currency="USD",
            input_price_per_million="2.5",
            output_price_per_million="10",
            valid_from="2026-11-01T00:00:00Z",
            valid_until=None,
            source_url="https://provider.example/pricing",
            source_checked_at="2026-11-19T00:00:00Z",
            pricing_id="price-openai-x",
        )
        self._insert_usage_rows([
            ("run-1", "fact_checker", "openai/model-x", 100, 20, "success"),
            ("run-2", "macro_policy_analyst", "openai/model-x", 80, None, "success"),
            ("run-3", "industry_chain_analyst", "openai/model-x", None, None, "error"),
            ("run-4", "market_expectations_analyst", "openai/model-y", 100, 10, "success"),
            ("run-5", "chief_reviewer", "openai/model-x", 100, 10, "success"),
        ])

    def tearDown(self):
        self.tmp.cleanup()

    def _insert_usage_rows(self, values):
        with self.storage.connect() as connection:
            for run_id, role, model, prompt, completion, status in values:
                connection.execute(
                    """INSERT INTO event_review_runs
                    (review_run_id,event_id,run_status,board_status,started_at,completed_at)
                    VALUES(?, 'event-a', 'incomplete', 'incomplete',
                           '2026-11-19T09:00:00+08:00','2026-11-19T09:01:00+08:00')""",
                    (run_id,),
                )
                connection.execute(
                    """INSERT INTO event_review_model_usage
                    (review_run_id,event_id,reviewer_role,request_status,provider,model,
                     prompt_tokens,completion_tokens,total_tokens,usage_complete,
                     call_started_at,call_completed_at,error_code)
                    VALUES(?, 'event-a', ?, ?, 'litellm', ?, ?, ?, NULL, 0,
                           '2026-11-19T09:00:10+08:00',
                           '2026-11-19T09:00:11+08:00', '')""",
                    (run_id, role, status, model, prompt, completion),
                )

    def test_calculates_exact_decimal_costs_and_preserves_partial_usage(self):
        report = get_event_review_cost_report(
            self.storage,
            self.pricing,
            provider_mapping={("litellm", "openai/model-x"): "openai"},
        )
        self.assertEqual(len(report.lines), 5)
        self.assertEqual(report.totals_by_currency[0].currency, "USD")
        totals = report.totals_by_currency[0].totals
        self.assertEqual(totals.calls, 5)
        self.assertEqual(totals.calculated_calls, 1)
        self.assertEqual(totals.partial_usage_calls, 1)
        self.assertEqual(totals.missing_usage_calls, 1)
        self.assertEqual(totals.missing_pricing_calls, 1)
        self.assertEqual(totals.unresolved_provider_calls, 1)
        complete = next(line for line in report.lines if line.cost_status == "calculated")
        self.assertEqual(complete.known_cost, Decimal("0.00045"))
        self.assertEqual(complete.complete_cost, Decimal("0.00045"))
        partial = next(line for line in report.lines if line.cost_status == "partial_usage")
        self.assertEqual(partial.known_cost, Decimal("0.0002"))
        self.assertIsNone(partial.complete_cost)

    def test_provider_route_must_be_explicit_and_exact(self):
        report = get_event_review_cost_report(
            self.storage, self.pricing, provider_mapping={},
        )
        self.assertTrue(all(line.cost_status in {"missing_usage", "unresolved_provider"} for line in report.lines))
        unresolved = next(line for line in report.lines if line.cost_status == "unresolved_provider")
        self.assertIsNone(unresolved.known_cost)
        self.assertEqual(unresolved.billing_provider, "")

    def test_time_and_event_filters_are_half_open(self):
        report = get_event_review_cost_report(
            self.storage,
            self.pricing,
            provider_mapping={("litellm", "openai/model-x"): "openai"},
            event_id="event-a",
            start_at="2026-11-19T01:00:10Z",
            end_at="2026-11-19T01:00:11Z",
        )
        self.assertEqual(len(report.lines), 5)
        self.assertEqual(report.event_id, "event-a")

    def test_limit_caps_breakdown_rows_not_cost_totals_or_call_lines(self):
        report = get_event_review_cost_report(
            self.storage,
            self.pricing,
            provider_mapping={("litellm", "openai/model-x"): "openai"},
            limit=1,
        )
        self.assertEqual(report.totals_by_currency[0].totals.calls, 5)
        self.assertEqual(len(report.lines), 5)
        self.assertEqual(len(report.by_role), 1)

    def test_invalid_call_timestamp_is_not_priced(self):
        with self.storage.connect() as connection:
            connection.execute(
                """INSERT INTO event_review_runs
                (review_run_id,event_id,run_status,board_status,started_at,completed_at)
                VALUES('run-invalid-time','event-a','incomplete','incomplete',
                       '2026-11-19T09:00:00+08:00','2026-11-19T09:01:00+08:00')"""
            )
            connection.execute(
                """INSERT INTO event_review_model_usage
                (review_run_id,event_id,reviewer_role,request_status,provider,model,
                 prompt_tokens,completion_tokens,total_tokens,usage_complete,
                 call_started_at,call_completed_at,error_code)
                VALUES('run-invalid-time','event-a','fact_checker','success','litellm',
                       'openai/model-x',100,20,120,1,
                       '2026-11-19T09:00:10','2026-11-19T09:00:11','')"""
            )
        report = get_event_review_cost_report(
            self.storage, self.pricing,
            provider_mapping={("litellm", "openai/model-x"): "openai"},
        )
        invalid = next(line for line in report.lines if line.cost_status == "invalid_timestamp")
        self.assertIsNone(invalid.complete_cost)
        unknown_currency = next(item for item in report.totals_by_currency if item.currency is None)
        self.assertEqual(unknown_currency.totals.invalid_timestamp_calls, 1)

    def test_rejects_invalid_provider_mapping_filters_and_repository(self):
        with self.assertRaisesRegex(ValueError, "provider_mapping"):
            get_event_review_cost_report(
                self.storage, self.pricing, provider_mapping={"litellm": "openai"},
            )
        with self.assertRaisesRegex(ValueError, "timezone"):
            get_event_review_cost_report(
                self.storage, self.pricing, provider_mapping={}, start_at="2026-11-19",
            )
        other = MarketStorage(str(Path(self.tmp.name) / "other.db"))
        with self.assertRaisesRegex(ValueError, "same MarketStorage"):
            get_event_review_cost_report(
                self.storage, EventReviewPricingRepository(other), provider_mapping={},
            )


if __name__ == "__main__":
    unittest.main()
