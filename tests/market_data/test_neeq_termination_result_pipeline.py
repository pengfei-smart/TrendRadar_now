# 本文件测试NEEQ终止挂牌结果及批量公告证据的候选入库和正式表隔离。
"""Tests for review-only NEEQ termination-result evidence ingestion."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from trendradar.market_data.lifecycle_evidence_fetcher import FetchedDocument
from trendradar.market_data.neeq_termination_result_pipeline import ingest_neeq_termination_result_pages
from trendradar.market_data.storage import MarketStorage


class FakeFetcher:
    def __init__(self, body, content_type="text/plain"):
        self.body = body
        self.content_type = content_type
        self.calls = []

    def fetch(self, source_id, url):
        self.calls.append((source_id, url))
        return FetchedDocument(
            source_id=source_id, requested_url=url, final_url=url,
            status_code=200, content_type=self.content_type, body=self.body,
            content_sha256=hashlib.sha256(self.body).hexdigest(),
        )


class FakePage:
    status_code = 200
    source_url = "https://www.neeq.com.cn/terminationController/infoResultse.do?callback=probe"
    response_sha256 = "a" * 64
    records = (
        {
            "companyCode": "870099", "companyName": "摘牌华搜",
            "hostBroker": "万和证券", "delistingDateISO": "2026-11-04",
            "delistingNotice": "https://www.neeq.com.cn/uploads/batch.pdf",
        },
        {
            "companyCode": "832480", "companyName": "摘牌商网",
            "hostBroker": "西南证券", "delistingDateISO": "2026-11-04",
            "delistingNotice": "https://www.neeq.com.cn/uploads/batch.pdf",
        },
    )


NOTICE = """
全国股转公司文件 股转公告〔2026〕305号
我司于 2026 年 9 月 29 日作出终止上述公司股票挂牌的决定。
11 870099 华搜股份 万和证券
14 832480 ST 商会网 西南证券
"""


class PipelineTests(unittest.TestCase):
    def test_persists_two_company_candidates_and_deduplicates_shared_pdf(self):
        body = NOTICE.encode("utf-8")
        fetcher = FakeFetcher(body)
        with tempfile.TemporaryDirectory() as tmp:
            storage = MarketStorage(str(Path(tmp) / "market.db"))
            result = ingest_neeq_termination_result_pages(
                [FakePage()], storage=storage, fetcher=fetcher,
                retrieved_at="2026-11-14T10:00:00+08:00",
            )
            self.assertEqual(result["processed_count"], 2)
            self.assertEqual(len(fetcher.calls), 1)
            self.assertEqual(result["formal_lifecycle_writes"], 0)
            rows = storage.list_lifecycle_evidence_candidates()
            selected = [row for row in rows if row["event_type"] == "delisting_completion"]
            self.assertEqual({row["security_code"] for row in selected}, {"870099", "832480"})
            self.assertTrue(all(row["review_status"] == "pending" for row in selected))
            for row in selected:
                payload = json.loads(row["payload_json"])
                self.assertEqual(payload["issuer_identity_hint"]["issuer_code"], row["security_code"])
                self.assertEqual(payload["result_date_evidence"][0]["value"], "2026-11-04")
            with storage.connect() as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM security_lifecycle_events").fetchone()[0], 0)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM neeq_issuer_master").fetchone()[0], 0)

    def test_extracts_official_html_termination_roster_attachment(self):
        html = """
        <html><head><title>终止挂牌决定公告</title></head><body>
        <h1>关于作出终止陕西成明节能技术股份有限公司等未披露定期报告公司股票挂牌决定的公告</h1>
        <p>全国股转公司于2026年9月29日作出终止挂牌决定。</p>
        <p>11 870099 华搜股份 万和证券</p>
        </body></html>
        """.encode("utf-8")
        record_page = FakePage()
        record_page.records = ({
            "companyCode": "870099", "companyName": "摘牌华搜",
            "hostBroker": "万和证券", "delistingDateISO": "2026-11-04",
            "delistingNotice": "https://www.neeq.com.cn/important_news/200006570.html",
            "attachmentKind": "html",
        },)
        fetcher = FakeFetcher(html, content_type="text/html")
        with tempfile.TemporaryDirectory() as tmp:
            storage = MarketStorage(str(Path(tmp) / "market.db"))
            result = ingest_neeq_termination_result_pages(
                [record_page], storage=storage, fetcher=fetcher,
                retrieved_at="2026-11-14T10:00:00+08:00",
            )
            self.assertEqual(result["processed_count"], 1)
            self.assertEqual(result["failed_count"], 0)
            row = next(
                item for item in storage.list_lifecycle_evidence_candidates()
                if item["event_type"] == "delisting_completion"
            )
            payload = json.loads(row["payload_json"])
            self.assertEqual(payload["document_content_type"], "text/html")
            self.assertEqual(payload["attachment_kind"], "html")
            self.assertTrue(payload["document_match"]["roster_code_match"])

    def test_invalid_source_page_rejected(self):
        class BadPage(FakePage):
            source_url = "https://evil.example/data"
        with tempfile.TemporaryDirectory() as tmp:
            storage = MarketStorage(str(Path(tmp) / "market.db"))
            with self.assertRaises(ValueError):
                ingest_neeq_termination_result_pages(
                    [BadPage()], storage=storage, fetcher=FakeFetcher(NOTICE.encode())
                )


if __name__ == "__main__":
    unittest.main()
