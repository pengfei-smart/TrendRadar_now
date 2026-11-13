# 本文件串联NEEQ官方公告元数据、附件下载、正文解析及待审核身份/生命周期候选入库，不创建正式发行人或生命周期事实。
"""Orchestrate review-only NEEQ lifecycle announcement ingestion."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit

from trendradar.market_data.lifecycle_candidate_builder import (
    build_lifecycle_evidence_candidates,
)
from trendradar.market_data.lifecycle_document_text import (
    extract_lifecycle_document_text,
)
from trendradar.market_data.lifecycle_evidence_parser import (
    parse_lifecycle_announcement,
)
from trendradar.market_data.lifecycle_source_registry import is_allowed_url


def ingest_neeq_lifecycle_probe_result(
    probe_result: dict,
    *,
    storage,
    fetcher,
    retrieved_at: str | None = None,
    max_announcements: int = 20,
) -> dict:
    """Download and parse a bounded set of metadata-linked NEEQ attachments.

    Every successful item is stored only in review candidate tables. The function
    never writes neeq_issuer_master, neeq_issuer_aliases, or formal lifecycle tables.
    Individual attachment failures are returned and do not discard other items.
    """
    if not isinstance(probe_result, dict):
        raise TypeError("probe_result 必须是 dict")
    if probe_result.get("source_id") != "neeq" or probe_result.get("status_code") != 200:
        raise ValueError("只接受成功的 NEEQ 官方公告列表探测结果")
    if not probe_result.get("metadata_source_url") or not is_allowed_url(
        "neeq", probe_result["metadata_source_url"]
    ):
        raise ValueError("公告列表元数据来源 URL 不在 NEEQ 官方白名单内")
    response_hash = str(probe_result.get("response_sha256") or "").lower()
    if len(response_hash) != 64 or any(ch not in "0123456789abcdef" for ch in response_hash):
        raise ValueError("公告列表响应缺少有效 SHA-256")
    candidates = probe_result.get("lifecycle_candidates")
    if not isinstance(candidates, list):
        raise ValueError("lifecycle_candidates 必须是数组")
    if isinstance(max_announcements, bool) or not isinstance(max_announcements, int) or not 1 <= max_announcements <= 100:
        raise ValueError("max_announcements 必须在 1 到 100 之间")

    collected_at = retrieved_at or datetime.now(timezone.utc).isoformat()
    result = {
        "source_id": "neeq",
        "processed_count": 0,
        "identity_candidates_saved": 0,
        "lifecycle_candidates_saved": 0,
        "items": [],
        "formal_master_writes": 0,
        "formal_lifecycle_writes": 0,
    }
    for item in candidates[:max_announcements]:
        try:
            if not isinstance(item, dict) or not isinstance(item.get("record"), dict):
                raise ValueError("公告候选缺少原始 record")
            record = dict(item["record"])
            code = str(record.get("companyCd") or "").strip()
            name = str(record.get("companyName") or "").strip()
            path = str(record.get("destFilePath") or "").strip()
            title = str(record.get("disclosureTitle") or item.get("title") or "").strip()
            publish_date = str(record.get("publishDate") or item.get("publish_date") or "").strip()
            if len(code) != 6 or not code.isdigit() or not name or not path or not title:
                raise ValueError("公告元数据缺少有效companyCd、companyName、destFilePath或标题")
            attachment_url = urljoin("https://www.neeq.com.cn/", path)
            if not is_allowed_url("neeq", attachment_url):
                raise ValueError("公告附件 URL 不在 NEEQ 官方白名单内")
            if str(record.get("fileExt") or item.get("file_extension") or "").lower() not in {"pdf", ".pdf"}:
                raise ValueError("当前流水线仅处理元数据明确标记为 PDF 的公告附件")

            document = fetcher.fetch("neeq", attachment_url)
            if urlsplit(document.final_url).path != urlsplit(attachment_url).path:
                raise ValueError("附件重定向后的路径与公告元数据路径不一致")
            text = extract_lifecycle_document_text(document)
            parsed = parse_lifecycle_announcement(text)
            lifecycle_rows = build_lifecycle_evidence_candidates(
                document,
                parsed,
                source_title=title,
                source_published_at=publish_date,
                retrieved_at=collected_at,
                announcement_metadata={
                    "record": record,
                    "source_url": probe_result["metadata_source_url"],
                    "response_sha256": response_hash,
                    "retrieved_at": collected_at,
                },
            )
            # Identity and lifecycle evidence remain separate review queues.
            identity_id = "neeq-id-" + hashlib.sha256(
                (code + "|" + attachment_url + "|" + response_hash).encode("utf-8")
            ).hexdigest()[:32]
            identity_rows = [{
                "identity_candidate_id": identity_id,
                "company_code": code,
                "company_name": name,
                "disclosure_title": title,
                "publish_date": publish_date,
                "attachment_url": attachment_url,
                "metadata_source_url": probe_result["metadata_source_url"],
                "metadata_response_sha256": response_hash,
                "retrieved_at": collected_at,
                "raw_record": record,
            }]
            identity_saved = storage.save_neeq_issuer_identity_candidates(identity_rows)
            lifecycle_saved = storage.save_lifecycle_evidence_candidates(lifecycle_rows) if lifecycle_rows else 0
            result["processed_count"] += 1
            result["identity_candidates_saved"] += identity_saved
            result["lifecycle_candidates_saved"] += lifecycle_saved
            result["items"].append({
                "company_code": code,
                "company_name": name,
                "attachment_url": attachment_url,
                "document_sha256": document.content_sha256,
                "parsed_event_types": list(parsed.event_types),
                "parsed_security_codes": list(parsed.security_codes),
                "identity_candidate_id": identity_id,
                "lifecycle_candidate_ids": [row["candidate_id"] for row in lifecycle_rows],
                "status": "saved_for_review",
            })
        except Exception as exc:
            result["items"].append({
                "company_code": str(item.get("company_code") or "") if isinstance(item, dict) else "",
                "status": "failed",
                "error": str(exc),
            })
    result["unprocessed_count"] = max(0, len(candidates) - min(len(candidates), max_announcements))
    return result
