# 本文件测试市场雷达数据库建表、事件幂等保存与行情保存，属于逐文件开发清单第八项。
"""SQLite market storage tests."""

import tempfile
import unittest
from pathlib import Path

from trendradar.market_data.storage import MarketStorage
from trendradar.market_data.models import MarketEvent, Security, QuoteSnapshot


class MarketStorageTests(unittest.TestCase):
    def test_event_and_security_quote_persistence(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = MarketStorage(str(Path(tmp) / "market.db"))
            storage.save_events([MarketEvent("e1", "source", "headline", "2026-01-01")])
            storage.save_events([MarketEvent("e1", "source", "headline revised", "2026-01-02")])
            storage.upsert_securities([Security("600000", "浦发银行")])
            storage.save_quotes([QuoteSnapshot("600000", "2026-01-02T14:33:00+08:00", price=10.2, change_pct=1.2)])
            with storage.connect() as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM market_events").fetchone()[0], 1)
                self.assertEqual(connection.execute("SELECT title FROM market_events").fetchone()[0], "headline revised")
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
