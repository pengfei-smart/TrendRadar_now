# 本文件测试NEEQ附件下载器的白名单、内容类型校验、断点续跑和只读安全约束。
"""Tests for bounded and resumable NEEQ attachment downloads."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from trendradar.market_data.lifecycle_evidence_fetcher import FetchedDocument
from trendradar.market_data.neeq_attachment_downloader import download_neeq_attachments


class FakeFetcher:
    def __init__(self, documents=None, error=None):
        self.documents = documents or {}
        self.error = error
        self.calls = []

    def fetch(self, source_id, url):
        self.calls.append((source_id, url))
        if self.error:
            raise self.error
        return self.documents[url]


def make_doc(url, body, content_type):
    return FetchedDocument(
        "neeq", url, url, 200, content_type, body,
        hashlib.sha256(body).hexdigest(),
    )


def make_manifest(url="https://www.neeq.com.cn/uploads/test.pdf", kind="pdf"):
    return {
        "tasks": [{
            "attachment_id": "neeq-attachment-test",
            "url": url,
            "attachment_kinds_reported": [kind],
            "linked_candidate_count": 1,
            "linked_candidates": [{"company_code": "830001"}],
        }]
    }


class NEEQAttachmentDownloaderTests(unittest.TestCase):
    def test_downloads_pdf_validates_magic_and_writes_hash_and_text(self):
        url = "https://www.neeq.com.cn/uploads/test.pdf"
        body = b"%PDF-1.7 official"
        fetcher = FakeFetcher({url: make_doc(url, body, "application/pdf")})
        with tempfile.TemporaryDirectory() as directory:
            result = download_neeq_attachments(
                make_manifest(url), directory, delay_seconds=0, retries=0,
                fetcher=fetcher, extractor=lambda document: "公告正文000001",
            )
            state = json.loads(Path(result["state_file"]).read_text(encoding="utf-8"))
            item = state["results"]["neeq-attachment-test"]
            self.assertEqual(item["download_status"], "downloaded")
            self.assertEqual(item["text_extraction_status"], "extracted")
            self.assertEqual(item["content_sha256"], hashlib.sha256(body).hexdigest())
            self.assertEqual((Path(directory) / item["text_file"]).read_text(encoding="utf-8"),
                             "公告正文000001")
            self.assertFalse(item["legal_fact_confirmed"])
            self.assertFalse(item["formal_lifecycle_write_allowed"])

    def test_rejects_pdf_html_error_page_even_when_content_type_says_pdf(self):
        url = "https://www.neeq.com.cn/uploads/test.pdf"
        fetcher = FakeFetcher({url: make_doc(url, b"<html>blocked</html>", "application/pdf")})
        with tempfile.TemporaryDirectory() as directory:
            result = download_neeq_attachments(
                make_manifest(url), directory, delay_seconds=0, retries=0,
                fetcher=fetcher,
            )
            state = json.loads(Path(result["state_file"]).read_text(encoding="utf-8"))
            self.assertEqual(state["results"]["neeq-attachment-test"]["download_status"], "failed")
            self.assertIn("魔数", state["results"]["neeq-attachment-test"]["error"])

    def test_rejects_html_task_returning_non_html_content_type(self):
        url = "https://www.neeq.com.cn/important_news/123.html"
        fetcher = FakeFetcher({url: make_doc(url, b"<html>notice</html>", "text/plain")})
        with tempfile.TemporaryDirectory() as directory:
            result = download_neeq_attachments(
                make_manifest(url, "html"), directory, delay_seconds=0, retries=0,
                fetcher=fetcher,
            )
            state = json.loads(Path(result["state_file"]).read_text(encoding="utf-8"))
            self.assertEqual(state["results"]["neeq-attachment-test"]["download_status"], "failed")

    def test_rejects_non_allowlisted_url_before_fetch(self):
        url = "https://evil.example/test.pdf"
        fetcher = FakeFetcher()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "白名单"):
                download_neeq_attachments(
                    make_manifest(url), directory, fetcher=fetcher,
                )
        self.assertEqual(fetcher.calls, [])

    def test_resume_skips_valid_cached_download(self):
        url = "https://www.neeq.com.cn/uploads/test.pdf"
        body = b"%PDF-1.7 official"
        with tempfile.TemporaryDirectory() as directory:
            first_fetcher = FakeFetcher({url: make_doc(url, body, "application/pdf")})
            first = download_neeq_attachments(
                make_manifest(url), directory, delay_seconds=0, retries=0,
                fetcher=first_fetcher, extractor=lambda document: "text",
            )
            second_fetcher = FakeFetcher()
            second = download_neeq_attachments(
                make_manifest(url), directory, delay_seconds=0, retries=0,
                fetcher=second_fetcher,
            )
            self.assertEqual(len(first_fetcher.calls), 1)
            self.assertEqual(len(second_fetcher.calls), 0)
            self.assertEqual(second["downloaded_total"], 1)

    def test_retries_and_persists_failure_for_later_resume(self):
        url = "https://www.neeq.com.cn/uploads/test.pdf"
        fetcher = FakeFetcher(error=RuntimeError("temporary"))
        sleeps = []
        with tempfile.TemporaryDirectory() as directory:
            result = download_neeq_attachments(
                make_manifest(url), directory, delay_seconds=0.25, retries=2,
                fetcher=fetcher, sleep=sleeps.append,
            )
            state = json.loads(Path(result["state_file"]).read_text(encoding="utf-8"))
            item = state["results"]["neeq-attachment-test"]
            self.assertEqual(item["download_status"], "failed")
            self.assertEqual(item["attempt_count"], 3)
            self.assertEqual(len(fetcher.calls), 3)
            self.assertEqual(sleeps, [0.25, 0.5])

    def test_rejects_conflicting_or_unknown_reported_kinds(self):
        manifest = make_manifest()
        manifest["tasks"][0]["attachment_kinds_reported"] = ["pdf", "html"]
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "类型"):
                download_neeq_attachments(manifest, directory, fetcher=FakeFetcher())


if __name__ == "__main__":
    unittest.main()
