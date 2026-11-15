# 本模块定向查询NEEQ官方终止挂牌结果接口并保存原始响应及哈希；只生成候选证据，不写正式生命周期表。
"""Audit targeted NEEQ termination-result records without promoting legal facts."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_SECURITY_CODES = ("834502", "834606", "834904")


def audit_targeted_neeq_termination_results(
    provider,
    *,
    security_codes=DEFAULT_SECURITY_CODES,
    raw_response_dir,
    max_pages_per_security: int = 20,
    retrieved_at: str | None = None,
) -> dict:
    """Query each security code separately and preserve every raw JSONP response."""
    codes = tuple(dict.fromkeys(str(code).strip() for code in security_codes))
    if not codes or any(len(code) != 6 or not code.isdigit() for code in codes):
        raise ValueError("security_codes 必须是非空的六位数字证券代码列表")
    if not 1 <= max_pages_per_security <= 100:
        raise ValueError("max_pages_per_security 必须在1到100之间")

    root = Path(raw_response_dir)
    root.mkdir(parents=True, exist_ok=True)
    collected_at = retrieved_at or datetime.now(timezone.utc).isoformat()
    cases = []

    for code in codes:
        pages = []
        first = provider.fetch_page(company_code=code, page=0, retrieved_at=collected_at)
        pages.append(first)
        page_limit = min(first.total_pages, max_pages_per_security)
        for page_number in range(1, page_limit):
            pages.append(provider.fetch_page(
                company_code=code, page=page_number, retrieved_at=collected_at
            ))

        page_audits = []
        matches = []
        rejected_count = 0
        for page in pages:
            body = getattr(page, "raw_response_body", b"")
            if not isinstance(body, bytes) or not body:
                raise ValueError(f"{code} 第{page.page_number}页缺少原始响应字节，不能建立可复核证据")
            actual_hash = hashlib.sha256(body).hexdigest()
            if actual_hash != page.response_sha256:
                raise ValueError(f"{code} 第{page.page_number}页原始响应 SHA256 不匹配")
            raw_name = f"{code}_page_{page.page_number}.jsonp"
            raw_path = root / raw_name
            raw_path.write_bytes(body)
            page_audits.append({
                "page_number": page.page_number,
                "page_size": page.page_size,
                "total_elements": page.total_elements,
                "total_pages": page.total_pages,
                "source_url": page.source_url,
                "retrieved_at": page.retrieved_at,
                "request_parameters": page.request_parameters,
                "request_body_sha256": page.request_body_sha256,
                "response_sha256": actual_hash,
                "raw_response_file": str(raw_path),
                "status_code": page.status_code,
                "rejected_record_count": page.rejected_record_count,
                "record_validation_errors": list(page.record_validation_errors),
                "rejected_record_details": list(page.rejected_record_details),
            })
            rejected_count += page.rejected_record_count
            for record in page.records:
                if str(record.get("companyCode") or "").strip() == code:
                    matches.append(record)

        unique = {}
        for record in matches:
            key = (
                str(record.get("companyCode") or ""),
                str(record.get("delistingDateISO") or ""),
                str(record.get("delistingNotice") or ""),
                str(record.get("companyName") or ""),
            )
            unique[key] = record
        records = list(unique.values())
        cases.append({
            "security_code": code,
            "query_status": "matched" if records else "no_matching_record_returned",
            "matched_record_count": len(records),
            "records": records,
            "pages": page_audits,
            "rejected_record_count": rejected_count,
            "date_evidence_candidates": [
                {
                    "date_value": record.get("delistingDateISO"),
                    "date_type": "official_result_delisting_date",
                    "classification_status": "candidate_pending_manual_review",
                    "source": "NEEQ官方终止挂牌结果列表接口",
                    "source_url": next((page["source_url"] for page in page_audits), ""),
                    "raw_response_sha256": next((page["response_sha256"] for page in page_audits), ""),
                    "legal_fact_confirmed": False,
                    "formal_lifecycle_write_allowed": False,
                }
                for record in records if record.get("delistingDateISO")
            ],
        })

    return {
        "schema_version": 1,
        "report_version": "neeq_targeted_termination_result_audit_v1",
        "generated_at": collected_at,
        "source": "https://www.neeq.com.cn/terminationController/infoResultse.do",
        "query_security_codes": list(codes),
        "cases": cases,
        "summary": {
            "security_count": len(cases),
            "matched_security_count": sum(case["matched_record_count"] > 0 for case in cases),
            "missing_security_count": sum(case["matched_record_count"] == 0 for case in cases),
            "matched_record_count": sum(case["matched_record_count"] for case in cases),
            "raw_response_page_count": sum(len(case["pages"]) for case in cases),
            "rejected_record_count": sum(case["rejected_record_count"] for case in cases),
        },
        "safety": {
            "official_api_queries_only": True,
            "attachment_downloads": 0,
            "database_writes": 0,
            "formal_lifecycle_writes": 0,
            "legal_fact_confirmations": 0,
            "automatic_date_promotions": 0,
            "manual_review_required": True,
            "read_only": True,
        },
    }
