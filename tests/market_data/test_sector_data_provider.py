"""Tests for guarded Shenwan sector master and membership refresh."""
import tempfile
import unittest
from pathlib import Path

from trendradar.market_data.sector_data_provider import (
    SectorDataUpdater, SectorMembershipRecord, SectorRecord, SectorSnapshot,
)
from trendradar.market_data.security_master_provider import SecurityMasterSourceError
from trendradar.market_data.models import Security
from trendradar.market_data.storage import MarketStorage


class FakeSectorProvider:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    def fetch_snapshot(self):
        return self.snapshot


class SectorDataUpdaterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = MarketStorage(str(Path(self.tmp.name) / "market.db"))
        self.storage.upsert_securities([
            Security("600000", "浦发银行", "SH"),
            Security("000001", "平安银行", "SZ"),
        ])

    def tearDown(self):
        self.tmp.cleanup()

    def snapshot(self):
        return SectorSnapshot(
            provider="akshare_sw",
            fetched_at="2026-11-18T00:00:00+00:00",
            sectors=(
                SectorRecord("SW1:801120", "银行", source_updated_at="2026-11-18"),
            ),
            memberships=(
                SectorMembershipRecord("600000", "SW1:801120"),
                SectorMembershipRecord("000001", "SW1:801120"),
            ),
        )

    def updater(self, snapshot):
        return SectorDataUpdater(
            FakeSectorProvider(snapshot), self.storage,
            minimum_sectors=1, minimum_memberships=1,
            minimum_matched_securities=1,
        )

    def test_refresh_persists_sector_master_and_memberships(self):
        result = self.updater(self.snapshot()).update()
        self.assertEqual(result.sector_count, 1)
        self.assertEqual(result.membership_count, 2)
        self.assertEqual(result.matched_security_count, 2)
        loaded = {item.code: item for item in self.storage.load_securities()}
        self.assertEqual(loaded["600000"].sectors, ("SW1:801120", "银行"))
        self.assertEqual(loaded["000001"].sectors, ("SW1:801120", "银行"))
        with self.storage.connect() as connection:
            row = connection.execute(
                "SELECT provider,source_url FROM sector_master WHERE sector_code='SW1:801120'"
            ).fetchone()
        self.assertEqual(row["provider"], "akshare_sw")
        self.assertTrue(row["source_url"].startswith("https://"))

    def test_loaded_securities_include_shenwan_names_for_keyword_mapping(self):
        self.updater(self.snapshot()).update()
        with self.storage.connect() as connection:
            connection.execute(
                "UPDATE sector_master SET sector_name='半导体' WHERE sector_code='SW1:801120'"
            )
        loaded = {item.code: item for item in self.storage.load_securities()}
        self.assertIn("半导体", loaded["600000"].sectors)
        self.assertIn("SW1:801120", loaded["600000"].sectors)

    def test_refresh_replaces_only_same_provider_snapshot(self):
        with self.storage.connect() as connection:
            connection.execute(
                "INSERT INTO sector_master(sector_code,sector_name,provider) VALUES('LOCAL:1','本地行业','manual')"
            )
            connection.execute(
                "INSERT INTO sector_membership(security_code,sector_code,source) VALUES('600000','LOCAL:1','manual')"
            )
        self.updater(self.snapshot()).update()
        with self.storage.connect() as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM sector_master WHERE provider='manual'"
            ).fetchone()[0], 1)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM sector_membership WHERE source='manual'"
            ).fetchone()[0], 1)

    def test_invalid_or_incomplete_snapshot_does_not_delete_existing_data(self):
        self.updater(self.snapshot()).update()
        empty = SectorSnapshot("akshare_sw", "2026-11-19", (), ())
        with self.assertRaises(SecurityMasterSourceError):
            self.updater(empty).update()
        loaded = {item.code: item for item in self.storage.load_securities()}
        self.assertEqual(loaded["600000"].sectors, ("SW1:801120", "银行"))

    def test_unmatched_membership_ratio_rejects_snapshot_without_mutation(self):
        self.updater(self.snapshot()).update()
        bad = SectorSnapshot(
            "akshare_sw", "2026-11-19",
            (SectorRecord("SW1:801010", "农林牧渔"),),
            (SectorMembershipRecord("999999", "SW1:801010"),),
        )
        with self.assertRaises(SecurityMasterSourceError):
            self.updater(bad).update()
        loaded = {item.code: item for item in self.storage.load_securities()}
        self.assertEqual(loaded["600000"].sectors, ("SW1:801120", "银行"))

    def test_membership_cannot_reference_unknown_sector(self):
        bad = SectorSnapshot(
            "akshare_sw", "2026-11-19",
            (SectorRecord("SW1:801010", "农林牧渔"),),
            (SectorMembershipRecord("600000", "SW1:999999"),),
        )
        with self.assertRaises(SecurityMasterSourceError):
            self.updater(bad).update()
        with self.storage.connect() as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM sector_master WHERE provider='akshare_sw'"
            ).fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
