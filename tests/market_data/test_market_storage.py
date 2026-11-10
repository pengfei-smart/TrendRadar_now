# 本文件测试市场数据库schema版本、主数据UPSERT、事件幂等和多行情源唯一约束，属于数据库详细设计阶段。
"""SQLite market storage tests."""

import tempfile
import unittest
from pathlib import Path

from trendradar.market_data.storage import MarketStorage
from trendradar.market_data.models import MarketEvent, Security, QuoteSnapshot


class MarketStorageTests(unittest.TestCase):
    def test_event_security_and_multi_source_quote_persistence(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = MarketStorage(str(Path(tmp) / "market.db"))
            storage.upsert_securities([Security("600000", "浦发银行", exchange="SH")])
            storage.save_events([MarketEvent("e1", "source", "headline", "2026-01-01")])
            storage.save_events([MarketEvent("e1", "source", "headline revised", "2026-01-02")])

            timestamp = "2026-01-02T14:33:00+08:00"
            storage.save_quotes([
                QuoteSnapshot("600000", timestamp, price=10.2, change_pct=1.2, source="provider_a"),
                QuoteSnapshot("600000", timestamp, price=10.3, change_pct=1.3, source="provider_b"),
            ])
            # Same code, data time and provider is idempotent and updates values.
            storage.save_quotes([
                QuoteSnapshot("600000", timestamp, price=10.4, change_pct=1.4, source="provider_a"),
            ])

            with storage.connect() as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM market_events").fetchone()[0], 1)
                self.assertEqual(connection.execute("SELECT title FROM market_events").fetchone()[0], "headline revised")
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0], 2)
                self.assertEqual(
                    connection.execute(
                        "SELECT price FROM market_snapshots WHERE source='provider_a'"
                    ).fetchone()[0], 10.4
                )
                self.assertEqual(
                    connection.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0],
                    "3",
                )
                self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")


    def test_listing_status_snapshot_records_provenance_and_rejects_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = MarketStorage(str(Path(tmp) / "market.db"))
            storage.upsert_securities([Security("600000", "浦发银行", "SH")])
            args = dict(
                source_id="sse_main_board", exchange="SH",
                board_scope="main_board_a", source_as_of="2026-11-10",
                statuses={"600000": "active"},
            )
            storage.apply_listing_status_snapshot(
                **args, fetched_at="2026-11-10T15:00:00+08:00",
                source_url="https://example.invalid/list",
            )
            with storage.connect() as connection:
                row = connection.execute(
                    "SELECT listing_status,listing_status_source,listing_status_updated_at "
                    "FROM security_master WHERE code='600000'"
                ).fetchone()
                self.assertEqual(tuple(row), ("active", "sse_main_board", "2026-11-10"))
                self.assertEqual(connection.execute(
                    "SELECT COUNT(*) FROM listing_status_evidence"
                ).fetchone()[0], 1)
            with self.assertRaises(ValueError):
                storage.apply_listing_status_snapshot(
                    **args, fetched_at="2026-11-10T14:00:00+08:00"
                )

    def test_unknown_identity_refresh_does_not_overwrite_verified_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = MarketStorage(str(Path(tmp) / "market.db"))
            storage.upsert_securities([Security("600000", "浦发银行", "SH")])
            storage.apply_listing_status_snapshot(
                source_id="sse_main_board", exchange="SH",
                board_scope="main_board_a",
                fetched_at="2026-11-10T15:00:00+08:00",
                source_as_of="2026-11-10", statuses={"600000": "active"},
            )
            storage.upsert_securities([Security("600000", "浦发银行股份", "SH")])
            with storage.connect() as connection:
                row = connection.execute(
                    "SELECT name,listing_status FROM security_master WHERE code='600000'"
                ).fetchone()
                self.assertEqual(tuple(row), ("浦发银行股份", "active"))


if __name__ == "__main__":
    unittest.main()
