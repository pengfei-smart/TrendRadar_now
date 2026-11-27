from trendradar.market_data.database_lifecycle import (
    MarketDatabaseLifecycle,
)
import unittest
import json
from unittest.mock import MagicMock, patch
import tempfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from trendradar.market_data import runner
from trendradar.market_data.security_master_provider import (
    DataSourceStatusRecordingError,
    SECURITY_MASTER_SOURCE_ID,
)
from trendradar.market_data.listing_status_provider import (
    EXCHANGE_LISTING_SOURCE_ID,
)

class UniverseAutoRefreshTests(unittest.TestCase):
    def setUp(self):
        self.storage = MagicMock()

    @patch("trendradar.market_data.runner.ExchangeListingStatusUpdater")
    @patch("trendradar.market_data.runner.SecurityMasterUpdater")
    @patch("trendradar.market_data.runner.EastmoneySecurityMasterProvider")
    def test_skips_both_sources_when_not_due(
        self,
        mock_provider,
        mock_security_updater,
        mock_listing_updater,
    ):
        self.storage.is_data_source_refresh_due.return_value = False

        result = runner._refresh_universe_if_due(self.storage)

        self.assertEqual(
            result,
            {
                "security_master": {
                    "status": "skipped",
                    "reason": "not_due",
                },
                "listing_status": {
                    "status": "skipped",
                    "reason": "not_due",
                },
            },
        )
        mock_security_updater.assert_not_called()
        mock_listing_updater.assert_not_called()

    @patch("trendradar.market_data.runner.ExchangeListingStatusUpdater")
    @patch("trendradar.market_data.runner.SecurityMasterUpdater")
    @patch("trendradar.market_data.runner.EastmoneySecurityMasterProvider")
    def test_refreshes_only_security_master_when_due(
        self,
        mock_provider,
        mock_security_updater,
        mock_listing_updater,
    ):
        self.storage.is_data_source_refresh_due.side_effect = [
            True,
            False,
        ]

        result = runner._refresh_universe_if_due(self.storage)

        self.assertEqual(result["security_master"]["status"], "success")
        self.assertEqual(
            result["listing_status"],
            {"status": "skipped", "reason": "not_due"},
        )
        mock_security_updater.return_value.update.assert_called_once()
        mock_listing_updater.assert_not_called()

    @patch("trendradar.market_data.runner.ExchangeListingStatusUpdater")
    @patch("trendradar.market_data.runner.SecurityMasterUpdater")
    @patch("trendradar.market_data.runner.EastmoneySecurityMasterProvider")
    def test_refreshes_only_listing_status_when_due(
        self,
        mock_provider,
        mock_security_updater,
        mock_listing_updater,
    ):
        self.storage.is_data_source_refresh_due.side_effect = [
            False,
            True,
        ]

        result = runner._refresh_universe_if_due(self.storage)

        self.assertEqual(
            result["security_master"],
            {"status": "skipped", "reason": "not_due"},
        )
        self.assertEqual(result["listing_status"]["status"], "success")
        mock_security_updater.assert_not_called()
        mock_listing_updater.return_value.update.assert_called_once()

    @patch("trendradar.market_data.runner.ExchangeListingStatusUpdater")
    @patch("trendradar.market_data.runner.SecurityMasterUpdater")
    @patch("trendradar.market_data.runner.EastmoneySecurityMasterProvider")
    def test_listing_refresh_continues_when_security_master_fails(
        self,
        mock_provider,
        mock_security_updater,
        mock_listing_updater,
    ):
        self.storage.is_data_source_refresh_due.return_value = True
        mock_security_updater.return_value.update.side_effect = RuntimeError(
            "security master unavailable"
        )

        result = runner._refresh_universe_if_due(self.storage)

        self.assertEqual(
            result["security_master"]["status"],
            "failed",
        )
        self.assertEqual(
            result["security_master"]["error_type"],
            "RuntimeError",
        )
        self.assertEqual(result["listing_status"]["status"], "success")
        mock_listing_updater.return_value.update.assert_called_once()

    @patch("trendradar.market_data.runner.ExchangeListingStatusUpdater")
    @patch("trendradar.market_data.runner.SecurityMasterUpdater")
    @patch("trendradar.market_data.runner.EastmoneySecurityMasterProvider")
    def test_refreshes_both_sources_when_both_are_due(
        self,
        mock_provider,
        mock_security_updater,
        mock_listing_updater,
    ):
        self.storage.is_data_source_refresh_due.return_value = True

        result = runner._refresh_universe_if_due(self.storage)

        self.assertEqual(result["security_master"]["status"], "success")
        self.assertEqual(result["listing_status"]["status"], "success")
        mock_security_updater.return_value.update.assert_called_once()
        mock_listing_updater.return_value.update.assert_called_once()


    @patch("trendradar.market_data.runner.ExchangeListingStatusUpdater")
    @patch("trendradar.market_data.runner.SecurityMasterUpdater")
    @patch("trendradar.market_data.runner.EastmoneySecurityMasterProvider")
    def test_skips_disabled_security_master_and_refreshes_listing_status(
        self,
        mock_provider,
        mock_security_updater,
        mock_listing_updater,
    ):
        self.storage.get_data_source.side_effect = lambda source_id: {
            "eastmoney_security_master": {"enabled": 0},
            "exchange_listing_status": {"enabled": 1},
        }.get(source_id)

        self.storage.is_data_source_refresh_due.return_value = True

        result = runner._refresh_universe_if_due(self.storage)

        self.assertEqual(
            result["security_master"],
            {"status": "skipped", "reason": "disabled"},
        )
        self.assertEqual(
            result["listing_status"]["status"],
            "success",
        )

        self.storage.is_data_source_refresh_due.assert_called_once_with(
            "exchange_listing_status",
            interval_hours=24,
        )
        mock_security_updater.assert_not_called()
        mock_listing_updater.return_value.update.assert_called_once()

    @patch("trendradar.market_data.runner.ExchangeListingStatusUpdater")
    @patch("trendradar.market_data.runner.SecurityMasterUpdater")
    @patch("trendradar.market_data.runner.EastmoneySecurityMasterProvider")
    def test_skips_disabled_listing_status_and_refreshes_security_master(
        self,
        mock_provider,
        mock_security_updater,
        mock_listing_updater,
    ):
        self.storage.get_data_source.side_effect = lambda source_id: {
            "eastmoney_security_master": {"enabled": 1},
            "exchange_listing_status": {"enabled": 0},
        }.get(source_id)

        self.storage.is_data_source_refresh_due.return_value = True

        result = runner._refresh_universe_if_due(self.storage)

        self.assertEqual(
            result["security_master"]["status"],
            "success",
        )
        self.assertEqual(
            result["listing_status"],
            {"status": "skipped", "reason": "disabled"},
        )

        self.storage.is_data_source_refresh_due.assert_called_once_with(
            "eastmoney_security_master",
            interval_hours=24,
        )
        mock_security_updater.return_value.update.assert_called_once()
        mock_listing_updater.assert_not_called()
    @patch("trendradar.market_data.runner.ExchangeListingStatusUpdater")
    @patch("trendradar.market_data.runner.SecurityMasterUpdater")
    @patch("trendradar.market_data.runner.EastmoneySecurityMasterProvider")
    def test_security_master_status_recording_failure_is_partial(
        self,
        mock_provider,
        mock_security_updater,
        mock_listing_updater,
    ):
        self.storage.is_data_source_refresh_due.return_value = True

        mock_security_updater.return_value.update.side_effect = (
            DataSourceStatusRecordingError(
                SECURITY_MASTER_SOURCE_ID,
                RuntimeError("成功状态记录失败"),
            )
        )

        result = runner._refresh_universe_if_due(self.storage)

        self.assertEqual(
            result["security_master"],
            {
                "status": "partial",
                "business_data_status": "success",
                "status_recording_status": "failed",
                "error_type": "DataSourceStatusRecordingError",
                "error": (
                    "数据源 eastmoney_security_master 业务数据已成功写入，"
                    "但成功状态记录失败：成功状态记录失败"
                ),
            },
        )

        # 一个数据源状态记录失败，不影响另一个数据源。
        self.assertEqual(result["listing_status"]["status"], "success")
        mock_listing_updater.return_value.update.assert_called_once()

    @patch("trendradar.market_data.runner.ExchangeListingStatusUpdater")
    @patch("trendradar.market_data.runner.SecurityMasterUpdater")
    @patch("trendradar.market_data.runner.EastmoneySecurityMasterProvider")
    def test_listing_status_recording_failure_is_partial(
        self,
        mock_provider,
        mock_security_updater,
        mock_listing_updater,
    ):
        self.storage.is_data_source_refresh_due.return_value = True

        mock_listing_updater.return_value.update.side_effect = (
            DataSourceStatusRecordingError(
                EXCHANGE_LISTING_SOURCE_ID,
                RuntimeError("成功状态记录失败"),
            )
        )

        result = runner._refresh_universe_if_due(self.storage)

        self.assertEqual(result["security_master"]["status"], "success")

        self.assertEqual(
            result["listing_status"],
            {
                "status": "partial",
                "business_data_status": "success",
                "status_recording_status": "failed",
                "error_type": "DataSourceStatusRecordingError",
                "error": (
                    "数据源 exchange_listing_status 业务数据已成功写入，"
                    "但成功状态记录失败：成功状态记录失败"
                ),
            },
        )

