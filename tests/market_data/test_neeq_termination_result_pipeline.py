# 本文件测试NEEQ终止挂牌结果及批量公告证据的候选入库和正式表隔离。
"""Tests for review-only NEEQ termination-result evidence ingestion."""

import hashlib
import tempfile
import unittest
from pathlib import Path

from trendradar.market_data.lifecycle_evidence_fetcher import FetchedDocument
from trendradar.market_data.neeq_termination_result_pipeline import ingest_neeq_termination_result_pages
from trendradar.market_data.storage import MarketStorage


class FakeFetcher:
    def __init__(self, body):
        self.body = body
        self.calls = []

    def fetch(self, source_id, url):
        self.calls.append((source_id, url))
        return FetchedDocument(
            source_id=source_id, requested_url=url, final_url=url,
            status_code=200, content_type="text/plain", body=self.body,
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
            with storage.connect() as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM security_lifecycle_events").fetchone()[0], 0)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM neeq_issuer_master").fetchone()[0], 0)

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
