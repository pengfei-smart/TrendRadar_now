# 本文件测试NEEQ附件任务按URL去重、保留公司关联并拒绝非官方地址。
"""Tests for the read-only NEEQ attachment manifest."""

import unittest

from trendradar.market_data.neeq_attachment_manifest import build_neeq_attachment_manifest


class NEEQAttachmentManifestTests(unittest.TestCase):
    def candidate(self, code, record_id, url, kind="pdf"):
        return {
            "record_id": record_id,
            "company_code": code,
            "company_name": "公司" + code,
            "delisting_date": "2026-01-02",
            "attachment_url": url,
            "candidate_status": "pending_manual_evidence_review",
            "raw_record_sha256": "abc" + code,
            "raw_record": {"attachmentKind": kind},
        }

    def test_groups_same_url_once_and_preserves_each_company_link(self):
        url = "https://www.neeq.com.cn/uploads/1/file/public/2026/a.pdf"
        result = build_neeq_attachment_manifest([
            self.candidate("830001", "1", url),
            self.candidate("830002", "2", url),
        ], generated_at="2026-11-14T00:00:00+00:00")
        self.assertEqual(result["unique_allowed_attachment_count"], 1)
        self.assertEqual(result["candidate_attachment_link_count"], 2)
        self.assertEqual(result["tasks"][0]["linked_candidate_count"], 2)
        self.assertEqual(
            {x["company_code"] for x in result["tasks"][0]["linked_candidates"]},
            {"830001", "830002"},
        )
        self.assertEqual(result["safety"]["network_downloads"], 0)

    def test_keeps_different_urls_as_different_tasks(self):
        base = "https://www.neeq.com.cn/uploads/1/file/public/2026/"
        result = build_neeq_attachment_manifest([
            self.candidate("830001", "1", base + "a.pdf"),
            self.candidate("830001", "2", base + "b.pdf"),
        ])
        self.assertEqual(result["unique_allowed_attachment_count"], 2)

    def test_rejects_non_allowlisted_url_and_tracks_missing_url(self):
        result = build_neeq_attachment_manifest([
            self.candidate("830001", "1", "http://www.neeq.com.cn/a.pdf"),
            self.candidate("830002", "2", ""),
        ])
        self.assertEqual(result["unique_allowed_attachment_count"], 0)
        self.assertEqual(result["rejected_attachment_candidate_count"], 1)
        self.assertEqual(result["missing_attachment_candidate_count"], 1)

    def test_does_not_treat_url_hash_as_company_identity(self):
        url = "https://www.neeq.com.cn/important_news/200006570.html"
        result = build_neeq_attachment_manifest([
            self.candidate("830001", "1", url, "html"),
            self.candidate("830001", "2", url, "html"),
        ])
        self.assertEqual(result["unique_allowed_attachment_count"], 1)
        self.assertEqual(result["tasks"][0]["linked_candidate_count"], 2)
        self.assertEqual(
            {x["record_id"] for x in result["tasks"][0]["linked_candidates"]},
            {"1", "2"},
        )


if __name__ == "__main__":
    unittest.main()
