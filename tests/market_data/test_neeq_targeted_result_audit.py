# 本文件测试NEEQ定向终止挂牌结果审计的原始响应留存、代码匹配和只读约束。
"""Tests for targeted NEEQ termination-result audit."""
import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from trendradar.market_data.neeq_targeted_result_audit import audit_targeted_neeq_termination_results


class FakeProvider:
    def __init__(self, records_by_code):
        self.records_by_code = records_by_code
        self.calls = []

    def fetch_page(self, *, company_code, page, retrieved_at=None):
        self.calls.append((company_code, page))
        records = self.records_by_code.get(company_code, []) if page == 0 else []
        body = ("callback(" + company_code + ":" + str(page) + ")").encode()
        return SimpleNamespace(
            source_url="https://www.neeq.com.cn/terminationController/infoResultse.do?callback=test",
            response_sha256=hashlib.sha256(body).hexdigest(),
            retrieved_at=retrieved_at or "2026-11-15T00:00:00+00:00",
            page_number=page, page_size=20, total_elements=len(records),
            total_pages=1, records=tuple(records),
            request_parameters={"companyCode": company_code, "page": page},
            request_body_sha256="b" * 64, status_code=200,
            rejected_record_count=0, record_validation_errors=(),
            rejected_record_details=(), raw_response_body=body,
        )


class TargetedAuditTests(unittest.TestCase):
    def test_saves_raw_response_and_records_candidate_date_without_confirmation(self):
        provider = FakeProvider({
            "834502": [{
                "companyCode": "834502", "companyName": "富海银涛",
                "delistingDateISO": "2017-12-21",
                "delistingNotice": "https://www.neeq.com.cn/disclosure/example.pdf",
            }]
        })
        with tempfile.TemporaryDirectory() as tmp:
            result = audit_targeted_neeq_termination_results(
                provider, security_codes=["834502"], raw_response_dir=tmp,
                retrieved_at="2026-11-15T00:00:00+00:00",
            )
            case = result["cases"][0]
            raw_file = Path(case["pages"][0]["raw_response_file"])
            self.assertTrue(raw_file.is_file())
            self.assertEqual(hashlib.sha256(raw_file.read_bytes()).hexdigest(),
                             case["pages"][0]["response_sha256"])
            self.assertEqual(case["date_evidence_candidates"][0]["date_type"],
                             "official_result_delisting_date")
            self.assertFalse(case["date_evidence_candidates"][0]["legal_fact_confirmed"])
            self.assertFalse(case["date_evidence_candidates"][0]["formal_lifecycle_write_allowed"])

    def test_reports_missing_codes_without_inference(self):
        provider = FakeProvider({})
        with tempfile.TemporaryDirectory() as tmp:
            result = audit_targeted_neeq_termination_results(
                provider, security_codes=["834606"], raw_response_dir=tmp,
            )
        self.assertEqual(result["cases"][0]["query_status"], "no_matching_record_returned")
        self.assertEqual(result["summary"]["missing_security_count"], 1)
        self.assertEqual(result["cases"][0]["date_evidence_candidates"], [])

    def test_ignores_other_security_records_returned_by_broad_api(self):
        provider = FakeProvider({
            "834904": [
                {"companyCode": "834904", "companyName": "银纪资产", "delistingDateISO": "2017-12-21"},
                {"companyCode": "999999", "companyName": "其他公司", "delistingDateISO": "2017-12-21"},
            ]
        })
        with tempfile.TemporaryDirectory() as tmp:
            result = audit_targeted_neeq_termination_results(
                provider, security_codes=["834904"], raw_response_dir=tmp,
            )
        self.assertEqual(result["cases"][0]["matched_record_count"], 1)
        self.assertEqual(result["cases"][0]["records"][0]["companyCode"], "834904")

    def test_rejects_invalid_security_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                audit_targeted_neeq_termination_results(
                    FakeProvider({}), security_codes=["83460"], raw_response_dir=tmp,
                )

    def test_rejects_missing_raw_response_bytes(self):
        provider = FakeProvider({"834502": [{
            "companyCode": "834502", "companyName": "富海银涛",
            "delistingDateISO": "2017-12-21",
        }]})
        original = provider.fetch_page
        def broken(**kwargs):
            page = original(**kwargs)
            page.raw_response_body = b""
            return page
        provider.fetch_page = broken
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                audit_targeted_neeq_termination_results(
                    provider, security_codes=["834502"], raw_response_dir=tmp,
                )


if __name__ == "__main__":
    unittest.main()
