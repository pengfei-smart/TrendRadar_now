# 本文件测试 NEEQ 公告列表只读探测的请求边界、JSONP 响应和候选标记行为。
"""Tests for the bounded read-only NEEQ announcement list probe."""

import json
import unittest
from urllib.parse import parse_qs

from trendradar.market_data.neeq_announcement_probe import probe_neeq_announcement_list


CALLBACK = "TrendRadarAnnouncementProbe"


class FakeResponse:
    def __init__(self, payload, content_type="application/javascript", final_url=None):
        self.body = (CALLBACK + "(" + json.dumps(payload) + ");").encode("utf-8")
        self.status = 200
        self.headers = {"Content-Type": content_type, "Content-Length": str(len(self.body))}
        self.final_url = final_url or (
            "https://www.neeq.com.cn/disclosureInfoController/companyAnnouncement.do"
            "?callback=" + CALLBACK
        )

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


class NEEQAnnouncementProbeTests(unittest.TestCase):
    def _payload(self):
        return [{
            "listInfo": {
                "content": [
                    {
                        "companyCd": "123456",
                        "companyName": "示例股份",
                        "disclosureTitle": "关于公司股票可能被终止挂牌的风险提示公告",
                        "publishDate": "2026-11-13",
                        "destFilePath": "/disclosure/sample.pdf",
                        "fileExt": "pdf",
                    },
                    {
                        "companyCd": "654321",
                        "companyName": "普通公司",
                        "disclosureTitle": "关于召开股东大会的通知",
                        "publishDate": "2026-11-13",
                        "destFilePath": "/disclosure/ordinary.pdf",
                        "fileExt": "pdf",
                    },
                ],
                "number": 0,
                "size": 20,
                "totalElements": 4052450,
                "totalPages": 202623,
            },
            "status": 0,
        }]

    def test_sends_one_post_with_repeated_form_keys_and_returns_metadata_only(self):
        opener = FakeOpener(FakeResponse(self._payload()))
        result = probe_neeq_announcement_list(
            start_date="2026-11-01",
            end_date="2026-11-13",
            opener=opener,
        )
        request = opener.request
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.full_url.split("?", 1)[0],
                         "https://www.neeq.com.cn/disclosureInfoController/companyAnnouncement.do")
        self.assertEqual(request.get_header("X-requested-with"), "XMLHttpRequest")
        self.assertEqual(request.get_header("Origin"), "https://www.neeq.com.cn")
        self.assertEqual(opener.timeout, 15.0)
        form = parse_qs(request.data.decode("utf-8"))
        self.assertEqual(form["xxfcbj[]"], ["3"])
        self.assertEqual(form["needFields[]"][0], "companyCd")
        self.assertEqual(form["page"], ["0"])
        self.assertEqual(result["request_count"], 1)
        self.assertEqual(result["page_number"], 0)
        self.assertEqual(result["page_size"], 20)
        self.assertEqual(result["total_elements"], 4052450)
        self.assertEqual(result["returned_count"], 2)
        self.assertEqual(result["lifecycle_candidate_count"], 1)
        self.assertFalse(result["classification_filter_verified"])
        self.assertFalse(result["lifecycle_only_query"])
        self.assertEqual(result["lifecycle_candidates"][0]["company_code"], "123456")
        self.assertNotIn("body", result)
        self.assertNotIn("pdf_content", result)

    def test_does_not_claim_lifecycle_category_filter_is_verified(self):
        opener = FakeOpener(FakeResponse(self._payload(), "text/html; charset=utf-8"))
        result = probe_neeq_announcement_list(
            start_date="2026-11-01", end_date="2026-11-13", opener=opener,
        )
        self.assertIsNotNone(result["content_type_warning"])
        self.assertFalse(result["classification_filter_verified"])

    def test_rejects_external_final_url(self):
        opener = FakeOpener(FakeResponse(
            self._payload(), final_url="https://example.com/redirected",
        ))
        with self.assertRaises(ValueError):
            probe_neeq_announcement_list(
                start_date="2026-11-01", end_date="2026-11-13", opener=opener,
            )

    def test_rejects_oversized_response_and_invalid_limits(self):
        opener = FakeOpener(FakeResponse(self._payload()))
        with self.assertRaises(ValueError):
            probe_neeq_announcement_list(
                start_date="2026-11-01", end_date="2026-11-13",
                opener=opener, max_bytes=1,
            )
        with self.assertRaises(ValueError):
            probe_neeq_announcement_list(
                start_date="2026-11-01", end_date="2026-11-13",
                opener=opener, max_bytes=0,
            )


if __name__ == "__main__":
    unittest.main()
