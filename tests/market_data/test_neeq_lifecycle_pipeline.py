# 本文件测试NEEQ公告元数据到附件解析及审核候选入库的端到端边界。
"""Tests for the review-only NEEQ lifecycle ingestion pipeline."""

import hashlib
import tempfile
import unittest
from pathlib import Path

from trendradar.market_data.lifecycle_evidence_fetcher import FetchedDocument
from trendradar.market_data.neeq_lifecycle_pipeline import ingest_neeq_lifecycle_probe_result
from trendradar.market_data.storage import MarketStorage


class FakeFetcher:
    def __init__(self, body):
        self.body = body
        self.urls = []

    def fetch(self, source_id, url):
        self.urls.append((source_id, url))
        return FetchedDocument(
            source_id=source_id,
            requested_url=url,
            final_url=url,
            status_code=200,
            content_type="text/plain",
            body=self.body,
            content_sha256=hashlib.sha256(self.body).hexdigest(),
        )


class NEEQLifecyclePipelineTests(unittest.TestCase):
    def _probe(self):
        record = {
            "companyCd": "870099",
            "companyName": "华搜股份",
            "disclosureTitle": "华搜股份股票进入摘牌整理期风险提示公告",
            "publishDate": "2026-11-13",
            "destFilePath": "/disclosure/sample.pdf",
            "fileExt": "pdf",
        }
        return {
            "source_id": "neeq",
            "status_code": 200,
            "metadata_source_url": "https://www.neeq.com.cn/disclosureInfoController/companyAnnouncement.do",
            "response_sha256": "a" * 64,
            "lifecycle_candidates": [{
                "company_code": "870099",
                "company_name": "华搜股份",
                "title": record["disclosureTitle"],
                "publish_date": record["publishDate"],
                "file_path": record["destFilePath"],
                "file_extension": "pdf",
                "record": record,
            }],
        }

    def test_downloads_parses_and_persists_only_review_candidates(self):
        body = (
            "北京华创搜了科技股份有限公司股票进入摘牌整理期交易，"
            "预计于2026年11月20日终止挂牌。"
        ).encode("utf-8")
        fetcher = FakeFetcher(body)
        with tempfile.TemporaryDirectory() as tmp:
            storage = MarketStorage(str(Path(tmp) / "market.db"))
            result = ingest_neeq_lifecycle_probe_result(
                self._probe(), storage=storage, fetcher=fetcher,
                retrieved_at="2026-11-13T20:00:00+08:00",
            )
            self.assertEqual(result["processed_count"], 1)
            self.assertEqual(result["identity_candidates_saved"], 1)
            self.assertGreaterEqual(result["lifecycle_candidates_saved"], 1)
            self.assertEqual(result["formal_master_writes"], 0)
            self.assertEqual(result["formal_lifecycle_writes"], 0)
            self.assertEqual(
                result["items"][0]["notice_classification"]["category"],
                "delisting_arrangement_risk_notice",
            )
            self.assertFalse(
                result["items"][0]["notice_classification"]["confirmed_legal_fact"]
            )
            identity = storage.list_neeq_issuer_identity_candidates(company_code="870099")
            self.assertEqual(identity[0]["review_status"], "pending")
            self.assertEqual(identity[0]["raw_record"]["companyCd"], "870099")
            lifecycle = storage.list_lifecycle_evidence_candidates()
            self.assertTrue(any(row["exchange"] == "NEEQ" and row["security_code"] == "" for row in lifecycle))
            self.assertTrue(any(
                __import__("json").loads(row["payload_json"])["notice_classification"]["category"]
                == "delisting_arrangement_risk_notice"
                for row in lifecycle if row["exchange"] == "NEEQ"
            ))
            self.assertTrue(any("北京华创搜了科技股份有限公司" in row["evidence_excerpt"] for row in lifecycle))
            self.assertTrue(all(not row["evidence_excerpt"].startswith("%PDF-") for row in lifecycle))
            with storage.connect() as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM neeq_issuer_master").fetchone()[0], 0)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM security_lifecycle_events").fetchone()[0], 0)
            self.assertEqual(fetcher.urls[0][1], "https://www.neeq.com.cn/disclosure/sample.pdf")

    def test_rejects_untrusted_metadata_source_before_downloading(self):
        probe = self._probe()
        probe["metadata_source_url"] = "https://example.com/forged"
        fetcher = FakeFetcher(b"unused")
        with tempfile.TemporaryDirectory() as tmp:
            storage = MarketStorage(str(Path(tmp) / "market.db"))
            with self.assertRaises(ValueError):
                ingest_neeq_lifecycle_probe_result(probe, storage=storage, fetcher=fetcher)
        self.assertEqual(fetcher.urls, [])

    def test_attachment_failure_is_recorded_without_creating_identity_master(self):
        class BrokenFetcher:
            def fetch(self, source_id, url):
                raise RuntimeError("download blocked")

        with tempfile.TemporaryDirectory() as tmp:
            storage = MarketStorage(str(Path(tmp) / "market.db"))
            result = ingest_neeq_lifecycle_probe_result(
                self._probe(), storage=storage, fetcher=BrokenFetcher(),
            )
            self.assertEqual(result["processed_count"], 0)
            self.assertEqual(result["items"][0]["status"], "failed")
            self.assertEqual(storage.list_neeq_issuer_identity_candidates(), [])
            self.assertIsNone(storage.get_neeq_issuer("870099"))


if __name__ == "__main__":
    unittest.main()
