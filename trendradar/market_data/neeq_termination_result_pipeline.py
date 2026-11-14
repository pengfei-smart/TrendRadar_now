# 本文件将NEEQ官方终止挂牌结果记录与批量决定公告PDF匹配后保存为待审核证据，不写正式生命周期事实。
"""Persist official NEEQ termination-result evidence as review-only candidates."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from urllib.parse import urlsplit

from trendradar.market_data.lifecycle_document_text import extract_lifecycle_document_text
from trendradar.market_data.lifecycle_source_registry import is_allowed_url
from trendradar.market_data.neeq_termination_batch_parser import (
    match_neeq_result_record, parse_neeq_termination_roster,
)


def ingest_neeq_termination_result_pages(
    pages, *, storage, fetcher, retrieved_at: str | None = None,
    max_records: int = 100,
) -> dict:
    if not isinstance(pages, (list, tuple)) or not pages:
        raise ValueError("pages 必须是非空分页结果列表")
    if not 1 <= max_records <= 1000:
        raise ValueError("max_records 必须在1到1000之间")
    collected_at = retrieved_at or datetime.now(timezone.utc).isoformat()
    result = {
        "source": "neeq_termination_result", "processed_count": 0,
        "candidate_count": 0, "failed_count": 0, "items": [],
        "formal_issuer_master_writes": 0, "formal_lifecycle_writes": 0,
    }
    document_cache = {}
    work = []
    seen = set()
    for page in pages:
        if getattr(page, "status_code", None) != 200:
            raise ValueError("只接受 HTTP 200 的 NEEQ 终止挂牌结果页")
        response_hash = str(getattr(page, "response_sha256", "")).lower()
        if len(response_hash) != 64 or any(c not in "0123456789abcdef" for c in response_hash):
            raise ValueError("结果页缺少有效响应 SHA256")
        if not is_allowed_url("neeq", getattr(page, "source_url", "")):
            raise ValueError("结果页来源 URL 不在官方白名单")
        for record in page.records:
            code = str(record.get("companyCode") or "").strip()
            notice_url = str(record.get("delistingNotice") or "").strip()
            key = (code, notice_url)
            if key in seen:
                continue
            seen.add(key)
            work.append((record, page.source_url, response_hash))
    for record, result_source_url, result_response_hash in work[:max_records]:
        code = str(record.get("companyCode") or "").strip()
        name = str(record.get("companyName") or "").strip()
        notice_url = str(record.get("delistingNotice") or "").strip()
        try:
            if len(code) != 6 or not code.isdigit() or not name:
                raise ValueError("结果记录证券代码或名称无效")
            if not notice_url or not is_allowed_url("neeq", notice_url):
                raise ValueError("结果记录缺少官方公告附件 URL")
            if notice_url not in document_cache:
                document = fetcher.fetch("neeq", notice_url)
                if document.status_code != 200 or not is_allowed_url("neeq", document.final_url):
                    raise ValueError("公告附件未从官方 HTTPS 来源成功获取")
                if urlsplit(document.final_url).path != urlsplit(notice_url).path:
                    raise ValueError("公告附件重定向路径与原始路径不一致")
                text = extract_lifecycle_document_text(document)
                document_cache[notice_url] = (
                    document, text, parse_neeq_termination_roster(text)
                )
            document, text, roster = document_cache[notice_url]
            match = match_neeq_result_record(record, roster)
            date_value = str(record.get("delistingDateISO") or "")
            record_fingerprint = json.dumps(
                record, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            evidence_hash = hashlib.sha256(
                (document.content_sha256 + "|" + result_response_hash + "|" +
                 record_fingerprint).encode("utf-8")
            ).hexdigest()
            # Candidate identity remains stable across evidence refreshes; changed evidence
            # hash makes storage reset prior human review to pending.
            candidate_id = "neeq-result-" + hashlib.sha256(
                (code + "|" + notice_url).encode("utf-8")
            ).hexdigest()[:32]
            payload = {
                "evidence_kind": "official_termination_result_list",
                "result_record": record,
                "result_source_url": result_source_url,
                "result_response_sha256": result_response_hash,
                "result_retrieved_at": collected_at,
                "document_requested_url": document.requested_url,
                "document_final_url": document.final_url,
                "document_sha256": document.content_sha256,
                "document_match": match,
                "roster_document_number": roster["document_number"],
                "roster_decision_date": roster["decision_date"],
                "roster_security_codes": roster["security_codes"],
                "legal_fact_confirmed": False,
                "formal_lifecycle_write_allowed": False,
                "review_required": True,
            }
            candidate = {
                "candidate_id": candidate_id, "candidate_kind": "event",
                "security_code": code, "exchange": "NEEQ",
                "event_type": "delisting_completion",
                "event_date": date_value, "date_status": "pending",
                "source_name": "全国中小企业股份转让系统",
                "source_title": "强制终止挂牌结果记录及批量终止挂牌决定公告",
                "source_url": notice_url,
                "source_published_at": roster["decision_date"],
                "retrieved_at": collected_at,
                "content_sha256": evidence_hash,
                "evidence_excerpt": match.get("notice_name_context", "")[:1200] or text[:1200],
                "payload_json": payload, "evidence_grade": "official",
            }
            storage.save_lifecycle_evidence_candidates([candidate])
            result["processed_count"] += 1
            result["candidate_count"] += 1
            result["items"].append({
                "company_code": code, "company_name": name,
                "delisting_date": date_value, "document_sha256": document.content_sha256,
                "document_match": match, "candidate_id": candidate_id,
                "status": "saved_for_human_review",
            })
        except Exception as exc:
            result["failed_count"] += 1
            result["items"].append({
                "company_code": code, "company_name": name,
                "status": "failed", "error": str(exc),
            })
    result["unprocessed_count"] = max(0, len(work) - min(len(work), max_records))
    result["unique_document_count"] = len(document_cache)
    return result
