# 本文件测试NEEQ官方终止挂牌结果接口的JSONP、分页、字段和安全校验。
"""Tests for the official NEEQ termination-result provider."""

import hashlib
import json
import unittest
from io import BytesIO

from trendradar.market_data.neeq_termination_result_provider import (
    CALLBACK, NEEQTerminationResultProvider, _parse_jsonp,
)


class FakeResponse:
    status = 200
    headers = {"Content-Type": "text/html;charset=utf-8"}

    def __init__(self, body, url):
        self.body = body
        self.url = url

    def read(self, size=-1):
        return self.body if size < 0 else self.body[:size]

    def geturl(self):
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeOpener:
    def __init__(self, body):
        self.body = body
        self.request = None

    def open(self, request, timeout=None):
        self.request = request
        return FakeResponse(self.body, request.full_url)


class ProviderTests(unittest.TestCase):
    def test_parses_realistic_jsonp_and_epoch_date(self):
        record = {
            "companyCode": "870099", "companyName": "摘牌华搜",
            "hostBroker": "万和证券", "delistingDate": {"time": 1793721600000},
            "delistingNotice": "https://www.neeq.com.cn/uploads/example.pdf",
        }
        payload = [{"page": {
            "content": [record], "number": 0, "size": 20,
            "totalElements": 1, "totalPages": 1,
        }}]
        body = f"{CALLBACK}({json.dumps(payload, ensure_ascii=False)});".encode()
        opener = FakeOpener(body)
        page = NEEQTerminationResultProvider(opener=opener).fetch_page()
        self.assertEqual(page.total_elements, 1)
        self.assertEqual(page.records[0]["companyCode"], "870099")
        self.assertEqual(page.records[0]["delistingDateISO"], "2026-11-04")
        self.assertEqual(page.response_sha256, hashlib.sha256(body).hexdigest())
        self.assertEqual(opener.request.get_method(), "POST")

    def test_rejects_bad_jsonp_and_callback_injection(self):
        with self.assertRaises(ValueError):
            _parse_jsonp("alert(1)", CALLBACK)
        with self.assertRaises(ValueError):
            _parse_jsonp("x({})", "x);evil(")

    def test_rejects_untrusted_attachment_url(self):
        payload = [{"page": {
            "content": [{
                "companyCode": "870099", "companyName": "摘牌华搜",
                "delistingNotice": "https://evil.example/a.pdf",
            }],
            "number": 0, "size": 20, "totalElements": 1, "totalPages": 1,
        }}]
        body = f"{CALLBACK}({json.dumps(payload)});".encode()
        with self.assertRaises(ValueError):
            NEEQTerminationResultProvider(opener=FakeOpener(body)).fetch_page()

    def test_rejects_invalid_company_code(self):
        with self.assertRaises(ValueError):
            NEEQTerminationResultProvider().fetch_page(company_code="87099")


if __name__ == "__main__":
    unittest.main()
