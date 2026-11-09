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
                    "2",
                )
                self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")


if __name__ == "__main__":
    unittest.main()
