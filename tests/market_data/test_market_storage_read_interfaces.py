"""Read-interface tests for the opportunity data provider boundary."""

import tempfile
import unittest
from pathlib import Path

from trendradar.market_data.models import MarketEvent, MappingEvidence, QuoteSnapshot, Security
from trendradar.market_data.storage import MarketStorage


class MarketStorageReadInterfaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.storage = MarketStorage(str(Path(self.temp.name) / "market.db"))
        self.storage.upsert_securities([
            Security("600000", "浦发银行", "SH", listing_status="active"),
            Security("000001", "平安银行", "SZ", listing_status="active"),
        ])

    def tearDown(self):
        self.temp.cleanup()

    def test_load_market_events_filters_publication_window_and_region(self):
        self.storage.save_events([
            MarketEvent("event-cn", "rss", "国内政策", "2026-11-18T09:00:00+08:00", region="CN"),
            MarketEvent("event-us", "wire", "美联储消息", "2026-11-18T10:00:00+08:00", region="US"),
            MarketEvent("event-old", "wire", "旧消息", "2026-11-16T10:00:00+08:00", region="US"),
        ])
        rows = self.storage.load_market_events(
            start_published_at="2026-11-18T00:00:00+08:00",
            end_published_at="2026-11-18T23:59:59+08:00",
            regions=["US"],
        )
        self.assertEqual([row.event_id for row in rows], ["event-us"])
        self.assertEqual(rows[0].title, "美联储消息")

    def test_load_event_mapping_evidence_preserves_types_and_review_status(self):
        self.storage.save_events([
            MarketEvent("event-map", "rss", "半导体需求", "2026-11-18T09:00:00+08:00")
        ])
        self.storage.save_mapping_evidence("event-map", [
            MappingEvidence(
                "SW2:801081", "industry_keyword", .72, "专项关键词命中",
                evidence_type="direct", verified=False,
            ),
            MappingEvidence(
                "SW2:801101", "official_name", .56, "官方行业名称命中",
                evidence_type="official_name", verified=True,
            ),
        ])
        all_rows = self.storage.load_event_mapping_evidence(event_id="event-map")
        trusted = self.storage.load_event_mapping_evidence(
            event_id="event-map", verified_only=True, min_confidence=.5,
        )
        self.assertEqual(len(all_rows), 2)
        rows_by_target = {row["target_code"]: row for row in all_rows}
        self.assertEqual(rows_by_target["SW2:801081"]["evidence_type"], "direct")
        self.assertEqual(rows_by_target["SW2:801081"]["verified"], 0)
        self.assertEqual(rows_by_target["SW2:801101"]["evidence_type"], "official_name")
        self.assertEqual(rows_by_target["SW2:801101"]["verified"], 1)
        self.assertEqual(len(trusted), 1)
        self.assertEqual(trusted[0]["target_code"], "SW2:801101")

    def test_load_latest_quotes_returns_only_provider_timestamped_latest_per_code(self):
        self.storage.save_quotes([
            QuoteSnapshot(
                "600000", "2026-11-18T09:30:00+08:00", price=10,
                source="provider", source_timestamp="2026-11-18T09:30:00+08:00",
                collected_at="2026-11-18T09:30:01+08:00", timestamp_quality="provider",
            ),
            QuoteSnapshot(
                "600000", "2026-11-18T09:31:00+08:00", price=10.2,
                source="provider", source_timestamp="2026-11-18T09:31:00+08:00",
                collected_at="2026-11-18T09:31:01+08:00", timestamp_quality="provider",
            ),
            QuoteSnapshot(
                "000001", "2026-11-18T09:31:00+08:00", price=12,
                source="missing-ts", timestamp_quality="missing",
            ),
            QuoteSnapshot(
                "000001", "2026-11-18T09:32:00+08:00", price=12.1,
                source="legacy-ts", timestamp_quality="legacy_unknown",
            ),
        ])
        rows = self.storage.load_latest_quotes()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].code, "600000")
        self.assertEqual(rows[0].price, 10.2)
        self.assertEqual(rows[0].timestamp_quality, "provider")
        self.assertEqual(self.storage.load_latest_quotes(["000001"]), [])

    def test_load_security_sector_codes_returns_only_current_official_codes(self):
        with self.storage.connect() as connection:
            connection.executemany(
                """INSERT INTO sector_master
                (sector_code,sector_name,sector_type,provider)
                VALUES(?,?,?,?)""",
                [
                    ("SW1:801080", "电子", "industry", "akshare_sw"),
                    ("SW2:801081", "半导体", "industry", "akshare_sw"),
                    ("CONCEPT:AI", "人工智能", "concept", "akshare_sw"),
                    ("SW2:999999", "其他供应商行业", "industry", "other_provider"),
                ],
            )
            connection.executemany(
                """INSERT INTO sector_membership
                (security_code,sector_code,valid_from,valid_to,source)
                VALUES(?,?,?,?,?)""",
                [
                    ("600000", "SW1:801080", "", "", "test"),
                    ("600000", "SW2:801081", "", "", "test"),
                    ("600000", "CONCEPT:AI", "", "", "test"),
                    ("600000", "SW2:999999", "", "", "test"),
                ],
            )
        result = self.storage.load_security_sector_codes(["600000"])
        self.assertEqual(
            result, {"600000": ("SW1:801080", "SW2:801081")}
        )
        self.assertEqual(self.storage.load_security_sector_codes(["000001"]), {})


if __name__ == "__main__":
    unittest.main()
