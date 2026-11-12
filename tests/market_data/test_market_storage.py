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
                    "4",
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


    def test_multi_board_listing_snapshot_rolls_back_as_one_transaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = MarketStorage(str(Path(tmp) / "market.db"))
            storage.upsert_securities([
                Security("600000", "浦发银行", "SH"),
                Security("000001", "平安银行", "SZ"),
            ])
            storage.apply_listing_status_snapshot(
                source_id="szse_a_share_list", exchange="SZ",
                board_scope="main_board_a",
                fetched_at="2026-11-10T15:00:00+08:00",
                source_as_of="2026-11-10", statuses={"000001": "active"},
            )
            batch = [
                {
                    "source_id": "sse_main_board_a", "exchange": "SH",
                    "board_scope": "main_board_a",
                    "fetched_at": "2026-11-10T16:00:00+08:00",
                    "source_as_of": "2026-11-10",
                    "statuses": {"600000": "active"},
                },
                {
                    "source_id": "szse_a_share_list", "exchange": "SZ",
                    "board_scope": "main_board_a",
                    "fetched_at": "2026-11-10T14:00:00+08:00",
                    "source_as_of": "2026-11-10",
                    "statuses": {"000001": "active"},
                },
            ]
            with self.assertRaises(ValueError):
                storage.apply_listing_status_snapshots(batch)
            with storage.connect() as connection:
                sh_count = connection.execute(
                    "SELECT COUNT(*) FROM listing_status_snapshots WHERE exchange='SH'"
                ).fetchone()[0]
                sh_status = connection.execute(
                    "SELECT listing_status FROM security_master WHERE code='600000'"
                ).fetchone()[0]
            self.assertEqual(sh_count, 0)
            self.assertEqual(sh_status, "unknown")


    def test_absent_security_is_downgraded_to_unknown_not_delisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = MarketStorage(str(Path(tmp) / "market.db"))
            storage.upsert_securities([
                Security("600000", "现行上市", "SH"),
                Security("600001", "名单缺席", "SH"),
            ])
            storage.apply_listing_status_snapshot(
                source_id="sse_main_board_a", exchange="SH",
                board_scope="main_board_a",
                fetched_at="2026-11-10T15:00:00+08:00",
                source_as_of="2026-11-10",
                statuses={"600000": "active"},
            )
            with storage.connect() as connection:
                rows = dict(connection.execute(
                    "SELECT code,listing_status FROM security_master"
                ).fetchall())
            self.assertEqual(rows["600000"], "active")
            self.assertEqual(rows["600001"], "unknown")



    def test_corporate_action_upsert_and_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = MarketStorage(str(Path(tmp) / "market.db"))
            storage.save_corporate_actions([{
                "corporate_action_id": "ca-1", "action_type": "cash_tender_offer",
                "action_name": "现金要约", "status": "announced", "evidence_grade": "unverified",
            }])
            storage.save_corporate_actions([{
                "corporate_action_id": "ca-1", "action_type": "cash_tender_offer",
                "action_name": "现金要约（更新）", "status": "completed", "evidence_grade": "official",
            }])
            self.assertEqual(storage.get_corporate_action("ca-1")["status"], "completed")
            self.assertEqual(storage.list_corporate_actions(status="completed")[0]["action_name"],
                             "现金要约（更新）")
            with self.assertRaises(ValueError):
                storage.save_corporate_actions([{
                    "corporate_action_id": "bad", "action_type": "not_an_action",
                }])

    def test_lifecycle_events_and_foreign_key_rollback(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = MarketStorage(str(Path(tmp) / "market.db"))
            storage.save_corporate_actions([{
                "corporate_action_id": "ca-2", "action_type": "delisted_then_relisted",
                "evidence_grade": "official",
            }])
            storage.save_security_lifecycle_events([{
                "event_id": "life-1", "security_code": "000765", "exchange": "SZ",
                "event_type": "delisting_effective", "event_date": "2020-01-01",
                "date_status": "confirmed", "evidence_grade": "official", "corporate_action_id": "ca-2",
            }])
            rows = storage.list_security_lifecycle_events(
                security_code="000765", event_type="delisting_effective"
            )
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["corporate_action_id"], "ca-2")
            with self.assertRaises(Exception):
                storage.save_security_lifecycle_events([{
                    "event_id": "life-invalid", "security_code": "000765",
                    "event_type": "delisting_effective", "corporate_action_id": "missing",
                }])
            self.assertEqual(len(storage.list_security_lifecycle_events()), 1)

    def test_relationships_support_delisted_transfer_and_relisting_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = MarketStorage(str(Path(tmp) / "market.db"))
            storage.save_corporate_actions([{
                "corporate_action_id": "ca-3", "action_type": "delisted_then_relisted",
                "evidence_grade": "official",
            }])
            storage.save_security_relationships([
                {
                    "relationship_id": "rel-transfer", "corporate_action_id": "ca-3",
                    "from_security_code": "000765", "to_security_code": "400038",
                    "relationship_type": "delisted_transfer", "is_listed_successor": 0,
                    "evidence_grade": "official",
                },
                {
                    "relationship_id": "rel-relist", "corporate_action_id": "ca-3",
                    "from_security_code": "000765", "to_security_code": "001267",
                    "relationship_type": "delisted_then_relisted", "is_listed_successor": 1,
                    "evidence_grade": "official",
                },
            ])
            rows = storage.list_security_relationships(from_security_code="000765")
            self.assertEqual(len(rows), 2)
            flags = {row["to_security_code"]: row["is_listed_successor"] for row in rows}
            self.assertEqual(flags, {"400038": 0, "001267": 1})
            with self.assertRaises(ValueError):
                storage.save_security_relationships([{
                    "relationship_id": "self", "from_security_code": "000765",
                    "to_security_code": "000765", "relationship_type": "other",
                }])

    def test_cash_tender_offer_does_not_implicitly_mark_successor_and_duplicate_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = MarketStorage(str(Path(tmp) / "market.db"))
            storage.save_corporate_actions([{
                "corporate_action_id": "ca-cash", "action_type": "cash_tender_offer",
                "evidence_grade": "official",
            }])
            storage.save_security_relationships([{
                "relationship_id": "cash-rel", "corporate_action_id": "ca-cash",
                "from_security_code": "000618", "to_security_code": "601857",
                "relationship_type": "cash_tender_offer", "is_listed_successor": 0,
                "evidence_grade": "official",
            }])
            self.assertEqual(storage.list_security_relationships(
                corporate_action_id="ca-cash"
            )[0]["is_listed_successor"], 0)
            with self.assertRaises(Exception):
                storage.save_security_relationships([{
                    "relationship_id": "cash-rel-duplicate", "corporate_action_id": "ca-cash",
                    "from_security_code": "000618", "to_security_code": "601857",
                    "relationship_type": "cash_tender_offer",
                }])
            self.assertEqual(len(storage.list_security_relationships()), 1)


if __name__ == "__main__":
    unittest.main()
