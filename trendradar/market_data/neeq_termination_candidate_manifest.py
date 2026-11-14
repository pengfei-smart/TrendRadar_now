# 本文件将NEEQ官方终止挂牌名录转换为只读候选清单，保留原始记录且不写入正式生命周期数据。
"""Build a conservative, review-only candidate manifest from NEEQ result pages."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import Iterable


def _canonical_record(row: dict) -> tuple[str, str]:
    raw = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return raw, hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_neeq_termination_candidate_manifest(
    pages: Iterable[object], audit: dict, *, generated_at: str | None = None,
) -> dict:
    """Fold only byte-equivalent logical rows sharing the same non-null record ID.

    Conflicting duplicate IDs and rows without IDs remain separate review items.
    Different record IDs are never merged merely because companyCode matches.
    No database or formal lifecycle records are written.
    """
    occurrences = []
    by_id: dict[str, list[dict]] = defaultdict(list)
    code_record_ids: dict[str, set[str]] = defaultdict(set)
    code_occurrence_counts: dict[str, int] = defaultdict(int)

    for page in sorted(list(pages), key=lambda item: item.page_number):
        for index, row in enumerate(page.records):
            if not isinstance(row, dict):
                continue
            raw, fingerprint = _canonical_record(row)
            record_id = str(row.get("id")).strip() if row.get("id") is not None else ""
            code = str(row.get("companyCode") or "").strip()
            occurrence = {
                "page": int(page.page_number),
                "record_index": index,
                "record_id": record_id or None,
                "company_code": code,
                "company_name": str(row.get("companyName") or ""),
                "delisting_date": row.get("delistingDateISO") or row.get("delistingDate"),
                "attachment_url": str(row.get("delistingNotice") or ""),
                "raw_record_sha256": fingerprint,
                "raw_record": row,
            }
            occurrences.append(occurrence)
            if code:
                code_occurrence_counts[code] += 1
                if record_id:
                    code_record_ids[code].add(record_id)
            if record_id:
                by_id[record_id].append(occurrence)

    candidates = []
    seen_exact_ids = set()
    for occurrence in occurrences:
        record_id = occurrence["record_id"]
        if record_id:
            group = by_id[record_id]
            fingerprints = {item["raw_record_sha256"] for item in group}
            if len(fingerprints) == 1:
                if record_id in seen_exact_ids:
                    continue
                seen_exact_ids.add(record_id)
                classification = "exact_duplicate_collapsed" if len(group) > 1 else "unique_record_id"
                retained = dict(occurrence)
                retained["occurrence_count"] = len(group)
                retained["occurrences"] = [
                    {"page": item["page"], "record_index": item["record_index"]}
                    for item in group
                ]
            else:
                classification = "conflicting_record_id_occurrence"
                retained = dict(occurrence)
                retained["occurrence_count"] = len(group)
                retained["occurrences"] = [
                    {"page": item["page"], "record_index": item["record_index"],
                     "raw_record_sha256": item["raw_record_sha256"]}
                    for item in group
                ]
        else:
            classification = "missing_record_id_review"
            retained = dict(occurrence)
            retained["occurrence_count"] = 1
            retained["occurrences"] = [
                {"page": occurrence["page"], "record_index": occurrence["record_index"]}
            ]
        retained["candidate_status"] = "pending_manual_evidence_review"
        retained["deduplication_classification"] = classification
        retained["confirmed_legal_fact"] = False
        retained["formal_lifecycle_write_allowed"] = False
        candidates.append(retained)

    company_groups = []
    for code in sorted(code_occurrence_counts):
        ids = sorted(code_record_ids.get(code, set()))
        company_groups.append({
            "company_code": code,
            "source_occurrence_count": code_occurrence_counts[code],
            "distinct_record_id_count": len(ids),
            "record_ids": ids,
            "multiple_records_same_company_code": len(ids) > 1 or code_occurrence_counts[code] > len(ids),
            "review_required": True,
        })

    classifications = defaultdict(int)
    for candidate in candidates:
        classifications[candidate["deduplication_classification"]] += 1

    return {
        "schema_version": 1,
        "source": "neeq_official_termination_result_list",
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "audit_baseline": {
            "status": audit.get("status"),
            "complete_coverage": bool(audit.get("complete_coverage")),
            "fetched_page_count": audit.get("fetched_page_count", 0),
            "reported_total_pages": audit.get("reported_total_pages"),
            "fetched_record_count": audit.get("fetched_record_count", 0),
            "reported_total_elements": audit.get("reported_total_elements"),
            "unique_company_code_count": audit.get("unique_company_code_count", 0),
            "exact_duplicate_record_ids": list(audit.get("exact_duplicate_record_ids", [])),
            "conflicting_duplicate_record_ids": list(audit.get("conflicting_duplicate_record_ids", [])),
            "attachment_url_count": audit.get("attachment_url_count", 0),
            "attachment_kind_counts": dict(audit.get("attachment_kind_counts", {})),
            "attachment_coverage_complete": bool(audit.get("attachment_coverage_complete")),
        },
        "candidate_count": len(candidates),
        "source_occurrence_count": len(occurrences),
        "deduplication_classification_counts": dict(sorted(classifications.items())),
        "candidates": candidates,
        "company_code_groups": company_groups,
        "safety": {
            "source_records_deleted": 0,
            "database_writes": 0,
            "formal_issuer_master_writes": 0,
            "formal_lifecycle_writes": 0,
            "confirmed_legal_facts_created": 0,
            "read_only": True,
            "manual_evidence_review_required": True,
        },
    }
