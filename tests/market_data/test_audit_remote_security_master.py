"""Tests for read-only remote security master audit."""

import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.market_data.audit_remote_security_master import audit_database


class AuditRemoteSecurityMasterTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "market.audit.db"
        self._create_database()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _create_database(self):
        connection = sqlite3.connect(self.db_path)
        try:
            connection.executescript("""
                CREATE TABLE schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                INSERT INTO schema_meta(key, value)
                VALUES ('schema_version', '14');

                CREATE TABLE security_master (
                    code TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    exchange TEXT NOT NULL,
                    board TEXT NOT NULL DEFAULT '',
                    listing_status TEXT NOT NULL DEFAULT 'unknown',
                    listing_status_source TEXT NOT NULL DEFAULT '',
                    listing_status_updated_at TEXT NOT NULL DEFAULT '',
                    risk_flag INTEGER NOT NULL DEFAULT 0
                );
            """)

            rows = [
                ("600000", "浦发银行", "SH", "沪主板", "active", "sse_main_board", 0),
                ("601000", "普通沪市", "SH", "沪主板", "active", "", 0),
                ("000001", "平安银行", "SZ", "深主板", "active", "szse_main_board", 0),
                ("300001", "特锐德", "SZ", "创业板", "active", "szse_chinext", 0),
                ("688001", "科创样本", "SH", "科创板", "active", "sse_star", 0),
                ("830001", "北交样本", "BJ", "北交所", "active", "neeq", 0),
                ("600002", "风险样本", "SH", "沪主板", "active", "sse_main_board", 1),
                ("600003", "未知样本", "SH", "沪主板", "unknown", "", 0),
                ("600004", "停牌样本", "SH", "沪主板", "suspended", "sse_main_board", 0),
                ("600005", "退市样本", "SH", "沪主板", "delisted", "official_delisting", 0),
            ]

            connection.executemany("""
                INSERT INTO security_master(
                    code, name, exchange, board, listing_status,
                    listing_status_source, risk_flag
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, rows)
            connection.commit()
        finally:
            connection.close()

    def test_reports_schema_and_total_security_count(self):
        report = audit_database(self.db_path)

        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["schema_version"], 14)
        self.assertEqual(report["security_total"], 10)

    def test_counts_listing_statuses_and_sources(self):
        report = audit_database(self.db_path)

        self.assertEqual(report["listing_status_counts"]["active"], 7)
        self.assertEqual(report["listing_status_counts"]["unknown"], 1)
        self.assertEqual(report["listing_status_counts"]["suspended"], 1)
        self.assertEqual(report["listing_status_counts"]["delisted"], 1)
        self.assertEqual(
            report["active_without_listing_status_source_count"], 1
        )

    def test_excludes_star_bj_risk_unknown_suspended_and_delisted(self):
        report = audit_database(self.db_path)

        self.assertEqual(report["eligible_total"], 4)
        self.assertEqual(
            report["eligible_board_counts"],
            {"沪主板": 2, "深主板": 1, "创业板": 1},
        )

    def test_retains_delisted_records_in_master(self):
        report = audit_database(self.db_path)

        self.assertEqual(report["delisted_master_count"], 1)
        self.assertEqual(report["eligible_total"], 4)

    def test_reports_exchange_and_board_distribution(self):
        report = audit_database(self.db_path)

        self.assertEqual(report["exchange_counts"]["SH"], 7)
        self.assertEqual(report["exchange_counts"]["SZ"], 2)
        self.assertEqual(report["exchange_counts"]["BJ"], 1)
        self.assertEqual(report["excluded_star_count"], 1)
        self.assertEqual(report["excluded_bj_count"], 1)

    def test_database_file_is_unchanged(self):
        before = hashlib.sha256(self.db_path.read_bytes()).hexdigest()

        audit_database(self.db_path)

        after = hashlib.sha256(self.db_path.read_bytes()).hexdigest()
        self.assertEqual(before, after)

    def test_rejects_wrong_schema_version(self):
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                "UPDATE schema_meta SET value='13' WHERE key='schema_version'"
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaises(ValueError):
            audit_database(self.db_path)

    def test_rejects_missing_database(self):
        missing = self.db_path.parent / "missing.db"

        with self.assertRaises((ValueError, FileNotFoundError)):
            audit_database(missing)


    def test_reports_read_only_safety_flags(self):
        report = audit_database(self.db_path)

        self.assertEqual(report["database_open_mode"], "ro")
        self.assertIs(report["sqlite_query_only"], True)
        self.assertIs(report["write_performed"], False)
        self.assertIs(report["remote_upload_performed"], False)
        self.assertIs(report["database_artifact_uploaded"], False)

    def test_reports_missing_timestamp_and_risk_flag_counts(self):
        report = audit_database(self.db_path)

        self.assertEqual(
            report["active_without_listing_status_updated_at_count"], 7
        )
        self.assertEqual(report["risk_flagged_count"], 1)



if __name__ == "__main__":
    unittest.main()
