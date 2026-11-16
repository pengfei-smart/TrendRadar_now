# 本文件验证正式退市生命周期事件必须绑定已审核的直接证据。
"""Tests for the formal delisting lifecycle write guard."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from trendradar.market_data.storage import MarketStorage


class LifecycleWriteGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage = MarketStorage(str(Path(self.temp_dir.name) / "market.db"))

    def tearDown(self):
        self.temp_dir.cleanup()

    def _candidate(
        self, *, review_status="verified", event_date="2020-02-25",
        event_type="delisting_effective",
    ):
        candidate_id = "evidence-actual-001"
        payload = {
            "metadata": {
                "evidence_kind": "final_termination_notice",
                "source_authority": "neeq_official",
                "evidence_status": "crosschecked",
                "confirmed_legal_fact": True,
                "date_semantics": "actual_termination",
            }
        }
        self.candidate_payload = {
            "candidate_id": candidate_id,
            "candidate_kind": "event",
            "security_code": "430134",
            "event_type": event_type,
            "event_date": event_date,
            "date_status": "confirmed",
            "source_name": "neeq_official",
            "source_title": "正式终止挂牌公告",
            "source_url": "https://www.neeq.com.cn/example.pdf",
            "retrieved_at": "2026-11-16T12:00:00+08:00",
            "content_sha256": hashlib.sha256(candidate_id.encode()).hexdigest(),
            "evidence_grade": "official",
            "payload_json": json.dumps(payload, ensure_ascii=False),
        }
        self.storage.save_lifecycle_evidence_candidates([self.candidate_payload])
        self.storage.review_lifecycle_evidence_candidate(
            candidate_id, review_status=review_status, reviewer="tester",
            reviewed_at="2026-11-16T12:01:00+08:00",
        )
        return candidate_id

    def _event(self, **overrides):
        event = {
            "event_id": "event-actual-001",
            "security_code": "430134",
            "event_type": "delisting_effective",
            "event_date": "2020-02-25",
            "delisting_effective_date": "2020-02-25",
            "date_status": "confirmed",
            "evidence_grade": "official",
            "source_title": "正式终止挂牌公告",
            "source_url": "https://www.neeq.com.cn/example.pdf",
        }
        event.update(overrides)
        return event

    def test_actual_delisting_requires_evidence_candidate_id(self):
        with self.assertRaisesRegex(ValueError, "evidence_candidate_id"):
            self.storage.save_security_lifecycle_events([self._event()])

    def test_actual_delisting_rejects_unverified_candidate(self):
        candidate_id = self._candidate(review_status="pending")
        with self.assertRaisesRegex(ValueError, "verified"):
            self.storage.save_security_lifecycle_events([
                self._event(evidence_candidate_id=candidate_id)
            ])

    def test_actual_delisting_rejects_candidate_date_mismatch(self):
        candidate_id = self._candidate(event_date="2020-02-24")
        with self.assertRaisesRegex(ValueError, "日期必须与"):
            self.storage.save_security_lifecycle_events([
                self._event(evidence_candidate_id=candidate_id)
            ])

    def test_actual_delisting_rejects_candidate_with_different_event_type(self):
        candidate_id = self._candidate(event_type="trading_suspension")
        with self.assertRaisesRegex(ValueError, "delisting_effective类型"):
            self.storage.save_security_lifecycle_events([
                self._event(evidence_candidate_id=candidate_id)
            ])

    def test_actual_delisting_requires_event_dates_to_match(self):
        candidate_id = self._candidate()
        with self.assertRaisesRegex(ValueError, "event_date必须与"):
            self.storage.save_security_lifecycle_events([
                self._event(
                    evidence_candidate_id=candidate_id,
                    event_date="2020-02-24",
                )
            ])

    def test_actual_delisting_accepts_verified_direct_official_evidence(self):
        candidate_id = self._candidate()
        saved = self.storage.save_security_lifecycle_events([
            self._event(evidence_candidate_id=candidate_id)
        ])
        self.assertEqual(saved, 1)
        rows = self.storage.list_security_lifecycle_events(security_code="430134")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["delisting_effective_date"], "2020-02-25")
        links = self.storage.list_security_lifecycle_event_evidence(
            event_id="event-actual-001"
        )
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["candidate_id"], candidate_id)
        self.assertEqual(links[0]["relation_type"], "supports_procedure")

    def test_evidence_change_with_same_hash_resets_candidate_review(self):
        candidate_id = self._candidate()
        changed = dict(
            self.candidate_payload,
            source_url="https://www.neeq.com.cn/changed-before-link.pdf",
        )
        self.storage.save_lifecycle_evidence_candidates([changed])
        with self.storage.connect() as connection:
            status = connection.execute(
                "SELECT review_status FROM lifecycle_evidence_candidates WHERE candidate_id=?",
                (candidate_id,),
            ).fetchone()[0]
        self.assertEqual(status, "pending")
        with self.assertRaisesRegex(ValueError, "verified"):
            self.storage.save_security_lifecycle_events([
                self._event(evidence_candidate_id=candidate_id)
            ])

    def test_linked_evidence_candidate_cannot_be_modified(self):
        candidate_id = self._candidate()
        self.storage.save_security_lifecycle_events([
            self._event(evidence_candidate_id=candidate_id)
        ])
        changed = dict(
            self.candidate_payload,
            source_url="https://www.neeq.com.cn/changed.pdf",
        )
        with self.assertRaisesRegex(ValueError, "不可修改证据内容"):
            self.storage.save_lifecycle_evidence_candidates([changed])

    def test_linked_formal_event_identity_and_date_cannot_be_rewritten(self):
        first_candidate = self._candidate()
        self.storage.save_security_lifecycle_events([
            self._event(evidence_candidate_id=first_candidate)
        ])
        second = dict(
            self.candidate_payload,
            candidate_id="evidence-actual-002",
            event_date="2020-02-26",
            content_sha256=hashlib.sha256(b"evidence-actual-002").hexdigest(),
        )
        self.storage.save_lifecycle_evidence_candidates([second])
        self.storage.review_lifecycle_evidence_candidate(
            second["candidate_id"], review_status="verified", reviewer="tester",
            reviewed_at="2026-11-16T12:03:00+08:00",
        )
        with self.assertRaisesRegex(ValueError, "不可通过UPSERT修改"):
            self.storage.save_security_lifecycle_events([
                self._event(
                    evidence_candidate_id=second["candidate_id"],
                    event_date="2020-02-26",
                    delisting_effective_date="2020-02-26",
                )
            ])

    def test_linked_evidence_candidate_cannot_be_downgraded(self):
        candidate_id = self._candidate()
        self.storage.save_security_lifecycle_events([
            self._event(evidence_candidate_id=candidate_id)
        ])
        with self.assertRaisesRegex(ValueError, "不得降级"):
            self.storage.review_lifecycle_evidence_candidate(
                candidate_id, review_status="rejected", reviewer="tester",
                reviewed_at="2026-11-16T12:02:00+08:00",
            )

    def test_mixed_batch_is_not_partially_written_when_delisting_evidence_fails(self):
        normal_event = {
            "event_id": "event-suspension-before-failure",
            "security_code": "430134",
            "event_type": "trading_suspension",
            "event_date": "2020-02-20",
        }
        with self.assertRaisesRegex(ValueError, "evidence_candidate_id"):
            self.storage.save_security_lifecycle_events([
                normal_event,
                self._event(),
            ])
        self.assertEqual(self.storage.list_security_lifecycle_events(), [])

    def test_non_delisting_lifecycle_event_keeps_existing_behavior(self):
        saved = self.storage.save_security_lifecycle_events([{
            "event_id": "event-suspension-001",
            "security_code": "430134",
            "event_type": "trading_suspension",
            "event_date": "2020-02-20",
        }])
        self.assertEqual(saved, 1)


if __name__ == "__main__":
    unittest.main()
