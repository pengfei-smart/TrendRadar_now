# 本文件测试NEEQ生命周期证据按发行人、事件类型和明确决定日期保守归并的规则。
"""Tests for conservative NEEQ lifecycle evidence grouping."""

import unittest

from trendradar.market_data.neeq_lifecycle_event_aggregator import (
    build_neeq_lifecycle_event_groups,
)


def candidate(candidate_id, event_type="delisting", decision_date="2026-09-29",
              code="870099", published="2026-11-02", review_status="pending"):
    return {
        "candidate_id": candidate_id,
        "candidate_kind": "event",
        "exchange": "NEEQ",
        "security_code": "",
        "event_type": event_type,
        "source_title": "华搜股份风险提示公告",
        "source_url": f"https://www.neeq.com.cn/disclosure/{candidate_id}.pdf",
        "source_published_at": published,
        "review_status": review_status,
        "payload_json": {
            "issuer_identity_hint": {
                "issuer_code": code,
                "issuer_name": "华搜股份",
                "identity_resolution_status": "metadata_linked_pending_review",
            },
            "parsed_evidence": {
                "date_evidence": [
                    {"value": decision_date, "date_type": "decision_date", "date_status": "unknown"},
                    {"value": "2026-11-04", "date_type": "delisting_date", "date_status": "unknown"},
                ]
            },
            "notice_classification": {
                "category": "delisting_arrangement_risk_notice",
                "confirmed_legal_fact": False,
            },
        },
    }


class NEEQLifecycleEventAggregatorTests(unittest.TestCase):
    def test_groups_same_event_across_distinct_notices_without_dropping_evidence(self):
        rows = [
            candidate("notice-1", published="2026-11-02"),
            candidate("notice-2", published="2026-11-03"),
        ]
        groups = build_neeq_lifecycle_event_groups(rows)
        self.assertEqual(len(groups), 1)
        group = groups[0]
        self.assertEqual(group["issuer_code"], "870099")
        self.assertEqual(group["event_type"], "delisting")
        self.assertEqual(group["decision_date_anchor"], "2026-09-29")
        self.assertEqual(group["evidence_count"], 2)
        self.assertEqual(
            {item["candidate_id"] for item in group["evidence"]},
            {"notice-1", "notice-2"},
        )
        self.assertEqual(group["review_status"], "pending")
        self.assertFalse(group["confirmed_legal_fact"])
        self.assertFalse(group["formal_lifecycle_write_allowed"])

    def test_keeps_different_event_types_in_separate_groups(self):
        groups = build_neeq_lifecycle_event_groups([
            candidate("decision", event_type="delisting_decision"),
            candidate("termination", event_type="delisting"),
        ])
        self.assertEqual(len(groups), 2)
        self.assertEqual(
            {item["event_type"] for item in groups},
            {"delisting_decision", "delisting"},
        )

    def test_does_not_merge_when_decision_date_is_missing_or_conflicting(self):
        missing = candidate("missing", decision_date="")
        conflicting = candidate("conflicting")
        conflicting["payload_json"]["parsed_evidence"]["date_evidence"].append(
            {"value": "2026-09-30", "date_type": "decision_date"}
        )
        groups = build_neeq_lifecycle_event_groups([missing, conflicting])
        self.assertEqual(len(groups), 2)
        self.assertTrue(all(not item["event_group_id"] for item in groups))
        self.assertTrue(all(
            item["grouping_status"] == "insufficient_identity_or_decision_date"
            for item in groups
        ))

    def test_accepts_review_cli_projection_shape(self):
        rows = [
            {
                "candidate_id": "cli-1",
                "exchange": "NEEQ",
                "security_code": "",
                "event_type": "delisting",
                "source_title": "公告一",
                "source_url": "https://www.neeq.com.cn/a.pdf",
                "source_published_at": "2026-11-02",
                "review_status": "pending",
                "issuer_identity_hint": {"issuer_code": "870099", "issuer_name": "华搜股份"},
                "date_evidence": [{"value": "2026-09-29", "date_type": "decision_date"}],
            },
            {
                "candidate_id": "cli-2",
                "exchange": "NEEQ",
                "security_code": "",
                "event_type": "delisting",
                "source_title": "公告二",
                "source_url": "https://www.neeq.com.cn/b.pdf",
                "source_published_at": "2026-11-03",
                "review_status": "pending",
                "issuer_identity_hint": {"issuer_code": "870099", "issuer_name": "华搜股份"},
                "date_evidence": [{"value": "2026-09-29", "date_type": "decision_date"}],
            },
        ]
        groups = build_neeq_lifecycle_event_groups(rows)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["evidence_count"], 2)

    def test_accepts_flattened_review_projection_with_empty_nested_dates(self):
        rows = [
            {
                "candidate_id": "flat-1",
                "exchange": "NEEQ",
                "event_type": "delisting_decision",
                "source_published_at": "2026-11-02",
                "issuer_identity_hint": {"issuer_code": "870099", "issuer_name": "华搜股份"},
                "date_evidence": [{"value": "2026-09-29", "date_type": "decision_date"}],
                "payload_json": {"parsed_evidence": {"date_evidence": []}},
            },
            {
                "candidate_id": "flat-2",
                "exchange": "NEEQ",
                "event_type": "delisting_decision",
                "source_published_at": "2026-11-03",
                "issuer_identity_hint": {"issuer_code": "870099", "issuer_name": "华搜股份"},
                "date_evidence": [{"value": "2026-09-29", "date_type": "decision_date"}],
                "payload_json": {"parsed_evidence": {"date_evidence": []}},
            },
        ]
        groups = build_neeq_lifecycle_event_groups(rows)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["evidence_count"], 2)
        self.assertEqual(groups[0]["decision_date_anchor"], "2026-09-29")

    def test_does_not_merge_across_different_issuers(self):
        groups = build_neeq_lifecycle_event_groups([
            candidate("issuer-a", code="870099"),
            candidate("issuer-b", code="832480"),
        ])
        self.assertEqual(len(groups), 2)
        self.assertEqual(
            {item["issuer_code"] for item in groups},
            {"870099", "832480"},
        )


if __name__ == "__main__":
    unittest.main()
