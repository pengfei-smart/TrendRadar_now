# 本文件验证NEEQ终止挂牌正式晋升必须满足双重审核、身份/附件/日期一致性和明确人工授权。
"""Tests for explicit NEEQ termination promotion."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from trendradar.market_data.storage import MarketStorage
from trendradar.market_data.neeq_termination_promotion import promote_neeq_termination


class NEEQTerminationPromotionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.storage = MarketStorage(str(Path(self.temp.name) / "market.db"))
        self.code = "832480"
        self.url = "https://www.neeq.com.cn/disclosure/2026/2026-11-03/notice.pdf"
        self.meta_url = "https://www.neeq.com.cn/disclosure/announcement.html"
        self.meta_hash = "a" * 64
        self.pdf_hash = "b" * 64
        self.identity_id = "identity-1"
        self.lifecycle_id = "lifecycle-1"
        self.storage.save_neeq_issuer_identity_candidates([{
            "identity_candidate_id": self.identity_id, "company_code": self.code,
            "company_name": "摘牌商网", "disclosure_title": "关于公司股票终止挂牌的公告",
            "publish_date": "2026-11-03", "attachment_url": self.url,
            "metadata_source_url": self.meta_url, "metadata_response_sha256": self.meta_hash,
            "retrieved_at": "2026-11-03T10:00:00+08:00", "raw_record": {"companyCd": self.code},
        }])
        self.storage.review_neeq_issuer_identity_candidate(
            self.identity_id, review_status="verified", reviewer="reviewer",
            reviewed_at="2026-11-14T10:00:00+08:00", review_notes="身份已核验",
        )
        self.storage.save_lifecycle_evidence_candidates([{
            "candidate_id": self.lifecycle_id, "candidate_kind": "event",
            "security_code": self.code, "exchange": "NEEQ", "event_type": "delisting",
            "event_date": "2026-11-04", "date_status": "unknown",
            "source_name": "NEEQ", "source_title": "[临时公告]摘牌商网:关于公司股票在全国中小企业股份转让系统终止挂牌的公告",
            "source_url": self.url, "retrieved_at": "2026-11-03T10:00:00+08:00",
            "content_sha256": self.pdf_hash, "evidence_excerpt": "公司股票将于2026年11月4日终止挂牌",
            "evidence_grade": "official",
            "payload_json": {
                "issuer_identity_hint": {
                    "issuer_code": self.code, "match_method": "exact_attachment_path",
                    "attachment_url": self.url, "metadata_response_sha256": self.meta_hash,
                },
                "parsed_evidence": {
                    "security_codes": [self.code],
                    "date_evidence": [{
                        "value": "2026-11-04", "date_type": "delisting_date",
                        "date_status": "unknown", "context": "公司股票将于2026年11月4日终止挂牌",
                    }],
                },
            },
        }])
        self.storage.review_lifecycle_evidence_candidate(
            self.lifecycle_id, review_status="verified", reviewer="reviewer",
            reviewed_at="2026-11-14T10:00:00+08:00", review_notes="公告及日期已核验",
        )

    def tearDown(self):
        self.temp.cleanup()

    def promote(self, **overrides):
        args = {
            "identity_candidate_id": self.identity_id,
            "lifecycle_candidate_id": self.lifecycle_id,
            "effective_date": "2026-11-04",
            "reviewer": "legal-reviewer", "legal_basis": "全国股转系统正式终止挂牌公告及审核记录",
            "promoted_at": "2026-11-14T10:00:00+08:00",
        }
        args.update(overrides)
        return promote_neeq_termination(self.storage, **args)

    def test_promotes_both_formal_records_in_one_action(self):
        result = self.promote()
        self.assertEqual(result["status"], "promoted")
        self.assertEqual(result["formal_issuer_master_writes"], 1)
        self.assertEqual(result["formal_lifecycle_event_writes"], 1)
        self.assertEqual(self.storage.get_neeq_issuer(self.code)["issuer_status"], "terminated")
        with self.storage.connect() as connection:
            row = connection.execute(
                "SELECT event_type,event_date,date_status,evidence_grade FROM security_lifecycle_events WHERE event_id=?",
                (result["event_id"],),
            ).fetchone()
        self.assertEqual(tuple(row), ("delisting_effective", "2026-11-04", "confirmed", "official"))

    def test_requires_both_candidates_verified(self):
        self.storage.review_lifecycle_evidence_candidate(
            self.lifecycle_id, review_status="pending", reviewer="reviewer",
            reviewed_at="2026-11-14T10:00:00+08:00",
        )
        with self.assertRaisesRegex(ValueError, "verified"):
            self.promote()

    def test_rejects_metadata_only_identity_without_body_code(self):
        with self.storage.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM lifecycle_evidence_candidates WHERE candidate_id=?",
                (self.lifecycle_id,),
            ).fetchone()
            payload = json.loads(row["payload_json"])
            payload["parsed_evidence"]["security_codes"] = []
            connection.execute(
                "UPDATE lifecycle_evidence_candidates SET payload_json=? WHERE candidate_id=?",
                (json.dumps(payload, ensure_ascii=False), self.lifecycle_id),
            )
        with self.assertRaisesRegex(ValueError, "候选条件"):
            self.promote()

    def test_rejects_date_not_in_announcement_evidence(self):
        with self.assertRaisesRegex(ValueError, "日期证据"):
            self.promote(effective_date="2026-11-03")

    def test_rejects_identity_attachment_mismatch(self):
        with self.storage.connect() as connection:
            payload = json.loads(connection.execute(
                "SELECT payload_json FROM lifecycle_evidence_candidates WHERE candidate_id=?",
                (self.lifecycle_id,),
            ).fetchone()["payload_json"])
            payload["issuer_identity_hint"]["attachment_url"] = "https://www.neeq.com.cn/other.pdf"
            connection.execute(
                "UPDATE lifecycle_evidence_candidates SET payload_json=? WHERE candidate_id=?",
                (json.dumps(payload, ensure_ascii=False), self.lifecycle_id),
            )
        with self.assertRaisesRegex(ValueError, "附件URL"):
            self.promote()

    def test_rejects_missing_legal_basis(self):
        with self.assertRaisesRegex(ValueError, "法律依据"):
            self.promote(legal_basis=" ")

    def test_repeated_promotion_is_idempotent(self):
        first = self.promote()
        second = self.promote()
        self.assertEqual(second["status"], "already_promoted")
        self.assertEqual(second["event_id"], first["event_id"])
        self.assertEqual(second["formal_lifecycle_event_writes"], 0)


if ( __name__ == "__main__"):
    unittest.main()
