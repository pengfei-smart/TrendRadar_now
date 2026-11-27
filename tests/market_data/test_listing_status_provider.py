# 本文件测试沪深交易所官方上市名单适配器的响应校验、板块拆分和失败保护。
"""Tests for official exchange listing-status providers."""

from io import BytesIO
import unittest
from unittest.mock import Mock

from openpyxl import Workbook

from trendradar.market_data.listing_status_provider import (
    EXCHANGE_LISTING_SOURCE_ID,
    ExchangeListingStatusUpdater,
    ListingStatusSnapshot,
    SSEMainBoardAProvider,
    SZSEBoardAProvider,
)
from trendradar.market_data.security_master_provider import (
    DataSourceStatusRecordingError,
    SecurityMasterSourceError,
)

class FakeStorage:
    def __init__(self):
        self.data_sources = {}
        self.success_calls = []
        self.failure_calls = []
        self.applied_batches = []

    def ensure_data_source(
        self,
        source_id,
        source_name,
        source_type,
        **kwargs,
    ):
        self.data_sources[source_id] = {
            "source_name": source_name,
            "source_type": source_type,
            **kwargs,
        }

    def apply_listing_status_snapshots(self, batch):
        self.applied_batches.append(batch)
        return {
            "snapshots": len(batch),
            "securities": sum(
                len(item["statuses"])
                for item in batch
            ),
        }

    def mark_data_source_success(self, source_id, **kwargs):
        self.success_calls.append(source_id)

    def mark_data_source_failure(self, source_id, error, **kwargs):
        self.failure_calls.append((source_id, error))


class FakeSSEProvider:
    def __init__(self, snapshot=None, error=None):
        self.snapshot = snapshot
        self.error = error

    def fetch_snapshot(self):
        if self.error:
            raise self.error
        return self.snapshot


class FakeSZSEProvider:
    def __init__(self, snapshots=None, error=None):
        self.snapshots = snapshots or []
        self.error = error

    def fetch_snapshots(self):
        if self.error:
            raise self.error
        return self.snapshots


def make_listing_snapshot(exchange, board_scope, codes):
    return ListingStatusSnapshot(
        source_id=f"{exchange}_{board_scope}",
        source_url="https://example.com/list",
        exchange=exchange,
        board_scope=board_scope,
        fetched_at="2026-11-24T10:00:00+08:00",
        source_as_of="2026-11-24",
        active_codes=tuple(codes),
    )

