# 本文件测试 NEEQ 分类树运行时探测的 JSONP 解析、请求边界和响应校验。
"""Tests for the bounded NEEQ runtime category-tree probe."""

import hashlib
import unittest

from trendradar.market_data.neeq_runtime_probe import (
    DEFAULT_CALLBACK,
    parse_neeq_jsonp,
    probe_neeq_category_tree,
)


class FakeResponse:
    def __init__(self, body, content_type="application/javascript", final_url=None):
        self.body = body
        self.status = 200
        self.headers = {"Content-Type": content_type, "Content-Length": str(len(body))}
        self.final_url = final_url or "https://www.neeq.com.cn/screening/advanced.do?callback=" + DEFAULT_CALLBACK

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def geturl(self):
        return self.final_url

    def read(self, size=-1):
        return self.body[:size]


class FakeOpener:
    def __init__(self, response):
        self.response = response
        self.request = None
        self.timeout = None

    def open(self, request, timeout):
        self.request = request
        self.timeout = timeout
        return self.response


class NEEQRuntimeProbeTests(unittest.TestCase):
    def test_parses_exact_jsonp_callback_and_plain_json(self):
        payload = [{"list": [{"title": "公告分类", "datebaseNumber": "100"}]}]
        self.assertEqual(
            parse_neeq_jsonp(
                (DEFAULT_CALLBACK + "(" + __import__("json").dumps(payload) + ");").encode()
            ),
            payload,
        )
        self.assertEqual(parse_neeq_jsonp(b'[{"list":[]}]'), [{"list": []}])

    def test_rejects_unexpected_callback_and_invalid_json(self):
        with self.assertRaises(ValueError):
            parse_neeq_jsonp(b"otherCallback([]);")
        with self.assertRaises(ValueError):
            parse_neeq_jsonp(b"TrendRadarCategoryProbe(not-json);")

    def test_sends_one_bounded_post_and_verifies_response_provenance(self):
        import json
        payload = [{"list": [{"title": "公告分类", "datebaseNumber": "100"}]}]
        body = (DEFAULT_CALLBACK + "(" + json.dumps(payload) + ");").encode()
        opener = FakeOpener(FakeResponse(body))
        result = probe_neeq_category_tree(opener=opener)
        self.assertEqual(opener.request.get_method(), "POST")
        self.assertEqual(opener.request.data, b"")
        self.assertEqual(opener.request.get_header("X-requested-with"), "XMLHttpRequest")
        self.assertEqual(opener.request.get_header("Origin"), "https://www.neeq.com.cn")
        self.assertIsNone(opener.request.get_header("Content-type"))
        self.assertEqual(opener.timeout, 15.0)
        self.assertEqual(result["http_method"], "POST")
        self.assertTrue(result["runtime_verified"])
        self.assertEqual(result["request_count"], 1)
        self.assertEqual(result["content_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(result["payload"], payload)

    def test_accepts_html_mime_only_when_body_is_valid_jsonp(self):
        import json
        payload = [{"list": [{"title": "风险事项类", "datebaseNumber": "9504-1600"}]}]
        body = (DEFAULT_CALLBACK + "(" + json.dumps(payload) + ");").encode()
        result = probe_neeq_category_tree(
            opener=FakeOpener(FakeResponse(body, content_type="text/html; charset=utf-8"))
        )
        self.assertEqual(result["content_type"], "text/html")
        self.assertIsNotNone(result["content_type_warning"])
        self.assertEqual(result["payload"], payload)

    def test_rejects_html_page_even_when_html_mime_is_allowed(self):
        body = b"<html><title>Access denied</title><body>blocked</body></html>"
        with self.assertRaisesRegex(ValueError, "响应不是预期 JSON"):
            probe_neeq_category_tree(
                opener=FakeOpener(FakeResponse(body, content_type="text/html; charset=utf-8"))
            )

    def test_rejects_unexpected_response_shape(self):
        import json
        body = (DEFAULT_CALLBACK + "(" + json.dumps({"list": []}) + ");").encode()
        with self.assertRaises(ValueError):
            probe_neeq_category_tree(opener=FakeOpener(FakeResponse(body)))

    def test_rejects_external_final_url(self):
        import json
        body = (DEFAULT_CALLBACK + "(" + json.dumps([{"list": []}]) + ");").encode()
        opener = FakeOpener(FakeResponse(body, final_url="https://outside.example/"))
        with self.assertRaises(ValueError):
            probe_neeq_category_tree(opener=opener)

    def test_rejects_oversized_response_and_invalid_limits(self):
        body = (DEFAULT_CALLBACK + "([]);").encode()
        with self.assertRaises(ValueError):
            probe_neeq_category_tree(opener=FakeOpener(FakeResponse(body)), max_bytes=1)
        with self.assertRaises(ValueError):
            probe_neeq_category_tree(opener=FakeOpener(FakeResponse(body)), max_bytes=0)


if __name__ == "__main__":
    unittest.main()
