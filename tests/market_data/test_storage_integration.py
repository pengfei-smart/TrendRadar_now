# 本文件验证证券主数据更新器与SQLite市场库集成、行情保存、外键约束及schema幂等初始化。
"""Integration tests for market storage with security-master and quote ingestion."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from trendradar.market_data.models import QuoteSnapshot, Security
from trendradar.market_data.security_master_provider import SecurityMasterUpdater
from trendradar.market_data.storage import MarketStorage


class StaticProvider:
    def __init__(self, securities):
        self.securities = securities

    def fetch_all(self):
        return self.securities


class MarketStorageIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "market.db"
        self.storage = MarketStorage(str(self.db_path))

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_security_master_updater_writes_real_sqlite_database(self):
        securities = [
            Security("600000", "浦发银行", "SH", listing_status="active"),
            Security("000001", "平安银行", "SZ", listing_status="active"),
            Security("300001", "*ST示例", "SZ", listing_status="active", risk_flag=True),
        ]
        updater = SecurityMasterUpdater(
            StaticProvider(securities), self.storage, min_expected_count=3
        )
        result = updater.update()
        self.assertEqual(result.updated_count, 3)
        with sqlite3.connect(self.db_path) as connection:
            rows = connection.execute(
                "SELECT code,name,risk_flag FROM security_master ORDER BY code"
            ).fetchall()
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0], ("000001", "平安银行", 0))
        self.assertEqual(rows[2], ("600000", "浦发银行", 0))

    def test_quote_snapshots_persist_and_are_linked_to_security_master(self):
        self.storage.upsert_securities([
            Security("600000", "浦发银行", "SH"),
            Security("000001", "平安银行", "SZ"),
        ])
        quotes = [
            QuoteSnapshot(
                code="600000", timestamp="2026-11-09T14:33:00+08:00",
                price=10.5, change_pct=1.2, turnover=50000000,
                volume_ratio=1.1, source="eastmoney_ulist", delayed=True,
            ),
            QuoteSnapshot(
                code="000001", timestamp="2026-11-09T14:33:00+08:00",
                price=12.2, change_pct=-0.4, turnover=30000000,
                volume_ratio=0.9, source="eastmoney_ulist", delayed=True,
            ),
        ]
        self.assertEqual(self.storage.save_quotes(quotes), 2)
        with sqlite3.connect(self.db_path) as connection:
            rows = connection.execute(
                """SELECT code,price,change_pct,turnover,volume_ratio,source,delayed
                FROM market_snapshots ORDER BY code"""
            ).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0], ("000001", 12.2, -0.4, 30000000.0, 0.9, "eastmoney_ulist", 1))
        self.assertEqual(rows[1], ("600000", 10.5, 1.2, 50000000.0, 1.1, "eastmoney_ulist", 1))

    def test_schema_initialization_is_idempotent(self):
        MarketStorage(str(self.db_path))
        MarketStorage(str(self.db_path))
        with sqlite3.connect(self.db_path) as connection:
            version = connection.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()[0]
            self.assertEqual(version, "2")
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM security_master").fetchone()[0], 0
            )

    def test_quote_for_unknown_security_fails_foreign_key(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.storage.save_quotes([
                QuoteSnapshot(
                    code="600000", timestamp="2026-11-09T14:33:00+08:00",
                    price=10.5, change_pct=1, source="eastmoney_ulist",
                )
            ])


if __name__ == "__main__":
    unittest.main()
