# 本文件测试官方生命周期公告文档文本提取，确保二进制内容不会被误解析为噪声。
"""Tests for safe text extraction from fetched lifecycle documents."""

import hashlib
import unittest

from trendradar.market_data.lifecycle_document_text import extract_lifecycle_document_text
from trendradar.market_data.lifecycle_evidence_fetcher import FetchedDocument


def document(body, content_type):
    return FetchedDocument(
        source_id="sse",
        requested_url="https://www.sse.com.cn/test",
        final_url="https://www.sse.com.cn/test",
        status_code=200,
        content_type=content_type,
        body=body,
        content_sha256=hashlib.sha256(body).hexdigest(),
    )


class LifecycleDocumentTextTests(unittest.TestCase):
    def test_extracts_visible_html_and_skips_script_style_and_svg(self):
        html = (
            "<html><head><style>.x{display:none}</style>"
            "<script>var code='999999';</script></head>"
            "<body><h1>关于证券代码000765暂停上市的公告</h1>"
            "<p>最后交易日为2020年12月31日。</p>"
            "<svg><text>888888</text></svg></body></html>"
        ).encode()
        text = extract_lifecycle_document_text(document(html, "text/html"))
        self.assertIn("000765", text)
        self.assertIn("2020年12月31日", text)
        self.assertNotIn("999999", text)
        self.assertNotIn("888888", text)
        self.assertNotIn("display:none", text)

    def test_extracts_plain_text_and_normalizes_whitespace(self):
        text = extract_lifecycle_document_text(
            document("证券代码000765\r\n\r\n  暂停上市。".encode(), "text/plain")
        )
        self.assertEqual(text, "证券代码000765\n\n暂停上市。")

    def test_extracts_xml_text(self):
        text = extract_lifecycle_document_text(
            document("<notice><code>000765</code><event>暂停上市</event></notice>".encode(),
                     "application/xml")
        )
        self.assertIn("000765", text)
        self.assertIn("暂停上市", text)

    def test_extracts_json_string_values_without_python_repr(self):
        body = '{"title":"证券公告","content":["证券代码000765","暂停上市"]}'.encode()
        text = extract_lifecycle_document_text(document(body, "application/json"))
        self.assertIn("证券公告", text)
        self.assertIn("000765", text)
        self.assertNotIn("['证券代码", text)


    def test_extracted_html_can_feed_lifecycle_parser(self):
        from trendradar.market_data.lifecycle_evidence_parser import parse_lifecycle_announcement

        html = (
            "<html><body><h1>证券代码000765暂停上市公告</h1>"
            "<p>最后交易日为2020年12月31日。</p></body></html>"
        ).encode()
        extracted = extract_lifecycle_document_text(document(html, "text/html"))
        parsed = parse_lifecycle_announcement(extracted)
        self.assertEqual(parsed.security_codes, ("000765",))
        self.assertIn("suspension", parsed.event_types)
        self.assertEqual(parsed.date_evidence[0].date_type, "last_trading_date")
        self.assertEqual(parsed.date_evidence[0].date_status, "unknown")

    def test_rejects_pdf_until_a_real_pdf_text_extractor_is_added(self):
        with self.assertRaisesRegex(ValueError, "暂不支持"):
            extract_lifecycle_document_text(document(b"%PDF-1.7 binary", "application/pdf"))

    def test_rejects_invalid_json_and_empty_extraction(self):
        with self.assertRaisesRegex(ValueError, "JSON"):
            extract_lifecycle_document_text(document(b"{bad json", "application/json"))
        with self.assertRaisesRegex(ValueError, "未提取到"):
            extract_lifecycle_document_text(document(b"<html><script>hidden</script></html>",
                                                     "text/html"))

    def test_rejects_non_document_and_non_200(self):
        with self.assertRaises(TypeError):
            extract_lifecycle_document_text(object())
        failed = document(b"error", "text/plain")
        failed = FetchedDocument(
            failed.source_id, failed.requested_url, failed.final_url, 404,
            failed.content_type, failed.body, failed.content_sha256,
        )
        with self.assertRaisesRegex(ValueError, "HTTP 200"):
            extract_lifecycle_document_text(failed)


if __name__ == "__main__":
    unittest.main()