class ListingStatusProviderTests(unittest.TestCase):
    def test_success_status_recording_failure_reports_business_data_updated(self):
        storage = FakeStorage()

        def fail_success_recording(source_id, **kwargs):
            raise RuntimeError("成功状态记录失败")

        storage.mark_data_source_success = fail_success_recording

        updater = ExchangeListingStatusUpdater(
            storage,
            sse_provider=FakeSSEProvider(
                make_listing_snapshot(
                    "SH", "main_board_a", ["600000"]
                )
            ),
            szse_provider=FakeSZSEProvider([
                make_listing_snapshot(
                    "SZ", "main_board_a", ["000001"]
                ),
                make_listing_snapshot(
                    "SZ", "chinext", ["300001"]
                ),
            ]),
        )

        with self.assertRaises(DataSourceStatusRecordingError) as context:
            updater.update()

        error = context.exception

        self.assertEqual(error.source_id, EXCHANGE_LISTING_SOURCE_ID)
        self.assertTrue(error.business_data_updated)
        self.assertTrue(error.status_recording_failed)
        self.assertIsInstance(error.original_error, RuntimeError)

        # 证明三个板块的业务数据已经一次性提交。
        self.assertEqual(len(storage.applied_batches), 1)
        self.assertEqual(len(storage.applied_batches[0]), 3)

        # 不应把状态记录失败误记为业务刷新失败。
        self.assertEqual(storage.failure_calls, [])

    def test_updater_registers_source_and_records_success(self):
        storage = FakeStorage()

        updater = ExchangeListingStatusUpdater(
            storage,
            sse_provider=FakeSSEProvider(
                make_listing_snapshot(
                    "SH", "main_board_a", ["600000"]
                )
            ),
            szse_provider=FakeSZSEProvider([
                make_listing_snapshot(
                    "SZ", "main_board_a", ["000001"]
                ),
                make_listing_snapshot(
                    "SZ", "chinext", ["300001"]
                ),
            ]),
        )

        result = updater.update()

        self.assertEqual(result["snapshots"], 3)
        self.assertIn(
            "exchange_listing_status",
            storage.data_sources,
        )
        self.assertEqual(
            storage.success_calls,
            ["exchange_listing_status"],
        )
        self.assertEqual(storage.failure_calls, [])
        self.assertEqual(len(storage.applied_batches), 1)

    def test_updater_failure_records_source_error(self):
        storage = FakeStorage()

        updater = ExchangeListingStatusUpdater(
            storage,
            sse_provider=FakeSSEProvider(
                error=SecurityMasterSourceError("SSE unavailable")
            ),
            szse_provider=FakeSZSEProvider(),
        )

        with self.assertRaisesRegex(
            SecurityMasterSourceError,
            "SSE unavailable",
        ):
            updater.update()

        self.assertEqual(storage.applied_batches, [])
        self.assertEqual(storage.success_calls, [])
        self.assertEqual(
            storage.failure_calls[0][0],
            "exchange_listing_status",
        )

    def test_updater_rejects_incomplete_scope_before_write(self):
        storage = FakeStorage()

        updater = ExchangeListingStatusUpdater(
            storage,
            sse_provider=FakeSSEProvider(
                make_listing_snapshot(
                    "SH", "main_board_a", ["600000"]
                )
            ),
            szse_provider=FakeSZSEProvider([
                make_listing_snapshot(
                    "SZ", "main_board_a", ["000001"]
                ),
            ]),
        )

        with self.assertRaisesRegex(
            SecurityMasterSourceError,
            "板块覆盖不完整",
        ):
            updater.update()

        self.assertEqual(storage.applied_batches, [])
        self.assertEqual(storage.success_calls, [])
        self.assertEqual(
            storage.failure_calls[0][0],
            "exchange_listing_status",
        )

    def test_updater_failure_recording_does_not_mask_original_error(self):
        storage = FakeStorage()

        def fail_recording(*args, **kwargs):
            raise RuntimeError("状态记录失败")

        storage.mark_data_source_failure = fail_recording

        updater = ExchangeListingStatusUpdater(
            storage,
            sse_provider=FakeSSEProvider(
                error=SecurityMasterSourceError("原始采集错误")
            ),
            szse_provider=FakeSZSEProvider(),
        )

        with self.assertRaisesRegex(
            SecurityMasterSourceError,
            "原始采集错误",
        ):
            updater.update()

    def test_sse_provider_accepts_only_normal_main_board_a_list(self):
        session = Mock()
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "result": [{"SECURITY_CODE": "600000"}, {"SECURITY_CODE": "601000"}], "pageHelp": {"total": 2}
        }
        session.get.return_value = response
        snapshot = SSEMainBoardAProvider(
            session=session, minimum_count=2
        ).fetch_snapshot()
        self.assertEqual(snapshot.exchange, "SH")
        self.assertEqual(snapshot.board_scope, "main_board_a")
        self.assertEqual(snapshot.active_codes, ("600000", "601000"))
        self.assertEqual(
            session.get.call_args.kwargs["params"]["COMPANY_STATUS"], "2,4,5,7,8"
        )

    def test_sse_provider_rejects_star_or_malformed_snapshot(self):
        session = Mock()
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"result": [{"SECURITY_CODE": "688001"}], "pageHelp": {"total": 1}}
        session.get.return_value = response
        with self.assertRaises(SecurityMasterSourceError):
            SSEMainBoardAProvider(session=session, minimum_count=1).fetch_snapshot()

    @staticmethod
    def _szse_xlsx():
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["板块", "A股代码", "A股简称"])
        sheet.append(["主板", "000001", "平安银行"])
        sheet.append(["创业板", "300001", "特锐德"])
        buffer = BytesIO()
        workbook.save(buffer)
        workbook.close()
        return buffer.getvalue()

    def test_szse_provider_resets_incorrect_worksheet_dimension(self):
        import zipfile

        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["板块", "A股代码", "A股简称"])
        sheet.append(["主板", "000001", "平安银行"])
        sheet.append(["创业板", "300001", "特锐德"])
        original = BytesIO()
        workbook.save(original)
        workbook.close()

        # Reproduce the official SZSE workbook's incorrect A1:A1 dimension.
        corrupted = BytesIO()
        with zipfile.ZipFile(BytesIO(original.getvalue()), "r") as source:
            with zipfile.ZipFile(corrupted, "w") as target:
                for item in source.infolist():
                    data = source.read(item.filename)
                    if item.filename == "xl/worksheets/sheet1.xml":
                        data = data.replace(b'ref="A1:C3"', b'ref="A1:A1"')
                    target.writestr(item, data)

        session = Mock()
        response = Mock()
        response.raise_for_status.return_value = None
        response.content = corrupted.getvalue()
        session.get.return_value = response
        main, chinext = SZSEBoardAProvider(
            session=session, minimum_main_count=1, minimum_chinext_count=1
        ).fetch_snapshots()
        self.assertEqual(main.active_codes, ("000001",))
        self.assertEqual(chinext.active_codes, ("300001",))

    def test_szse_provider_splits_main_board_and_chinext(self):
        session = Mock()
        response = Mock()
        response.raise_for_status.return_value = None
        response.content = self._szse_xlsx()
        session.get.return_value = response
        main, chinext = SZSEBoardAProvider(
            session=session, minimum_main_count=1, minimum_chinext_count=1
        ).fetch_snapshots()
        self.assertEqual(main.active_codes, ("000001",))
        self.assertEqual(main.board_scope, "main_board_a")
        self.assertEqual(chinext.active_codes, ("300001",))
        self.assertEqual(chinext.board_scope, "chinext")

    def test_szse_provider_accepts_302_series_chinext_code(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["板块", "A股代码", "A股简称"])
        sheet.append(["主板", "000001", "平安银行"])
        sheet.append(["创业板", "302132", "中航成飞"])
        buffer = BytesIO()
        workbook.save(buffer)
        workbook.close()
        session = Mock()
        response = Mock()
        response.raise_for_status.return_value = None
        response.content = buffer.getvalue()
        session.get.return_value = response
        main, chinext = SZSEBoardAProvider(
            session=session, minimum_main_count=1, minimum_chinext_count=1
        ).fetch_snapshots()
        self.assertEqual(main.active_codes, ("000001",))
        self.assertEqual(chinext.active_codes, ("302132",))

    def test_szse_provider_retries_transient_read_timeout(self):
        import requests
        session = Mock()
        response = Mock()
        response.raise_for_status.return_value = None
        response.content = self._szse_xlsx()
        session.get.side_effect = [requests.exceptions.ReadTimeout("temporary timeout"), response]
        main, chinext = SZSEBoardAProvider(
            session=session, minimum_main_count=1, minimum_chinext_count=1,
            backoff_seconds=0,
        ).fetch_snapshots()
        self.assertEqual(main.active_codes, ("000001",))
        self.assertEqual(chinext.active_codes, ("300001",))
        self.assertEqual(session.get.call_count, 2)

    def test_szse_provider_fails_after_retry_limit(self):
        import requests
        session = Mock()
        session.get.side_effect = requests.exceptions.ReadTimeout("still unavailable")
        with self.assertRaises(SecurityMasterSourceError):
            SZSEBoardAProvider(
                session=session, minimum_main_count=1, minimum_chinext_count=1,
                max_attempts=2, backoff_seconds=0,
            ).fetch_snapshots()
        self.assertEqual(session.get.call_count, 2)

    def test_szse_provider_rejects_missing_board_and_duplicate_codes(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["板块", "A股代码", "A股简称"])
        sheet.append(["其他", "000001", "样本"])
        buffer = BytesIO()
        workbook.save(buffer)
        workbook.close()
        session = Mock()
        response = Mock()
        response.raise_for_status.return_value = None
        response.content = buffer.getvalue()
        session.get.return_value = response
        with self.assertRaises(SecurityMasterSourceError):
            SZSEBoardAProvider(
                session=session, minimum_main_count=1, minimum_chinext_count=1
            ).fetch_snapshots()


if __name__ == "__main__":
    unittest.main()
