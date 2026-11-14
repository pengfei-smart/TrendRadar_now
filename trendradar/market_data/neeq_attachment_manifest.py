# 本文件按官方附件URL分组NEEQ候选证据任务，不下载附件、不写数据库并保留全部关联候选。
"""Build a read-only, URL-grouped attachment worklist for NEEQ evidence review."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import datetime, timezone
from typing import Iterable

from trendradar.market_data.lifecycle_source_registry import is_allowed_url


def build_neeq_attachment_manifest(
    candidates: Iterable[dict], *, generated_at: str | None = None,
) -> dict:
    """Group candidates by exact official URL; never merge or rewrite URL identities."""
    grouped: dict[str, list[dict]] = defaultdict(list)
    missing_url = []
    rejected_url = []

    for candidate in candidates:
        url = str(candidate.get("attachment_url") or "").strip()
        if not url:
            missing_url.append({
                "record_id": candidate.get("record_id"),
                "company_code": candidate.get("company_code"),
                "company_name": candidate.get("company_name"),
                "reason": "missing_attachment_url",
            })
            continue
        if not is_allowed_url("neeq", url):
            rejected_url.append({
                "record_id": candidate.get("record_id"),
                "company_code": candidate.get("company_code"),
                "company_name": candidate.get("company_name"),
                "url": url,
                "reason": "url_not_in_neeq_https_allowlist",
            })
            continue
        grouped[url].append(candidate)

    tasks = []
    for url in sorted(grouped):
        linked = grouped[url]
        url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()
        kinds = sorted({
            str(item.get("raw_record", {}).get("attachmentKind") or "unknown").lower()
            for item in linked
        })
        tasks.append({
            "attachment_id": "neeq-attachment-" + url_hash[:32],
            "url": url,
            "url_sha256": url_hash,
            "attachment_kinds_reported": kinds,
            "linked_candidate_count": len(linked),
            "linked_candidates": [{
                "record_id": item.get("record_id"),
                "company_code": item.get("company_code"),
                "company_name": item.get("company_name"),
                "delisting_date": item.get("delisting_date"),
                "candidate_status": item.get("candidate_status"),
                "raw_record_sha256": item.get("raw_record_sha256"),
            } for item in linked],
            "download_status": "pending",
            "text_extraction_status": "pending",
            "legal_fact_confirmed": False,
            "formal_lifecycle_write_allowed": False,
        })

    return {
        "schema_version": 1,
        "source": "neeq_official_termination_result_list",
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "unique_allowed_attachment_count": len(tasks),
        "candidate_attachment_link_count": sum(len(items) for items in grouped.values()),
        "missing_attachment_candidate_count": len(missing_url),
        "rejected_attachment_candidate_count": len(rejected_url),
        "tasks": tasks,
        "missing_url_candidates": missing_url,
        "rejected_url_candidates": rejected_url,
        "safety": {
            "network_downloads": 0,
            "database_writes": 0,
            "formal_lifecycle_writes": 0,
            "read_only": True,
            "manual_evidence_review_required": True,
        },
    }
