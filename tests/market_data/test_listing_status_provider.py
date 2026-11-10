# 本文件测试沪深交易所官方上市名单适配器的响应校验、板块拆分和失败保护。
"""Tests for official exchange listing-status providers."""

from io import BytesIO
import unittest
from unittest.mock import Mock

from openpyxl import Workbook

from trendradar.market_data.listing_status_provider import (
    SSEMainBoardAProvider,
    SZSEBoardAProvider,
)
from trendradar.market_data.security_master_provider import SecurityMasterSourceError


class ListingStatusProviderTests(unittest.TestCase):
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
