# 本文件测试证券主数据分页采集、规范化、风险识别、数量阈值与异常快照拒绝策略。
"""Tests for security-master provider and guarded refresh."""

import unittest
from unittest.mock import Mock

from trendradar.market_data.models import Security
from trendradar.market_data.security_master_provider import (
    EastmoneySecurityMasterProvider,
    SecurityMasterSourceError,
    SecurityMasterUpdater,
    infer_board,
    infer_exchange,
    is_risk_warning_name,
    normalize_eastmoney_record,
    eligible_securities,
)


class FakeProvider:
    def __init__(self, records):
        self.records = records

    def fetch_all(self):
        return self.records


class FakeStorage:
    def __init__(self, existing=0):
        self.existing = existing
        self.writes = []

    def count_securities(self):
        return self.existing

    def upsert_securities(self, items):
        self.writes.append(list(items))
        return len(items)


class SecurityMasterProviderTests(unittest.TestCase):
    def test_exchange_and_board_inference(self):
        self.assertEqual(infer_exchange("600000"), "SH")
        self.assertEqual(infer_exchange("000001"), "SZ")
        self.assertEqual(infer_exchange("830799"), "BJ")
        self.assertEqual(infer_board("688001", "SH"), "科创板")
        self.assertEqual(infer_board("300001", "SZ"), "创业板")

    def test_risk_warning_name_detection(self):
        self.assertTrue(is_risk_warning_name("*ST某公司"))
        self.assertTrue(is_risk_warning_name("ST 某公司"))
        self.assertFalse(is_risk_warning_name("明星电力"))

    def test_eligible_securities_excludes_beijing_exchange(self):
        securities = [
            Security("600000", "浦发银行", "SH"),
            Security("000001", "平安银行", "SZ"),
            Security("920305", "北交所样本", "BJ"),
            Security("688001", "科创板样本", "SH"),
            Security("689009", "科创板新代码样本", "SH"),
            Security("600001", "ST样本", "SH", risk_flag=True),
            Security("600002", "停牌样本", "SH", listing_status="suspended"),
        ]
        eligible = eligible_securities(securities)
        self.assertEqual([item.code for item in eligible], ["600000", "000001"])

    def test_eastmoney_record_normalization(self):
        security = normalize_eastmoney_record({"f12": "600000", "f14": "浦发银行", "f13": "1"})
        self.assertEqual(security.code, "600000")
        self.assertEqual(security.exchange, "SH")
        self.assertFalse(security.risk_flag)
        self.assertEqual(security.listing_status, "unknown")

    def test_paginated_provider_assembles_all_pages(self):
        session = Mock()
        first = Mock()
        first.raise_for_status.return_value = None
        first.json.return_value = {"data": {"total": 2, "diff": [
            {"f12": "600000", "f14": "浦发银行", "f13": "1"}
        ]}}
        second = Mock()
        second.raise_for_status.return_value = None
        second.json.return_value = {"data": {"total": 2, "diff": [
            {"f12": "000001", "f14": "平安银行", "f13": "0"}
        ]}}
        session.get.side_effect = [first, second]
        provider = EastmoneySecurityMasterProvider(session=session, page_size=1, max_pages=3)
        records = provider.fetch_all()
        self.assertEqual([item.code for item in records], ["600000", "000001"])
        self.assertEqual(session.get.call_count, 2)
        self.assertEqual(
            session.get.call_args_list[0].kwargs["params"]["fs"],
            "m:1+t:2,m:0+t:6,m:0+t:80",
        )

    def test_paginated_provider_uses_actual_page_capacity(self):
        session = Mock()
        responses = []
        total = 250
        for page_size in (100, 100, 50):
            response = Mock()
            response.raise_for_status.return_value = None
            start = len(responses) * 100
            records = [
                {"f12": f"{600000 + index:06d}", "f14": f"公司{index}", "f13": "1"}
                for index in range(start, start + page_size)
            ]
            response.json.return_value = {"data": {"total": total, "diff": records}}
            responses.append(response)
        session.get.side_effect = responses
        provider = EastmoneySecurityMasterProvider(session=session, page_size=500, max_pages=5)
        records = provider.fetch_all()
        self.assertEqual(len(records), total)
        self.assertEqual(session.get.call_count, 3)

    def test_paginated_provider_rejects_unstable_total(self):
        session = Mock()
        responses = []
        for total in (2, 3):
            response = Mock()
            response.raise_for_status.return_value = None
            response.json.return_value = {"data": {"total": total, "diff": [
                {"f12": "600000", "f14": "浦发银行", "f13": "1"}
            ]}}
            responses.append(response)
        session.get.side_effect = responses
        provider = EastmoneySecurityMasterProvider(session=session, page_size=1, max_pages=3)
        with self.assertRaises(SecurityMasterSourceError):
            provider.fetch_all()

    def test_update_rejects_small_snapshot_before_write(self):
        storage = FakeStorage()
        updater = SecurityMasterUpdater(FakeProvider([Security("600000", "浦发银行", "SH")]),
                                        storage, min_expected_count=2)
        with self.assertRaises(SecurityMasterSourceError):
            updater.update()
        self.assertEqual(storage.writes, [])

    def test_update_rejects_large_count_drop(self):
        storage = FakeStorage(existing=2000)
        records = [Security(f"{i:06d}", f"公司{i}", "SZ") for i in range(1500)]
        updater = SecurityMasterUpdater(FakeProvider(records), storage,
                                        min_expected_count=1000, max_count_drop_ratio=0.2)
        with self.assertRaises(SecurityMasterSourceError):
            updater.update()
        self.assertEqual(storage.writes, [])

    def test_update_persists_full_valid_snapshot(self):
        storage = FakeStorage(existing=0)
        records = [Security(f"{i:06d}", f"公司{i}", "SZ") for i in range(1000)]
        updater = SecurityMasterUpdater(FakeProvider(records), storage, min_expected_count=1000)
        result = updater.update()
        self.assertEqual(result.updated_count, 1000)
        self.assertEqual(result.normalized_count, 1000)
        self.assertEqual(len(storage.writes), 1)


if __name__ == "__main__":
    unittest.main()
