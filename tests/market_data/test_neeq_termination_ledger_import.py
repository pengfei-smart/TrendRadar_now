# 本文件验证 NEEQ 历史候选导入器的哈希校验、证据关联及日期事实隔离。
"""Tests for the guarded NEEQ termination evidence ledger importer."""
import hashlib
import json
import unittest

from scripts.market_data.import_neeq_termination_ledger import build_import_payload


class NEEQTerminationLedgerImportTests(unittest.TestCase):
    def setUp(self):
        self.raw = {
            "id": 841, "companyCode": "838047", "companyName": "二中科技",
            "delistingDate": "2019-09-06",
        }
        canonical = json.dumps(
            self.raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        self.raw_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        self.manifest = {"records": [{
            "id": 841, "companyCode": "838047", "companyName": "二中科技",
            "delistingDateISO": "2019-09-06", "raw_record": self.raw,
        }]}
        self.audit = {
            "report_version": "neeq_datatype0_date_audit_v4",
            "safety": {
                "read_only": True,
                "interface_date_promoted_to_actual_termination_date": False,
            },
            "candidates": [{
                "record_id": "841", "security_code": "838047",
                "company_name": "二中科技",
                "interface_result_date": "2019-09-06",
                "raw_record_sha256": self.raw_hash,
                "decision_evidence": [{
                    "evidence_type": "parsed_batch_membership",
                    "membership_type": "formal_decision_company",
                    "notice_number": "股转系统公告〔2019〕984号",
                    "decision_date": None,
                    "conditional_termination_date": "2019-07-09",
                    "source_url": "https://www.neeq.com.cn/important_news/200006230.html",
                }],
            }],
        }

    def test_build_payload_keeps_interface_and_conditional_dates_separate(self):
        payload = build_import_payload(
            self.manifest, self.audit, retrieved_at="2026-11-15T12:00:00+08:00"
        )
        self.assertEqual(len(payload["cases"]), 1)
        self.assertEqual(payload["cases"][0]["interface_result_date"], "2019-09-06")
        self.assertEqual(payload["cases"][0]["case_status"], "pending_review")
        self.assertEqual(len(payload["evidence"]), 1)
        self.assertEqual(len(payload["procedures"]), 1)
        procedure = payload["procedures"][0]
        self.assertEqual(procedure["event_date"], "2019-07-09")
        self.assertEqual(procedure["date_semantics"], "conditional_termination")
        self.assertEqual(procedure["date_status"], "pending")
        self.assertNotIn("actual_termination", {
            item["date_semantics"] for item in payload["procedures"]
        })
        self.assertEqual(payload["links"][0]["relation_type"], "supports_procedure")

    def test_nested_full_manifest_imports_only_audit_scoped_candidates(self):
        full_manifest = {
            "source": "neeq_official_termination_result_list",
            "audit": {"status": "complete"},
            "manifest": {
                "candidate_count": 2,
                "candidates": [
                    self.manifest["records"][0],
                    {
                        "record_id": "999", "company_code": "830001",
                        "company_name": "其他候选",
                        "raw_record": {"id": 999, "companyCode": "830001"},
                    },
                ],
            },
            "safety": {"formal_lifecycle_write_allowed": False},
        }
        payload = build_import_payload(
            full_manifest, self.audit, retrieved_at="2026-11-15T12:00:00+08:00"
        )
        self.assertEqual(len(payload["cases"]), 1)
        self.assertEqual(payload["cases"][0]["source_record_id"], "841")

    def test_raw_record_hash_mismatch_is_rejected(self):
        self.audit["candidates"][0]["raw_record_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
            build_import_payload(
                self.manifest, self.audit, retrieved_at="2026-11-15T12:00:00+08:00"
            )

    def test_unsafe_date_audit_is_rejected(self):
        self.audit["safety"]["interface_date_promoted_to_actual_termination_date"] = True
        with self.assertRaisesRegex(ValueError, "unsafe audit report"):
            build_import_payload(
                self.manifest, self.audit, retrieved_at="2026-11-15T12:00:00+08:00"
            )

    def test_background_membership_is_context_only_when_no_dates(self):
        self.audit["candidates"][0]["decision_evidence"][0].update({
            "membership_type": "background_pending_company",
            "decision_date": None,
            "conditional_termination_date": None,
        })
        payload = build_import_payload(
            self.manifest, self.audit, retrieved_at="2026-11-15T12:00:00+08:00"
        )
        self.assertEqual(payload["procedures"], [])
        self.assertEqual(payload["links"][0]["relation_type"], "context_only")


if __name__ == "__main__":
    unittest.main()
