# 本文件验证生命周期证据从官方文档到SQLite候选池的端到端集成与审核隔离。
"""Integration tests for lifecycle evidence candidate persistence."""

import hashlib
import tempfile
import unittest
from pathlib import Path

from trendradar.market_data.lifecycle_candidate_builder import (
    build_lifecycle_evidence_candidates,
    save_lifecycle_evidence_candidates,
)
from trendradar.market_data.lifecycle_evidence_fetcher import FetchedDocument
from trendradar.market_data.lifecycle_evidence_parser import parse_lifecycle_announcement
from trendradar.market_data.storage import MarketStorage


class LifecycleCandidateIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage = MarketStorage(str(Path(self.temp_dir.name) / "market.db"))

    def tearDown(self):
        self.temp_dir.cleanup()

    @staticmethod
    def make_document(text):
        body = text.encode("utf-8")
        url = "https://www.sse.com.cn/notice/lifecycle-integration"
        return FetchedDocument(
            source_id="sse",
            requested_url=url,
            final_url=url,
            status_code=200,
            content_type="text/html",
            body=body,
            content_sha256=hashlib.sha256(body).hexdigest(),
        )

    def build_rows(self, text):
        document = self.make_document(text)
        parsed = parse_lifecycle_announcement(text)
        return build_lifecycle_evidence_candidates(
            document,
            parsed,
            source_title="生命周期公告集成测试",
            source_published_at="2026-11-12",
            retrieved_at="2026-11-12T10:00:00+08:00",
        )

    def assert_formal_lifecycle_tables_empty(self):
        with self.storage.connect() as connection:
            for table in (
                "security_lifecycle_events",
                "corporate_actions",
                "security_relationships",
            ):
                count = connection.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
                self.assertEqual(count, 0, f"{table} 不应由候选采集自动写入")

    def test_document_to_sqlite_review_and_hash_revision_flow(self):
        original_text = "证券代码000765，股票暂停上市。"
        rows = self.build_rows(original_text)
        self.assertEqual(len(rows), 1)
        self.assertEqual(save_lifecycle_evidence_candidates(self.storage, rows), 1)

        candidate_id = rows[0]["candidate_id"]
        saved = self.storage.list_lifecycle_evidence_candidates(
            review_status="pending", candidate_kind="event", security_code="000765"
        )
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["source_title"], "生命周期公告集成测试")
        self.assertEqual(saved[0]["source_url"], rows[0]["source_url"])
        self.assertEqual(saved[0]["content_sha256"], rows[0]["content_sha256"])
        self.assertEqual(saved[0]["date_status"], "unknown")
        self.assertEqual(saved[0]["evidence_grade"], "official")

        self.storage.review_lifecycle_evidence_candidate(
            candidate_id,
            review_status="verified",
            reviewer="integration-test",
            reviewed_at="2026-11-12T10:05:00+08:00",
            review_notes="仅验证候选审核流程",
        )
        # 同一内容重复采集是幂等更新，不应清除已有审核记录。
        save_lifecycle_evidence_candidates(self.storage, self.build_rows(original_text))
        saved = self.storage.list_lifecycle_evidence_candidates(
            candidate_kind="event", security_code="000765"
        )[0]
        self.assertEqual(saved["review_status"], "verified")
        self.assertEqual(saved["reviewer"], "integration-test")

        # 同一官方公告内容发生修订时，候选身份保持稳定，审核状态必须退回 pending。
        revised_text = "证券代码000765，股票暂停上市。公告补充了相关说明。"
        revised_rows = self.build_rows(revised_text)
        self.assertEqual(revised_rows[0]["candidate_id"], candidate_id)
        self.assertNotEqual(revised_rows[0]["content_sha256"], rows[0]["content_sha256"])
        save_lifecycle_evidence_candidates(self.storage, revised_rows)
        revised = self.storage.list_lifecycle_evidence_candidates(
            candidate_kind="event", security_code="000765"
        )[0]
        self.assertEqual(revised["review_status"], "pending")
        self.assertEqual(revised["reviewer"], "")
        self.assertEqual(revised["reviewed_at"], "")
        self.assertEqual(revised["review_notes"], "")
        self.assertEqual(revised["content_sha256"], revised_rows[0]["content_sha256"])
        self.assert_formal_lifecycle_tables_empty()

    def test_verified_candidates_never_auto_promote_any_candidate_kind(self):
        text = "证券代码000765与001267涉及破产重整及现金要约收购。"
        rows = build_lifecycle_evidence_candidates(
            self.make_document(text),
            parse_lifecycle_announcement(text),
            source_title="重整与要约公告",
            retrieved_at="2026-11-12T10:00:00+08:00",
            relationship_hints=[{
                "security_code": "000765",
                "related_security_code": "001267",
                "relationship_type": "restructuring_successor",
                "evidence_excerpt": "集成测试显式提供的关系方向线索",
            }],
        )
        kinds = {row["candidate_kind"] for row in rows}
        self.assertEqual(kinds, {"event", "corporate_action", "relationship"})
        save_lifecycle_evidence_candidates(self.storage, rows)
        for row in rows:
            self.storage.review_lifecycle_evidence_candidate(
                row["candidate_id"],
                review_status="verified",
                reviewer="integration-test",
                reviewed_at="2026-11-12T10:05:00+08:00",
            )
        self.assertEqual(
            len(self.storage.list_lifecycle_evidence_candidates(review_status="verified")),
            len(rows),
        )
        self.assert_formal_lifecycle_tables_empty()


if __name__ == "__main__":
    unittest.main()
