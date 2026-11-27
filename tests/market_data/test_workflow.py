
"""Offline tests for the independent market workflow orchestration."""

from __future__ import annotations

import tempfile
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import Mock, patch
from trendradar.market_data.models import MarketEvent, Security
from trendradar.market_data.workflow import run_market_workflow

_DEFAULT_QUOTE_FETCHER = object()
from trendradar.market_data.workflow_context import MarketWorkflowContext


class FakeFetcher:
    def __init__(self, events=None, error=None):
        self.events = list(events or [])
        self.error = error

    def fetch(self, *, limit_per_feed):
        if self.error:
            raise self.error
        return self.events


class MarketWorkflowTests(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = f"{self.temp_dir.name}/market.db"

        self.guard = Mock()
        self.guard.is_allowed.return_value = False

        self.app_context = Mock()
        self.app_context.get_ai_request_guard.return_value = self.guard

        with patch(
            "trendradar.market_data.workflow_context.AppContext",
            return_value=self.app_context,
        ):
            self.context = MarketWorkflowContext(
                {"STORAGE": {"BACKEND": "local"}},
                db_path=self.db_path,
                run_id="workflow-test",
            )

        self.storage = Mock()
        self.storage.save_events.side_effect = (
            lambda events: len(events)
        )
        self.storage.load_securities.return_value = [
            Security(
                code="600000",
                name="浦发银行",
                exchange="SH",
                listing_status="active",
            )
        ]

        self.lifecycle = Mock()
        self.lifecycle.storage = self.storage
        self.lifecycle.start.return_value = {
            "status": "success",
            "run_id": "workflow-test",
        }
        self.lifecycle.finish.return_value = {
            "status": "success",
            "persistence": "local_only",
        }

        self.repository = Mock()
        self.repository.list_runs.return_value = []

        self.radar = Mock()
        self.radar.scan_and_persist_with_run.return_value = (
            ["candidate"],
            1,
        )

        self.event = MarketEvent(
            event_id="event-001",
            source="test",
            title="Test market event",
            published_at="2026-11-23T10:00:00+08:00",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def run_workflow(
        self,
        *,
        us_fetcher=None,
        macro_fetcher=None,
        pipeline_error=None,
        radar_status="success",
        quote_fetcher=_DEFAULT_QUOTE_FETCHER,
        runtime_error=None,
        runtime_factory=None,
    ):
        with ExitStack() as stack:
            stack.enter_context(
                patch(
                    "trendradar.market_data.workflow.MarketDatabaseLifecycle",
                    return_value=self.lifecycle,
                )
            )
            stack.enter_context(
                patch(
                    "trendradar.market_data.workflow.EventReviewRepository",
                    return_value=self.repository,
                )
            )
            runtime_factory = runtime_factory or Mock()
            runtime_getter = stack.enter_context(
                patch.object(
                    self.context,
                    "get_event_review_runtime",
                    return_value=runtime_factory,
                    side_effect=runtime_error,
                )
            )
            self.runtime_getter_mock = runtime_getter

            result_store = Mock()
            result_store.get_run.return_value = {
                "run": {
                    "status": radar_status,
                    "run_id": 1,
                    "candidate_count": 1,
                    "pushed_count": 1,
                },
                "candidates": [],
            }

            stack.enter_context(
                patch(
                    "trendradar.market_data.workflow.RadarResultStore",
                    return_value=result_store,
                )
            )
            stack.enter_context(
                patch(
                    "trendradar.market_data.workflow.CrossMarketRelationStore",
                    return_value=Mock(),
                )
            )
            stack.enter_context(
                patch(
                    "trendradar.market_data.workflow.MarketRadar",
                    return_value=self.radar,
                )
            )

            pipeline = stack.enter_context(
                patch(
                    "trendradar.market_data.workflow."
                    "review_persisted_market_event_with_runtime"
                )
            )

            if pipeline_error:
                pipeline.side_effect = pipeline_error

            quote_factory = stack.enter_context(
                patch(
                    "trendradar.market_data.workflow.QuoteFetcher"
                )
            )
            self.quote_fetcher_factory = quote_factory

            supplied_quote_fetcher = (
                Mock()
                if quote_fetcher is _DEFAULT_QUOTE_FETCHER
                else quote_fetcher
            )

            report = run_market_workflow(
                context=self.context,
                us_fetcher=us_fetcher or FakeFetcher([self.event]),
                macro_fetcher=macro_fetcher or FakeFetcher(),
                quote_fetcher=supplied_quote_fetcher,
            )

        return report, pipeline

    def test_ai_denied_still_runs_deterministic_radar(self):
        report, pipeline = self.run_workflow()

        self.assertEqual(report["status"], "success")
        self.assertEqual(
            report["metadata"]["event_review"]["status"],
            "skipped",
        )
        self.assertTrue(
            report["metadata"]["event_review"]["guard_denied"]
        )

        self.storage.save_events.assert_called_once()
        self.radar.scan_and_persist_with_run.assert_called_once()
        self.lifecycle.finish.assert_called_once()
        self.lifecycle.abort.assert_not_called()
        pipeline.assert_not_called()

    def test_scheduler_exception_fails_closed_without_runtime_initialization(self):
        self.guard.is_allowed.side_effect = RuntimeError(
            "scheduler unavailable"
        )
        runtime_factory = Mock()

        report, pipeline = self.run_workflow(
            runtime_factory=runtime_factory,
        )

        self.assertEqual(report["status"], "success")
        self.assertEqual(
            report["metadata"]["event_review"]["status"],
            "skipped",
        )
        self.assertTrue(
            report["metadata"]["event_review"]["guard_denied"]
        )
        runtime_factory.assert_not_called()
        pipeline.assert_not_called()
        self.radar.scan_and_persist_with_run.assert_called_once()

    def test_runtime_initialization_failure_prevents_model_review(self):
        self.guard.is_allowed.return_value = True
        runtime_factory = Mock()

        report, pipeline = self.run_workflow(
            runtime_error=RuntimeError("runtime initialization failed"),
            runtime_factory=runtime_factory,
        )

        self.assertEqual(report["status"], "partial")
        self.runtime_getter_mock.assert_called_once()
        runtime_factory.assert_not_called()
        pipeline.assert_not_called()
        self.radar.scan_and_persist_with_run.assert_called_once()
        self.lifecycle.finish.assert_called_once()

    def test_one_event_source_failure_does_not_block_other_source(self):
        report, _ = self.run_workflow(
            us_fetcher=FakeFetcher(
                error=RuntimeError("US feed unavailable")
            ),
            macro_fetcher=FakeFetcher([self.event]),
        )

        self.assertEqual(report["status"], "partial")
        self.storage.save_events.assert_called_once()
        self.radar.scan_and_persist_with_run.assert_called_once()
        self.lifecycle.finish.assert_called_once()

    def test_event_review_failure_does_not_block_radar(self):
        self.guard.is_allowed.return_value = True

        report, pipeline = self.run_workflow(
            pipeline_error=RuntimeError("review failed")
        )

        self.assertEqual(report["status"], "partial")
        pipeline.assert_called_once()
        self.radar.scan_and_persist_with_run.assert_called_once()
        self.lifecycle.finish.assert_called_once()

    def test_adjudicated_event_is_not_reviewed_again(self):
        self.guard.is_allowed.return_value = True
        self.repository.list_runs.return_value = [
            SimpleNamespace(run_status="adjudicated")
        ]

        report, pipeline = self.run_workflow()

        self.assertEqual(report["status"], "success")
        pipeline.assert_not_called()
        self.radar.scan_and_persist_with_run.assert_called_once()
        self.lifecycle.finish.assert_called_once()

    def test_radar_partial_status_propagates_to_workflow(self):
        report, _ = self.run_workflow(radar_status="partial")

        self.assertEqual(report["status"], "partial")

        radar_stage = next(
            stage
            for stage in report["stages"]
            if stage["name"] == "radar_scan"
        )

        self.assertEqual(radar_stage["status"], "partial")
        self.lifecycle.finish.assert_called_once()
        self.lifecycle.abort.assert_not_called()

    def test_radar_failure_still_finishes_database(self):
        self.radar.scan_and_persist_with_run.side_effect = RuntimeError(
		"radar scan failed"
	)

        report, _ = self.run_workflow()

        self.assertEqual(report["status"], "failed")

        stages = {
            item["name"]: item
            for item in report["stages"]
        }

        self.assertEqual(stages["radar_scan"]["status"], "failed")
        self.assertEqual(stages["database_finish"]["status"], "success")

        self.lifecycle.finish.assert_called_once()
        self.lifecycle.abort.assert_not_called()
        self.assertTrue(report["errors"])

    def test_database_start_failure_marks_workflow_failed(self):
        self.lifecycle.start.side_effect = RuntimeError(
            "database start failed"
        )

        report, _ = self.run_workflow()

        self.assertEqual(report["status"], "failed")
        self.lifecycle.finish.assert_not_called()
        self.radar.scan_and_persist_with_run.assert_not_called()

    def test_database_finish_failure_triggers_abort_cleanup(self):
        self.lifecycle.finish.side_effect = RuntimeError(
            "database finish failed"
        )

        report, _ = self.run_workflow()

        self.assertEqual(report["status"], "failed")
        self.lifecycle.finish.assert_called_once()
        self.lifecycle.abort.assert_called_once()

        finish_stage = next(
            (
                stage
                for stage in report["stages"]
                if stage["name"] == "database_finish"
            ),
            None,
        )

        self.assertIsNotNone(finish_stage)
        self.assertEqual(finish_stage["status"], "failed")
        self.assertEqual(
            finish_stage["error_type"],
            "RuntimeError",
        )
        self.assertEqual(
            finish_stage["error_message"],
            "database finish failed",
        )


    def test_workflow_closes_owned_quote_fetcher_after_success(self):
        report, _ = self.run_workflow(quote_fetcher=None)

        self.assertEqual(report["status"], "success")
        self.quote_fetcher_factory.assert_called_once()
        self.quote_fetcher_factory.return_value.close.assert_called_once_with()

    def test_workflow_does_not_close_injected_quote_fetcher(self):
        injected_fetcher = Mock()

        report, _ = self.run_workflow(
            quote_fetcher=injected_fetcher
        )

        self.assertEqual(report["status"], "success")
        injected_fetcher.close.assert_not_called()
        self.quote_fetcher_factory.assert_not_called()

    def test_workflow_closes_owned_quote_fetcher_after_finish_failure(self):
        self.lifecycle.finish.side_effect = RuntimeError(
            "database finish failed"
        )

        report, _ = self.run_workflow(quote_fetcher=None)

        self.assertEqual(report["status"], "failed")
        self.lifecycle.abort.assert_called_once()
        self.quote_fetcher_factory.return_value.close.assert_called_once_with()

    def test_quote_fetcher_close_failure_does_not_change_workflow_result(self):
        self.quote_fetcher_factory_close_error = RuntimeError(
            "simulated quote fetcher close failure"
        )

        # 在 workflow 创建的实例上模拟 close 失败
        with patch(
            "trendradar.market_data.workflow.QuoteFetcher"
        ) as quote_factory:
            owned_fetcher = quote_factory.return_value
            owned_fetcher.close.side_effect = (
                self.quote_fetcher_factory_close_error
            )

            # 使用独立调用，避免测试辅助方法中的工厂 Patch 覆盖
            with ExitStack() as stack:
                stack.enter_context(
                    patch(
                        "trendradar.market_data.workflow.MarketDatabaseLifecycle",
                        return_value=self.lifecycle,
                    )
                )
                stack.enter_context(
                    patch(
                        "trendradar.market_data.workflow.EventReviewRepository",
                        return_value=self.repository,
                    )
                )
                stack.enter_context(
                    patch.object(
                        self.context,
                        "get_event_review_runtime",
                        return_value=Mock(),
                    )
                )

                result_store = Mock()
                result_store.get_run.return_value = {
                    "run": {
                        "status": "success",
                        "run_id": 1,
                        "candidate_count": 1,
                        "pushed_count": 1,
                    },
                    "candidates": [],
                }

                stack.enter_context(
                    patch(
                        "trendradar.market_data.workflow.RadarResultStore",
                        return_value=result_store,
                    )
                )
                stack.enter_context(
                    patch(
                        "trendradar.market_data.workflow.CrossMarketRelationStore",
                        return_value=Mock(),
                    )
                )
                stack.enter_context(
                    patch(
                        "trendradar.market_data.workflow.MarketRadar",
                        return_value=self.radar,
                    )
                )

                report = run_market_workflow(
                    context=self.context,
                    us_fetcher=FakeFetcher([self.event]),
                    macro_fetcher=FakeFetcher(),
                    quote_fetcher=None,
                )

        self.assertEqual(report["status"], "success")
        self.lifecycle.finish.assert_called_once()
        owned_fetcher.close.assert_called_once_with()

if __name__ == "__main__":
    unittest.main()