class RunnerMainTests(unittest.TestCase):
    @staticmethod
    def _scan_report():
        return {
            "status": "success",
            "run": {"run_id": 1, "candidate_count": 0},
            "event_count": 0,
            "security_count": 0,
            "selected_count": 0,
            "candidates": [],
        }

    def test_normal_run_uses_auto_refresh_and_continues_after_refresh_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "market.db")
            report_path = str(Path(tmp) / "report.json")

            with (
                patch("trendradar.market_data.runner.MarketStorage"),
                patch(
                    "trendradar.market_data.runner._refresh_universe_if_due",
                    return_value={
                        "security_master": {"status": "failed"},
                        "listing_status": {"status": "success"},
                    },
                ) as auto_refresh,
                patch(
                    "trendradar.market_data.runner._collect_events",
                    return_value=[],
                ) as collect_events,
                patch("trendradar.market_data.runner.QuoteFetcher"),
                patch(
                    "trendradar.market_data.runner.run_once",
                    return_value=self._scan_report(),
                ) as run_once,
            ):
                result = runner.main(["--db", db_path, "--report", report_path])

            self.assertEqual(result, 0)
            auto_refresh.assert_called_once()
            collect_events.assert_called_once()
            run_once.assert_called_once()

            self.assertTrue(Path(report_path).exists())
            saved_report = json.loads(
                Path(report_path).read_text(encoding="utf-8")
            )
            self.assertEqual(
                saved_report["universe_refresh"]["security_master"]["status"],
                "failed",
            )
            self.assertEqual(
                saved_report["universe_refresh"]["listing_status"]["status"],
                "success",
            )

    def test_normal_run_continues_after_partial_universe_refresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "market.db")
            report_path = str(Path(tmp) / "report.json")

            partial_refresh = {
                "security_master": {
                    "status": "partial",
                    "business_data_status": "success",
                    "status_recording_status": "failed",
                    "error_type": "DataSourceStatusRecordingError",
                    "error": "成功状态记录失败",
                },
                "listing_status": {
                    "status": "success",
                },
            }

            with (
                patch("trendradar.market_data.runner.MarketStorage"),
                patch(
                    "trendradar.market_data.runner._refresh_universe_if_due",
                    return_value=partial_refresh,
                ) as auto_refresh,
                patch(
                    "trendradar.market_data.runner._collect_events",
                    return_value=[],
                ) as collect_events,
                patch("trendradar.market_data.runner.QuoteFetcher"),
                patch(
                    "trendradar.market_data.runner.run_once",
                    return_value=self._scan_report(),
                ) as run_once,
            ):
                result = runner.main([
                    "--db", db_path,
                    "--report", report_path,
                ])

            self.assertEqual(result, 0)
            auto_refresh.assert_called_once()
            collect_events.assert_called_once()
            run_once.assert_called_once()

            saved_report = json.loads(
                Path(report_path).read_text(encoding="utf-8")
            )

            self.assertEqual(
                saved_report["universe_refresh"],
                partial_refresh,
            )

    def test_partial_universe_refresh_does_not_change_radar_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "market.db")
            report_path = str(Path(tmp) / "report.json")

            scan_report = self._scan_report()
            scan_report["status"] = "success"

            partial_refresh = {
                "security_master": {
                    "status": "partial",
                    "business_data_status": "success",
                    "status_recording_status": "failed",
                },
            }

            with (
                patch("trendradar.market_data.runner.MarketStorage"),
                patch(
                    "trendradar.market_data.runner._refresh_universe_if_due",
                    return_value=partial_refresh,
                ),
                patch(
                    "trendradar.market_data.runner._collect_events",
                    return_value=[],
                ),
                patch("trendradar.market_data.runner.QuoteFetcher"),
                patch(
                    "trendradar.market_data.runner.run_once",
                    return_value=scan_report,
                ),
            ):
                result = runner.main([
                    "--db", db_path,
                    "--report", report_path,
                ])

            self.assertEqual(result, 0)

            saved_report = json.loads(
                Path(report_path).read_text(encoding="utf-8")
            )

            self.assertEqual(saved_report["status"], "success")
            self.assertEqual(
                saved_report["universe_refresh"]["security_master"]["status"],
                "partial",
            )

    def test_generated_at_uses_shanghai_timezone(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "market.db")
            report_path = str(Path(tmp) / "report.json")

            with (
                patch("trendradar.market_data.runner.MarketStorage"),
                patch(
                    "trendradar.market_data.runner._refresh_universe_if_due",
                    return_value={},
                ),
                patch(
                    "trendradar.market_data.runner._collect_events",
                    return_value=[],
                ),
                patch("trendradar.market_data.runner.QuoteFetcher"),
                patch(
                    "trendradar.market_data.runner.run_once",
                    return_value=self._scan_report(),
                ),
            ):
                result = runner.main([
                    "--db", db_path,
                    "--report", report_path,
                ])

            self.assertEqual(result, 0)

            saved_report = json.loads(
                Path(report_path).read_text(encoding="utf-8")
            )

            generated_at = datetime.fromisoformat(
                saved_report["generated_at"]
            )

            self.assertIsNotNone(generated_at.tzinfo)
            self.assertEqual(
                generated_at.utcoffset().total_seconds(),
                8 * 60 * 60,
            )

    def test_refresh_universe_forces_both_sources_without_auto_refresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "market.db")
            report_path = str(Path(tmp) / "report.json")

            with (
                patch("trendradar.market_data.runner.MarketStorage"),
                patch(
                    "trendradar.market_data.runner._refresh_universe_if_due"
                ) as auto_refresh,
                patch(
                    "trendradar.market_data.runner.SecurityMasterUpdater"
                ) as security_updater,
                patch(
                    "trendradar.market_data.runner.ExchangeListingStatusUpdater"
                ) as listing_updater,
                patch(
                    "trendradar.market_data.runner.SectorDataUpdater"
                ) as sector_updater,
                patch(
                    "trendradar.market_data.runner._collect_events",
                    return_value=[],
                ),
                patch("trendradar.market_data.runner.QuoteFetcher"),
                patch(
                    "trendradar.market_data.runner.run_once",
                    return_value=self._scan_report(),
                ),
            ):
                result = runner.main([
                    "--db", db_path,
                    "--report", report_path,
                    "--refresh-universe",
                ])

            self.assertEqual(result, 0)
            auto_refresh.assert_not_called()
            security_updater.return_value.update.assert_called_once()
            listing_updater.return_value.update.assert_called_once()
            sector_updater.return_value.update.assert_called_once()

    def test_refresh_sectors_does_not_refresh_universe(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "market.db")

            sector_result = SimpleNamespace(
                provider="test",
                sector_count=10,
                membership_count=20,
                matched_security_count=18,
                unmatched_security_count=2,
                excluded_security_count=0,
            )

            with (
                patch("trendradar.market_data.runner.MarketStorage"),
                patch(
                    "trendradar.market_data.runner._refresh_universe_if_due"
                ) as auto_refresh,
                patch(
                    "trendradar.market_data.runner.SecurityMasterUpdater"
                ) as security_updater,
                patch(
                    "trendradar.market_data.runner.ExchangeListingStatusUpdater"
                ) as listing_updater,
                patch(
                    "trendradar.market_data.runner.SectorDataUpdater"
                ) as sector_updater,
                patch(
                    "trendradar.market_data.runner._collect_events"
                ) as collect_events,
                patch("trendradar.market_data.runner.run_once") as run_once,
            ):
                sector_updater.return_value.update.return_value = sector_result

                result = runner.main([
                    "--db", db_path,
                    "--refresh-sectors",
                ])

            self.assertEqual(result, 0)
            auto_refresh.assert_not_called()
            security_updater.assert_not_called()
            listing_updater.assert_not_called()
            sector_updater.return_value.update.assert_called_once()
            collect_events.assert_not_called()
            run_once.assert_not_called()

    def test_runner_rejects_existing_database_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "market.db")
            holder = MarketDatabaseLifecycle(
                db_path,
                storage_mode="local",
                run_id="existing-lock-owner",
            )

            holder.start()

            try:
                with (
                    patch(
                        "trendradar.market_data.runner._refresh_universe_if_due"
                    ) as auto_refresh,
                    patch(
                        "trendradar.market_data.runner._collect_events"
                    ) as collect_events,
                    patch(
                        "trendradar.market_data.runner.QuoteFetcher"
                    ) as quote_fetcher,
                    patch(
                        "trendradar.market_data.runner.run_once"
                    ) as run_once,
                ):
                    result = runner.main([
                        "--db", db_path,
                    ])

                self.assertEqual(result, 1)

                # 锁冲突必须发生在所有业务操作之前。
                auto_refresh.assert_not_called()
                collect_events.assert_not_called()
                quote_fetcher.assert_not_called()
                run_once.assert_not_called()

                # Runner 不得删除其他进程持有的锁。
                self.assertTrue(holder.lock_path.exists())

            finally:
                holder.abort()

            self.assertFalse(holder.lock_path.exists())

            # 原持锁进程退出后，新任务可以正常获取锁。
            next_lifecycle = MarketDatabaseLifecycle(
                db_path,
                storage_mode="local",
                run_id="next-lock-owner",
            )
            next_lifecycle.start()
            next_lifecycle.abort()

    def test_runner_releases_lock_when_event_collection_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "market.db")

            with (
                patch(
                    "trendradar.market_data.runner._refresh_universe_if_due",
                    return_value={},
                ),
                patch(
                    "trendradar.market_data.runner._collect_events",
                    side_effect=RuntimeError("event collection failed"),
                ),
                patch(
                    "trendradar.market_data.runner.QuoteFetcher"
                ) as quote_fetcher,
                patch(
                    "trendradar.market_data.runner.run_once"
                ) as run_once,
            ):
                result = runner.main([
                    "--db", db_path,
                ])

            self.assertEqual(result, 1)

            lock_path = Path(
                db_path
            ).with_name(
                f"{Path(db_path).name}.workflow.lock"
            )

            # 采集异常后，finally 必须释放数据库锁。
            self.assertFalse(lock_path.exists())

            # 采集失败后不能继续行情和雷达执行。
            quote_fetcher.assert_not_called()
            run_once.assert_not_called()

            # 验证锁确实可以被后续生命周期重新获取。
            next_lifecycle = MarketDatabaseLifecycle(
                db_path,
                storage_mode="local",
                run_id="after-collection-failure",
            )
            next_lifecycle.start()
            next_lifecycle.abort()

    def test_refresh_universe_failure_keeps_fail_fast_behavior(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "market.db")

            with (
                patch("trendradar.market_data.runner.MarketStorage"),
                patch(
                    "trendradar.market_data.runner._refresh_universe_if_due"
                ) as auto_refresh,
                patch(
                    "trendradar.market_data.runner.SecurityMasterUpdater"
                ) as security_updater,
                patch(
                    "trendradar.market_data.runner.ExchangeListingStatusUpdater"
                ) as listing_updater,
                patch(
                    "trendradar.market_data.runner._collect_events"
                ) as collect_events,
            ):
                security_updater.return_value.update.side_effect = RuntimeError(
                    "forced refresh failed"
                )

                result = runner.main([
                    "--db", db_path,
                    "--refresh-universe",
                ])

            self.assertEqual(result, 1)
            auto_refresh.assert_not_called()
            security_updater.return_value.update.assert_called_once()
            listing_updater.assert_not_called()
            collect_events.assert_not_called()

    def test_runner_reports_partial_when_report_write_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "market.db")
            report_path = Path(tmp) / "report.json"

            with (
                patch(
                    "trendradar.market_data.runner._refresh_universe_if_due",
                    return_value={},
                ),
                patch(
                    "trendradar.market_data.runner._collect_events",
                    return_value=[],
                ),
                patch(
                    "trendradar.market_data.runner.QuoteFetcher",
                ),
                patch(
                    "trendradar.market_data.runner.run_once",
                    return_value={
                        "status": "success",
                        "run": {
                            "run_id": 1,
                            "candidate_count": 0,
                        },
                        "event_count": 0,
                        "security_count": 0,
                    },
                ),
                patch.object(
                    Path,
                    "write_text",
                    side_effect=OSError("report write failed"),
                ),
            ):
                result = runner.main([
                    "--db",
                    db_path,
                    "--report",
                    str(report_path),
                ])

            self.assertEqual(result, 1)

            lock_path = Path(db_path).with_name(
                f"{Path(db_path).name}.workflow.lock"
            )
            self.assertFalse(lock_path.exists())

            next_lifecycle = MarketDatabaseLifecycle(
                db_path,
                storage_mode="local",
                run_id="after-report-failure",
            )
            next_lifecycle.start()
            next_lifecycle.abort()

if __name__ == "__main__":
    unittest.main()
