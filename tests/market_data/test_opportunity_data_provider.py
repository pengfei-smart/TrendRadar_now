import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import pytz

from trendradar.market_data.models import MarketEvent, MappingEvidence, QuoteSnapshot, Security
from trendradar.market_data.opportunity_data_provider import OpportunityDataProvider
from trendradar.market_data.storage import MarketStorage


SHANGHAI = pytz.timezone("Asia/Shanghai")


class OpportunityDataProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.storage = MarketStorage(str(Path(self.temp.name) / "market.db"))
        self.storage.upsert_securities([
            Security("600000", "浦发银行", "SH", listing_status="active"),
            Security("000001", "平安银行", "SZ", listing_status="active"),
            Security("688001", "科创测试", "SH", listing_status="active"),
            Security("430001", "北交测试", "BJ", listing_status="active"),
            Security("600001", "风险测试", "SH", listing_status="active", risk_flag=True),
            Security("600002", "停牌测试", "SH", listing_status="suspended"),
        ])
        with self.storage.connect() as connection:
            connection.executemany(
                """INSERT INTO sector_master
                (sector_code,sector_name,sector_type,provider)
                VALUES(?,?,?,?)""",
                [
                    ("SW1:801080", "电子", "industry", "akshare_sw"),
                    ("SW2:801081", "半导体", "industry", "akshare_sw"),
                ],
            )
            connection.executemany(
                """INSERT INTO sector_membership
                (security_code,sector_code,valid_from,valid_to,source)
                VALUES(?,?,?,?,?)""",
                [
                    ("600000", "SW1:801080", "", "", "test"),
                    ("600000", "SW2:801081", "", "", "test"),
                    ("000001", "SW1:801080", "", "", "test"),
                ],
            )

    def tearDown(self):
        self.temp.cleanup()

    def test_load_snapshot_joins_windowed_events_evidence_universe_quotes_and_sectors(self):
        as_of = SHANGHAI.localize(datetime(2026, 11, 18, 10, 0, 0))
        self.storage.save_events([
            MarketEvent(
                "event-cn", "rss", "国内半导体政策",
                "2026-11-18T09:30:00+08:00", region="CN",
            ),
            MarketEvent(
                "event-us", "wire", "美国芯片消息",
                "2026-11-18T08:30:00+08:00", region="US",
            ),
            MarketEvent(
                "event-old", "wire", "窗口外旧消息",
                "2026-11-15T08:30:00+08:00", region="US",
            ),
        ])
        self.storage.save_mapping_evidence("event-cn", [
            MappingEvidence(
                "SW2:801081", "industry_keyword", .72, "芯片关键词命中",
                verified=False, evidence_type="direct",
            )
        ])
        self.storage.save_mapping_evidence("event-us", [
            MappingEvidence(
                "SW2:801081", "industry_keyword", .56, "官方行业名称命中",
                verified=True, evidence_type="official_name",
            )
        ])
        self.storage.save_quotes([
            QuoteSnapshot(
                "600000", "2026-11-18T09:55:00+08:00", price=10.2,
                change_pct=2.0, turnover=1000000, volume_ratio=1.8,
                source="eastmoney", source_timestamp="2026-11-18T09:55:00+08:00",
                collected_at="2026-11-18T09:55:01+08:00",
                timestamp_quality="provider",
            ),
            QuoteSnapshot(
                "000001", "2026-11-18T09:30:00+08:00", price=12.1,
                change_pct=1.0, turnover=800000, source="eastmoney",
                source_timestamp="2026-11-18T09:30:00+08:00",
                collected_at="2026-11-18T09:30:01+08:00",
                timestamp_quality="provider",
            ),
        ])

        provider = OpportunityDataProvider(
            self.storage,
            max_quote_age_seconds=900,
            clock=lambda: as_of,
        )
        snapshot = provider.load_snapshot(
            lookback_hours=24,
            regions=("CN", "US"),
        )

        self.assertEqual(
            {event.event_id for event in snapshot.events},
            {"event-cn", "event-us"},
        )
        self.assertEqual(
            {item.event_id for item in snapshot.mapping_evidence},
            {"event-cn", "event-us"},
        )
        by_event = {item.event_id: item for item in snapshot.mapping_evidence}
        self.assertFalse(by_event["event-cn"].verified)
        self.assertEqual(by_event["event-cn"].evidence_type, "direct")
        self.assertTrue(by_event["event-us"].verified)
        self.assertEqual(
            {item.code for item in snapshot.eligible_securities},
            {"600000", "000001"},
        )
        self.assertEqual(set(snapshot.quoted_security_codes), {"600000", "000001"})
        self.assertEqual(snapshot.fresh_quote_count, 1)
        self.assertEqual(snapshot.stale_quote_count, 1)
        self.assertIn("stale_quotes:1", snapshot.warnings)
        self.assertEqual(
            dict(snapshot.security_sector_codes)["600000"],
            ("SW1:801080", "SW2:801081"),
        )

    def test_empty_event_window_returns_empty_evidence_without_global_evidence_scan(self):
        provider = OpportunityDataProvider(
            self.storage,
            clock=lambda: SHANGHAI.localize(datetime(2026, 11, 18, 10, 0, 0)),
        )

        snapshot = provider.load_snapshot(lookback_hours=1)

        self.assertEqual(snapshot.events, ())
        self.assertEqual(snapshot.mapping_evidence, ())
        self.assertIn("no_events_in_window", snapshot.warnings)

    def test_future_quote_is_flagged_not_silently_treated_as_fresh(self):
        as_of = SHANGHAI.localize(datetime(2026, 11, 18, 10, 0, 0))
        self.storage.save_quotes([
            QuoteSnapshot(
                "600000", "2026-11-18T10:05:00+08:00", price=10.2,
                source="eastmoney", source_timestamp="2026-11-18T10:05:00+08:00",
                collected_at="2026-11-18T10:05:01+08:00",
                timestamp_quality="provider",
            )
        ])
        provider = OpportunityDataProvider(
            self.storage,
            future_tolerance_seconds=120,
            clock=lambda: as_of,
        )

        snapshot = provider.load_snapshot()

        freshness = {
            item.security_code: item for item in snapshot.quote_freshness
        }
        self.assertEqual(freshness["600000"].status, "future")
        self.assertIn("future_quotes:1", snapshot.warnings)
        self.assertEqual(snapshot.fresh_quote_count, 0)

    def test_invalid_lookback_and_naive_as_of_are_rejected(self):
        provider = OpportunityDataProvider(self.storage)

        with self.assertRaises(ValueError):
            provider.load_snapshot(lookback_hours=0)
        with self.assertRaises(ValueError):
            provider.load_snapshot(as_of=datetime(2026, 11, 18, 10, 0, 0))


if __name__ == "__main__":
    unittest.main()
