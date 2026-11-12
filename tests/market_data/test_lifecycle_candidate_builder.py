# 本文件测试生命周期证据候选构建、来源溯源与人工审核隔离规则。
"""Tests for lifecycle evidence candidate building."""

import hashlib
import unittest

from trendradar.market_data.lifecycle_evidence_fetcher import FetchedDocument
from trendradar.market_data.lifecycle_evidence_parser import parse_lifecycle_announcement
from trendradar.market_data.lifecycle_candidate_builder import (
    build_lifecycle_evidence_candidates,
)


class LifecycleCandidateBuilderTests(unittest.TestCase):
    def make_document(self, text, source_id="sse", url="https://www.sse.com.cn/notice/1"):
        body = text.encode("utf-8")
        return FetchedDocument(
            source_id=source_id,
            requested_url=url,
            final_url=url,
            status_code=200,
            content_type="text/html",
            body=body,
            content_sha256=hashlib.sha256(body).hexdigest(),
        )

    def test_builds_event_candidates_with_full_provenance_and_unknown_date(self):
        text = "证券代码000765，公告日期：2024年3月1日，最后交易日为2024-03-08。股票暂停上市。"
        document = self.make_document(text)
        parsed = parse_lifecycle_announcement(text)
        rows = build_lifecycle_evidence_candidates(
            document, parsed, source_title="证券生命周期公告",
            retrieved_at="2026-11-12T10:00:00+08:00",
        )
        self.assertTrue(rows)
        row = next(item for item in rows if item["event_type"] == "suspension")
        self.assertEqual(row["candidate_kind"], "event")
        self.assertEqual(row["security_code"], "000765")
        self.assertEqual(row["exchange"], "SSE")
        self.assertEqual(row["source_name"], "上海证券交易所")
        self.assertEqual(row["source_url"], document.final_url)
        self.assertEqual(row["content_sha256"], document.content_sha256)
        self.assertEqual(row["date_status"], "unknown")
        # 多个日期无法安全对应单一事件时，不把公告日或最后交易日误填为事件日期。
        self.assertEqual(row["event_date"], "")
        self.assertEqual(row["evidence_grade"], "official")
        self.assertIn("parsed_evidence", row["payload_json"])

    def test_corporate_actions_are_separated_from_lifecycle_events(self):
        text = "证券代码000765，现金要约收购，公告日期：2024-03-01。"
        rows = build_lifecycle_evidence_candidates(
            self.make_document(text), parse_lifecycle_announcement(text),
            source_title="现金要约公告",
        )
        row = next(item for item in rows if item["candidate_kind"] == "corporate_action")
        self.assertEqual(row["action_type"], "cash_tender_offer")
        self.assertEqual(row["event_date"], "2024-03-01")

    def test_relationship_candidates_require_explicit_direction_hint(self):
        text = "证券代码000765与001267涉及重整计划。"
        document = self.make_document(text)
        parsed = parse_lifecycle_announcement(text)
        rows = build_lifecycle_evidence_candidates(
            document, parsed, source_title="重整公告",
        )
        self.assertFalse(any(row["candidate_kind"] == "relationship" for row in rows))
        rows = build_lifecycle_evidence_candidates(
            document, parsed, source_title="重整公告",
            relationship_hints=[{
                "security_code": "000765",
                "related_security_code": "001267",
                "relationship_type": "restructuring_successor",
                "evidence_excerpt": "公告明确说明重整承继关系",
            }],
        )
        relation = next(row for row in rows if row["candidate_kind"] == "relationship")
        self.assertEqual(relation["related_security_code"], "001267")
        self.assertFalse(relation["payload_json"]["relationship_direction_inferred"])

    def test_rejects_unregistered_or_tampered_documents(self):
        text = "证券代码000765，终止上市。"
        document = self.make_document(text)
        tampered = FetchedDocument(
            **{**document.__dict__, "content_sha256": "0" * 64}
        )
        with self.assertRaises(ValueError):
            build_lifecycle_evidence_candidates(
                tampered, parse_lifecycle_announcement(text), source_title="公告"
            )
        outside = self.make_document(text, url="https://evil.example/notice")
        with self.assertRaises(ValueError):
            build_lifecycle_evidence_candidates(
                outside, parse_lifecycle_announcement(text), source_title="公告"
            )

    def test_unrecognized_text_does_not_create_candidates(self):
        text = "这是一段没有证券代码和生命周期事件的普通文字。"
        rows = build_lifecycle_evidence_candidates(
            self.make_document(text), parse_lifecycle_announcement(text),
            source_title="普通公告",
        )
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
