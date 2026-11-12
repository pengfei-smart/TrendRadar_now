# 本文件测试官方公告入口检查器对表单、iframe、脚本和白名单链接的识别。
"""Tests for official lifecycle announcement page inspection."""

import hashlib
import unittest

from trendradar.market_data.lifecycle_evidence_fetcher import FetchedDocument
from trendradar.market_data.lifecycle_source_inspector import (
    SOURCE_PAGES,
    inspect_announcement_page,
    inspect_script_asset,
)


class FakeFetcher:
    def __init__(self, body, content_type="text/html"):
        self.document = FetchedDocument(
            source_id="neeq",
            requested_url="https://www.neeq.com.cn/disclosure/announcement.html",
            final_url="https://www.neeq.com.cn/disclosure/announcement.html",
            status_code=200,
            content_type=content_type,
            body=body,
            content_sha256=hashlib.sha256(body).hexdigest(),
        )

    def fetch(self, source_id, url):
        if source_id != "neeq":
            raise AssertionError("unexpected source")
        return self.document


class LifecycleSourceInspectorTests(unittest.TestCase):
    def test_source_pages_are_explicit_official_entry_points(self):
        self.assertEqual(set(SOURCE_PAGES), {"sse", "szse", "csrc", "neeq"})
        for source_id, urls in SOURCE_PAGES.items():
            self.assertTrue(urls)
            self.assertTrue(all(url.startswith("https://") for url in urls))

    def test_inspects_form_fields_iframe_and_scripts_without_values(self):
        html = b"""
        <html><head><title>Official Notice Search</title>
        <script src="/assets/search.js"></script></head>
        <body>
          <form action="/query" method="post">
            <input name="keyword" type="text" placeholder="code or title">
            <input name="token" type="hidden" value="secret-token">
            <select name="category"><option value="a">A</option></select>
            <button name="submit" type="submit">Search</button>
          </form>
          <iframe src="/notice/list"></iframe>
          <a href="/notice/123.pdf">Notice PDF</a>
        </body></html>
        """
        result = inspect_announcement_page(
            "neeq",
            "https://www.neeq.com.cn/disclosure/announcement.html",
            fetcher=FakeFetcher(html),
        )
        self.assertEqual(result["title"], "Official Notice Search")
        self.assertEqual(result["forms"][0]["method"], "post")
        names = {field["name"] for field in result["forms"][0]["fields"]}
        self.assertIn("keyword", names)
        self.assertIn("category", names)
        self.assertNotIn("value", result["forms"][0]["fields"][0])
        self.assertNotIn("secret-token", str(result))
        self.assertTrue(result["iframes"][0]["allowed_by_source_registry"])
        self.assertTrue(result["scripts"][0]["allowed_by_source_registry"])
        self.assertTrue(result["links"][0]["allowed_by_source_registry"])

    def test_external_form_action_is_marked_not_followed(self):
        html = b'<form action="https://outside.example/search"><input name="q"></form>'
        result = inspect_announcement_page(
            "neeq",
            "https://www.neeq.com.cn/disclosure/announcement.html",
            fetcher=FakeFetcher(html),
        )
        self.assertFalse(result["forms"][0]["action"]["allowed_by_source_registry"])

    def test_rejects_unregistered_page_before_fetch(self):
        class NeverFetcher:
            def fetch(self, *args):
                raise AssertionError("must not fetch")

        with self.assertRaises(ValueError):
            inspect_announcement_page(
                "neeq", "https://outside.example/page", fetcher=NeverFetcher()
            )

    def test_rejects_non_html_page(self):
        with self.assertRaisesRegex(ValueError, "不是 HTML"):
            inspect_announcement_page(
                "neeq",
                "https://www.neeq.com.cn/disclosure/announcement.html",
                fetcher=FakeFetcher(b"%PDF", content_type="application/pdf"),
            )


    def test_extracts_endpoint_like_literals_from_allowlisted_javascript(self):
        source = b"""
        const endpoint = "/api/disclosure/search.json";
        const detail = "/disclosure/announcement/detail.shtml";
        const label = "search";
        """
        class ScriptFetcher:
            def fetch(self, source_id, url):
                return FetchedDocument(
                    source_id=source_id,
                    requested_url=url,
                    final_url=url,
                    status_code=200,
                    content_type="application/javascript",
                    body=source,
                    content_sha256=hashlib.sha256(source).hexdigest(),
                )

        result = inspect_script_asset(
            "neeq",
            "https://www.neeq.com.cn/assets/disclosure.js",
            fetcher=ScriptFetcher(),
        )
        self.assertIn("/api/disclosure/search.json", result["endpoint_candidates"])
        self.assertIn("/disclosure/announcement/detail.shtml", result["endpoint_candidates"])
        self.assertEqual(result["content_length"], len(source))

    def test_rejects_external_script_asset_before_fetch(self):
        class NeverFetcher:
            def fetch(self, *args):
                raise AssertionError("must not fetch")

        with self.assertRaises(ValueError):
            inspect_script_asset(
                "neeq", "https://outside.example/search.js", fetcher=NeverFetcher()
            )


if __name__ == "__main__":
    unittest.main()
