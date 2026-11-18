"""Offline integration tests for the market radar runner."""
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from trendradar.market_data.models import MarketEvent, QuoteSnapshot, Security
from trendradar.market_data.runner import run_once
from trendradar.market_data.storage import MarketStorage


class FakeQuoteFetcher:
    source_name = "test_quotes"

    def fetch(self, securities):
        now = datetime.now(timezone.utc).astimezone().isoformat()
        return [
            QuoteSnapshot(
                code=item.code, timestamp=now, price=10.0,
                change_pct=1.5, turnover=1000000.0,
                volume_ratio=1.2, source=self.source_name,
                source_timestamp=now, timestamp_quality="provider",
            )
            for item in securities
        ], self.source_name


class MarketRunnerTests(unittest.TestCase):
    def test_run_once_executes_and_persists_offline_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "market.db")
            storage = MarketStorage(db_path)
            storage.upsert_securities([
                Security("600000", "浦发银行", "SH", listing_status="active"),
            ])
            with storage.connect() as connection:
                # Active listing status must carry provenance. MarketStorage
                # intentionally downgrades unverified legacy "active" rows
                # to "unknown" when a new storage instance initializes.
                connection.execute(
                    """UPDATE security_master
                    SET listing_status_source='test_fixture',
                        listing_status_updated_at='2026-11-17'
                    WHERE code='600000'"""
                )
                connection.execute(
                    "INSERT INTO sector_master(sector_code,sector_name) VALUES('半导体','半导体')"
                )
                connection.execute(
                    """INSERT INTO sector_membership
                    (security_code,sector_code,valid_from,valid_to)
                    VALUES('600000','半导体','','')"""
                )
            report = run_once(
                db_path=db_path,
                events=[MarketEvent(
                    "event-1", "test", "NVIDIA GPU semiconductor demand rises",
                    datetime.now(timezone.utc).isoformat(),
                    summary="Semiconductor demand",
                )],
                quote_fetcher=FakeQuoteFetcher(),
            )
            self.assertEqual(report["status"], "success")
            self.assertEqual(report["event_count"], 1)
            self.assertEqual(report["security_count"], 1)
            self.assertEqual(report["run"]["candidate_count"], 1)
            self.assertEqual(report["candidates"][0]["code"], "600000")


if __name__ == "__main__":
    unittest.main()
