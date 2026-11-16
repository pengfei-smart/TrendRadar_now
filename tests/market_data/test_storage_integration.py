# 本文件验证证券主数据更新器与SQLite市场库集成、行情保存、外键约束及schema幂等初始化。
"""Integration tests for market storage with security-master and quote ingestion."""

import sqlite3
from contextlib import closing
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
        with closing(sqlite3.connect(self.db_path)) as connection:
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
        with closing(sqlite3.connect(self.db_path)) as connection:
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
        with closing(sqlite3.connect(self.db_path)) as connection:
            version = connection.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()[0]
            self.assertEqual(version, "8")
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM security_master").fetchone()[0], 0
            )

    def test_neeq_termination_ledger_schema_is_separate_and_idempotent(self):
        MarketStorage(str(self.db_path))
        with closing(sqlite3.connect(self.db_path)) as connection:
            tables = {
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            expected = {
                "neeq_termination_case",
                "neeq_termination_evidence",
                "neeq_termination_procedure",
                "neeq_termination_evidence_link",
                "neeq_termination_review",
                "neeq_termination_evidence_search",
            }
            self.assertTrue(expected.issubset(tables))
            case_foreign_keys = connection.execute(
                "PRAGMA foreign_key_list(neeq_termination_case)"
            ).fetchall()
            self.assertEqual(case_foreign_keys, [])
            evidence_foreign_keys = connection.execute(
                "PRAGMA foreign_key_list(neeq_termination_evidence)"
            ).fetchall()
            self.assertEqual(evidence_foreign_keys, [])
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM security_master").fetchone()[0], 0
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM neeq_issuer_master").fetchone()[0], 0
            )

    def test_neeq_termination_ledger_write_read_idempotency_and_review_guard(self):
        case = {
            "case_id": "neeq-case-838047-841",
            "company_code": "838047",
            "company_name_snapshot": "二中科技",
            "source_record_id": "841",
            "source_api": "neeq_termination_result_api",
            "interface_result_date": "2019-09-06",
            "interface_create_at": "2020-02-21T17:23:46+08:00",
            "raw_record": {
                "id": 841, "companyCode": "838047",
                "companyName": "二中科技", "delistingDate": "2019-09-06",
            },
        }
        self.assertEqual(self.storage.save_neeq_termination_cases([case]), 1)
        self.assertEqual(self.storage.save_neeq_termination_cases([case]), 1)
        loaded = self.storage.list_neeq_termination_cases(company_code="838047")
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["raw_record"]["id"], 841)
        self.assertEqual(loaded[0]["case_status"], "pending_review")

        evidence = {
            "evidence_id": "neeq-evidence-2019-984",
            "evidence_kind": "batch_notice",
            "source_authority": "neeq_official",
            "notice_number": "股转系统公告〔2019〕984号",
            "document_title": "关于作出终止46家未披露2018年年度报告公司股票挂牌决定的公告",
            "source_url": "https://www.neeq.com.cn/important_news/200006230.html",
            "retrieved_at": "2026-11-15T10:00:00+08:00",
            "evidence_status": "crosschecked",
            "metadata": {"appendix": "Appendix 2"},
        }
        self.assertEqual(self.storage.save_neeq_termination_evidence([evidence]), 1)
        self.assertEqual(self.storage.save_neeq_termination_evidence([evidence]), 1)
        self.assertEqual(self.storage.save_neeq_termination_procedures([{
            "procedure_id": "neeq-procedure-838047-decision",
            "case_id": case["case_id"],
            "procedure_type": "decision_issued",
            "event_date": "2019-07-01",
            "date_semantics": "decision_issued",
            "date_status": "confirmed",
        }]), 1)
        self.assertEqual(self.storage.link_neeq_termination_evidence([{
            "link_id": "neeq-link-838047-984",
            "case_id": case["case_id"],
            "evidence_id": evidence["evidence_id"],
            "procedure_id": "neeq-procedure-838047-decision",
            "relation_type": "supports_procedure",
        }]), 1)
        review_id = self.storage.record_neeq_termination_review({
            "case_id": case["case_id"],
            "evidence_id": evidence["evidence_id"],
            "procedure_id": "neeq-procedure-838047-decision",
            "review_status": "needs_more_evidence",
            "conclusion_code": "decision_evidence_only",
            "reviewer": "test-reviewer",
            "reviewed_at": "2026-11-15T10:30:00+08:00",
            "review_notes": "尚未找到复核及最终终止公告。",
        })
        self.assertGreater(review_id, 0)
        with self.assertRaises(ValueError):
            self.storage.record_neeq_termination_review({
                "case_id": case["case_id"],
                "review_status": "verified",
                "conclusion_code": "actual_termination_confirmed",
                "reviewer": "test-reviewer",
                "reviewed_at": "2026-11-15T10:31:00+08:00",
            })
        with closing(sqlite3.connect(self.db_path)) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM neeq_termination_case").fetchone()[0], 1
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM neeq_termination_review").fetchone()[0], 1
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM security_lifecycle_events").fetchone()[0], 0
            )

    def test_neeq_evidence_search_history_is_append_only(self):
        self.storage.save_neeq_termination_cases([{
            "case_id": "neeq-result-838047-841",
            "company_code": "838047",
            "company_name_snapshot": "二中科技",
            "source_record_id": "841",
            "source_api": "neeq_termination_result_api",
            "interface_result_date": "2019-09-06",
            "raw_record": {"id": 841, "companyCode": "838047"},
        }])
        search = {
            "search_id": "search-838047-final-not-found",
            "case_id": "neeq-result-838047-841",
            "evidence_kind": "final_termination_notice",
            "search_status": "searched_not_found",
            "searched_at": "2026-11-16T15:00:00+08:00",
            "search_scope": "全国股转系统公开公告检索",
            "search_notes": "检索范围内未找到，不代表公告不存在。",
            "source_urls": ["https://www.neeq.com.cn/disclosure/termination_listing.html"],
            "reviewer": "test-reviewer",
        }
        self.assertEqual(self.storage.record_neeq_termination_evidence_search(search), 1)
        self.assertEqual(self.storage.record_neeq_termination_evidence_search(search), 1)
        rows = self.storage.list_neeq_termination_evidence_searches(
            case_id="neeq-result-838047-841",
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["search_status"], "searched_not_found")
        self.assertEqual(rows[0]["source_urls"], search["source_urls"])
        with self.assertRaises(ValueError):
            self.storage.record_neeq_termination_evidence_search(dict(
                search, search_id="search-invalid-case", case_id="missing-case",
            ))

    def test_neeq_identity_candidates_do_not_auto_create_issuer_master(self):
        self.storage.save_neeq_issuer_identity_candidates([{
            "identity_candidate_id": "neeq-pdf-832480-001",
            "company_code": "832480",
            "company_name": "摘牌商网",
            "disclosure_title": "股票进入摘牌整理期风险提示公告",
            "publish_date": "2026-11-02",
            "attachment_url": "https://www.neeq.com.cn/disclosure/sample.pdf",
            "metadata_source_url": "https://www.neeq.com.cn/disclosureInfoController/companyAnnouncement.do",
            "metadata_response_sha256": "a" * 64,
            "retrieved_at": "2026-11-13T10:00:00+08:00",
            "raw_record": {"companyCd": "832480", "companyName": "摘牌商网"},
        }])
        candidate = self.storage.list_neeq_issuer_identity_candidates(company_code="832480")[0]
        self.assertEqual(candidate["review_status"], "pending")
        self.assertEqual(candidate["raw_record"]["companyCd"], "832480")
        self.assertIsNone(self.storage.get_neeq_issuer("832480"))

    def test_neeq_issuer_master_is_separate_from_a_share_security_master(self):
        self.storage.upsert_neeq_issuer_master([{
            "company_code": "832480", "current_name": "摘牌商网",
            "market_layer": "base", "issuer_status": "delisting_arrangement",
            "source_name": "NEEQ", "source_url": "https://www.neeq.com.cn/disclosure/sample.pdf",
            "source_sha256": "b" * 64, "source_retrieved_at": "2026-11-13T10:00:00+08:00",
        }])
        self.assertEqual(self.storage.get_neeq_issuer("832480")["current_name"], "摘牌商网")
        with closing(sqlite3.connect(self.db_path)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM security_master").fetchone()[0], 0)

    def test_listing_snapshot_accepts_302_chinext_and_marks_absence_unknown(self):
        self.storage.upsert_securities([
            Security("302132", "创业板302样本", "SZ", listing_status="unknown"),
            Security("302133", "创业板302缺席样本", "SZ", listing_status="active"),
        ])
        result = self.storage.apply_listing_status_snapshots([{
            "source_id": "szse_a_share_list", "exchange": "SZ", "board_scope": "chinext",
            "fetched_at": "2026-11-10T12:30:00+00:00", "source_as_of": "",
            "statuses": {"302132": "active"}, "source_url": "https://www.szse.cn/",
        }])
        self.assertEqual(result["SZ_chinext"], 1)
        with closing(sqlite3.connect(self.db_path)) as connection:
            rows = dict(connection.execute(
                "SELECT code,listing_status FROM security_master"
            ).fetchall())
        self.assertEqual(rows["302132"], "active")
        self.assertEqual(rows["302133"], "unknown")

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
