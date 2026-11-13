# 本文件测试NEEQ生命周期采集CLI的参数边界、管线衔接及正式主表隔离声明。
"""Tests for the bounded NEEQ lifecycle ingestion command-line entry point."""

import contextlib
import io
import json
import unittest
from unittest.mock import patch

from scripts.market_data import ingest_neeq_lifecycle as cli


class NEEQLifecycleIngestionCLITests(unittest.TestCase):
    def test_parser_requires_date_range_and_defaults_to_small_batch(self):
        args = cli.build_parser().parse_args([
            "--start-date", "2026-11-01", "--end-date", "2026-11-13",
        ])
        self.assertEqual(args.page, 0)
        self.assertEqual(args.max_announcements, 5)
        self.assertEqual(args.db, "output/market/market.db")

    def test_main_connects_probe_to_review_only_pipeline(self):
        probe_result = {
            "source_id": "neeq", "status_code": 200,
            "metadata_source_url": "https://www.neeq.com.cn/disclosureInfoController/companyAnnouncement.do",
            "response_sha256": "a" * 64, "page_number": 280, "page_size": 20,
            "returned_count": 2, "total_elements": 5602,
            "lifecycle_candidate_count": 2,
            "classification_filter_verified": False, "warnings": [],
        }
        ingestion_result = {
            "source_id": "neeq", "processed_count": 2,
            "identity_candidates_saved": 2, "lifecycle_candidates_saved": 4,
            "items": [{"status": "saved_for_review"}],
            "formal_master_writes": 0, "formal_lifecycle_writes": 0,
            "unprocessed_count": 0,
        }
        stdout = io.StringIO()
        with patch.object(cli, "probe_neeq_announcement_list", return_value=probe_result) as probe, \
             patch.object(cli, "MarketStorage") as storage_type, \
             patch.object(cli, "LifecycleEvidenceFetcher") as fetcher_type, \
             patch.object(cli, "ingest_neeq_lifecycle_probe_result", return_value=ingestion_result) as ingest:
            storage_type.return_value.db_path = "output/market/market.db"
            with patch("sys.argv", [
                "ingest_neeq_lifecycle.py",
                "--start-date", "2026-11-01", "--end-date", "2026-11-13",
                "--page", "280", "--max-announcements", "5",
                "--notice-type", "5", "--disclosure-type", "5",
            ]), contextlib.redirect_stdout(stdout):
                self.assertEqual(cli.main(), 0)

        probe.assert_called_once()
        self.assertEqual(probe.call_args.kwargs["start_date"], "2026-11-01")
        self.assertEqual(probe.call_args.kwargs["page"], 280)
        self.assertEqual(probe.call_args.kwargs["notice_type"], "5")
        self.assertEqual(probe.call_args.kwargs["disclosure_type"], "5")
        fetcher_type.assert_called_once()
        ingest.assert_called_once()
        self.assertEqual(ingest.call_args.kwargs["max_announcements"], 5)
        output = json.loads(stdout.getvalue())
        self.assertEqual(output["safety"]["formal_issuer_master_writes"], 0)
        self.assertEqual(output["safety"]["formal_lifecycle_writes"], 0)
        self.assertTrue(output["safety"]["writes_are_review_candidates_only"])

    def test_main_rejects_unsafe_batch_size_before_network_or_database(self):
        with patch.object(cli, "probe_neeq_announcement_list") as probe, \
             patch.object(cli, "MarketStorage") as storage_type, \
             patch("sys.argv", [
                 "ingest_neeq_lifecycle.py",
                 "--start-date", "2026-11-01", "--end-date", "2026-11-13",
                 "--max-announcements", "101",
             ]):
            with self.assertRaises(SystemExit):
                cli.main()
        probe.assert_not_called()
        storage_type.assert_not_called()


if __name__ == "__main__":
    unittest.main()
