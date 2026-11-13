# 本文件测试官方生命周期资料来源白名单、重定向限制、文件大小和内容哈希。
"""Tests for lifecycle source registry and bounded evidence fetching."""

import hashlib
import io
import unittest
from email.message import Message

from trendradar.market_data.lifecycle_source_registry import (
    get_source, is_allowed_url, list_sources,
)
from trendradar.market_data.lifecycle_evidence_fetcher import LifecycleEvidenceFetcher


class FakeResponse(io.BytesIO):
    def __init__(self, body=b"official notice", url="https://www.sse.com.cn/test.pdf",
                 content_type="application/pdf", status=200):
        super().__init__(body)
        self._url = url
        self.status = status
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        self.headers["Content-Length"] = str(len(body))

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class FakeOpener:
    def __init__(self, response):
        self.response = response
        self.request = None
        self.timeout = None

    def open(self, request, timeout):
        self.request = request
        self.timeout = timeout
        return self.response


class LifecycleSourceRegistryTests(unittest.TestCase):
    def test_official_sources_are_registered_in_priority_order(self):
        sources = list_sources()
        self.assertEqual({source.source_id for source in sources}, {"sse", "szse", "csrc", "neeq"})
        self.assertEqual(sources[0].priority, 1)
        self.assertEqual(get_source("sse").base_url, "https://www.sse.com.cn/")

    def test_url_allowlist_requires_https_and_exact_registered_host(self):
        self.assertTrue(is_allowed_url("sse", "https://www.sse.com.cn/disclosure/a.pdf"))
        self.assertFalse(is_allowed_url("sse", "http://www.sse.com.cn/a.pdf"))
        self.assertFalse(is_allowed_url("sse", "https://sse.com.cn.evil.example/a.pdf"))
        self.assertFalse(is_allowed_url("sse", "https://example.com/a.pdf"))
        self.assertFalse(is_allowed_url("sse", "https://user@www.sse.com.cn/a.pdf"))

    def test_unknown_source_fails_closed(self):
        with self.assertRaises(ValueError):
            get_source("unknown")


class LifecycleEvidenceFetcherTests(unittest.TestCase):
    def test_fetches_allowlisted_document_and_calculates_sha256(self):
        body = b"%PDF-1.7 official notice"
        opener = FakeOpener(FakeResponse(body))
        fetcher = LifecycleEvidenceFetcher(opener=opener)
        result = fetcher.fetch("sse", "https://www.sse.com.cn/test.pdf")
        self.assertEqual(result.body, body)
        self.assertEqual(result.content_sha256, hashlib.sha256(body).hexdigest())
        self.assertEqual(result.status_code, 200)
        self.assertEqual(opener.timeout, 20.0)
        self.assertEqual(opener.request.get_header("User-agent"), "TrendRadar-LifecycleEvidence/1.0")

    def test_neeq_fetch_uses_verified_browser_style_attachment_headers(self):
        body = b"%PDF-1.7 official NEEQ notice"
        opener = FakeOpener(FakeResponse(
            body,
            url="https://www.neeq.com.cn/disclosure/test.pdf",
        ))
        fetcher = LifecycleEvidenceFetcher(opener=opener)
        result = fetcher.fetch("neeq", "https://www.neeq.com.cn/disclosure/test.pdf")
        self.assertEqual(result.body, body)
        self.assertIn("Chrome/131.0.0.0", opener.request.get_header("User-agent"))
        self.assertEqual(
            opener.request.get_header("Referer"),
            "https://www.neeq.com.cn/disclosure/announcement.html",
        )
        self.assertEqual(opener.request.get_header("Origin"), "https://www.neeq.com.cn")
        self.assertEqual(
            opener.request.get_header("Accept"),
            "application/pdf,application/octet-stream;q=0.9,*/*;q=0.8",
        )

    def test_rejects_unregistered_url_before_network(self):
        opener = FakeOpener(FakeResponse())
        fetcher = LifecycleEvidenceFetcher(opener=opener)
        with self.assertRaises(ValueError):
            fetcher.fetch("sse", "https://example.com/fake.pdf")
        self.assertIsNone(opener.request)

    def test_rejects_redirected_final_url_outside_allowlist(self):
        opener = FakeOpener(FakeResponse(url="https://evil.example/file.pdf"))
        fetcher = LifecycleEvidenceFetcher(opener=opener)
        with self.assertRaises(ValueError):
            fetcher.fetch("sse", "https://www.sse.com.cn/test.pdf")

    def test_rejects_oversized_document(self):
        body = b"x" * 20
        opener = FakeOpener(FakeResponse(body))
        fetcher = LifecycleEvidenceFetcher(max_bytes=10, opener=opener)
        with self.assertRaises(ValueError):
            fetcher.fetch("sse", "https://www.sse.com.cn/test.pdf")

    def test_rejects_unsupported_content_type(self):
        opener = FakeOpener(FakeResponse(content_type="image/png"))
        fetcher = LifecycleEvidenceFetcher(opener=opener)
        with self.assertRaises(ValueError):
            fetcher.fetch("sse", "https://www.sse.com.cn/test.pdf")


if __name__ == "__main__":
    unittest.main()
