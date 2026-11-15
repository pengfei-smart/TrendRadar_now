# 本模块登记NEEQ历史公告原件及日期主张，计算本地文件SHA256并保留待人工核验状态；不确认法律事实、不写数据库。
"""Build a read-only NEEQ historical evidence register with local file integrity checks."""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from trendradar.market_data.neeq_followup_evidence_tracker import FOLLOW_UP_EVIDENCE_TYPES

DATE_TYPES = (
    "batch_announcement_date",
    "formal_decision_date",
    "reconsideration_application_date",
    "reconsideration_acceptance_date",
    "reconsideration_decision_date",
    "conditional_termination_date",
    "company_announcement_date",
    "official_result_delisting_date",
    "legal_termination_date",
    "transfer_start_date",
    "transfer_suspension_date",
    "transfer_resumption_date",
    "last_trading_date",
    "other_date",
)


def _clean(value):
    return str(value).strip() if value is not None else ""


def _safe_file(root: Path, relative_path: str) -> Path | None:
    raw = _clean(relative_path)
    if not raw:
        return None
    candidate = (root / raw).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _valid_https_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme == "https" and bool(parsed.hostname)


def _date_assertions(rows):
    result = []
    for index, source in enumerate(rows or [], start=1):
        row = dict(source)
        raw_date = _clean(row.get("date_value"))
        try:
            normalized = date.fromisoformat(raw_date[:10]).isoformat()
        except ValueError:
            normalized = None
        date_type = _clean(row.get("date_type"))
        result.append({
            "assertion_id": _clean(row.get("assertion_id")) or f"date-{index}",
            "date_type": date_type or None,
            "date_value": normalized,
            "source_quote": _clean(row.get("source_quote")) or None,
            "page_number": row.get("page_number"),
            "section": _clean(row.get("section")) or None,
            "date_type_valid": date_type in DATE_TYPES,
            "date_value_valid": normalized is not None,
            "review_status": "pending_manual_review",
            "confirmed_legal_fact": False,
            "formal_lifecycle_write_allowed": False,
        })
    return result


def build_neeq_historical_evidence_register(
    tracker_report: dict,
    evidence_records=None,
    *,
    evidence_root: str | Path,
    generated_at: str | None = None,
) -> dict:
    """Validate evidence metadata and local file hashes; never promote date/legal facts."""
    root = Path(evidence_root).resolve()
    tasks = {}
    for task in tracker_report.get("tasks") or []:
        key = (_clean(task.get("security_code")), _clean(task.get("notice_document_number")))
        if key[0]:
            tasks.setdefault(key, task)

    linked, unlinked, fingerprints = {}, [], set()
    for source in evidence_records or []:
        row = dict(source)
        code = _clean(row.get("security_code"))
        notice = _clean(row.get("notice_document_number"))
        key = (code, notice)
        if not code or key not in tasks:
            unlinked.append({"input_record": row, "link_status": "no_matching_security_notice_task"})
            continue
        evidence_type = _clean(row.get("evidence_type"))
        if evidence_type not in FOLLOW_UP_EVIDENCE_TYPES:
            unlinked.append({"input_record": row, "link_status": "rejected_unknown_evidence_type"})
            continue

        relative_path = _clean(row.get("source_file"))
        file_path = _safe_file(root, relative_path)
        file_status = "source_file_missing"
        actual_sha = None
        if file_path is None:
            file_status = "unsafe_or_missing_relative_path"
        elif not file_path.is_file():
            file_status = "source_file_missing"
        else:
            actual_sha = _sha256(file_path)
            expected_sha = _clean(row.get("source_sha256")).lower()
            file_status = "sha256_match" if not expected_sha or expected_sha == actual_sha else "sha256_mismatch"

        source_url = _clean(row.get("source_url"))
        url_valid = _valid_https_url(source_url)
        assertions = _date_assertions(row.get("date_assertions"))
        metadata_complete = all((
            _clean(row.get("source_title")),
            url_valid,
            _clean(row.get("published_date")),
            bool(actual_sha),
        ))
        assertion_complete = bool(assertions) and all(
            item["date_type_valid"] and item["date_value_valid"] and item["source_quote"]
            for item in assertions
        )
        if file_status == "sha256_mismatch":
            review_status = "file_integrity_mismatch"
        elif file_status != "sha256_match":
            review_status = "source_file_or_path_incomplete"
        elif not metadata_complete:
            review_status = "source_metadata_incomplete"
        elif not assertion_complete:
            review_status = "date_assertion_incomplete_pending_review"
        else:
            review_status = "evidence_integrity_complete_pending_manual_review"

        fingerprint_payload = "|".join((code, notice, actual_sha or relative_path, evidence_type))
        fingerprint = hashlib.sha256(fingerprint_payload.encode("utf-8")).hexdigest()
        if fingerprint in fingerprints:
            continue
        fingerprints.add(fingerprint)
        linked.setdefault(key, []).append({
            "evidence_id": _clean(row.get("evidence_id")) or f"neeq-evidence-{fingerprint[:16]}",
            "security_code": code,
            "notice_document_number": notice or None,
            "evidence_type": evidence_type,
            "source_title": _clean(row.get("source_title")) or None,
            "source_url": source_url or None,
            "source_url_is_https": url_valid,
            "source_document_number": _clean(row.get("source_document_number")) or None,
            "published_date": _clean(row.get("published_date")) or None,
            "source_file": relative_path or None,
            "source_sha256": actual_sha,
            "declared_sha256": _clean(row.get("source_sha256")) or None,
            "file_integrity_status": file_status,
            "date_assertions": assertions,
            "review_notes": _clean(row.get("review_notes")) or None,
            "evidence_fingerprint": fingerprint,
            "review_status": review_status,
            "source_authority_status": "pending_manual_source_verification",
            "supports_legal_fact": None,
            "confirmed_legal_fact": False,
            "formal_lifecycle_write_allowed": False,
        })

    dossiers = []
    for key, task in sorted(tasks.items()):
        records = linked.get(key, [])
        dossiers.append({
            "task_id": task.get("task_id"),
            "security_code": key[0],
            "company_short_name": task.get("company_short_name"),
            "notice_document_number": key[1] or None,
            "initial_notice_source_url": task.get("initial_notice_source_url"),
            "evidence_count": len(records),
            "evidence_records": records,
            "dossier_status": "evidence_attached_pending_review" if records else "pending_evidence_search",
            "confirmed_legal_fact": False,
            "formal_lifecycle_write_allowed": False,
        })

    status_counts = {}
    all_records = [record for rows in linked.values() for record in rows]
    for record in all_records:
        status = record["review_status"]
        status_counts[status] = status_counts.get(status, 0) + 1
    return {
        "schema_version": 1,
        "register_version": "neeq_historical_evidence_register_v1",
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "source_tracker_version": tracker_report.get("tracker_version"),
        "summary": {
            "task_count": len(dossiers),
            "evidence_record_count": len(all_records),
            "date_assertion_count": sum(len(row["date_assertions"]) for row in all_records),
            "review_status_counts": status_counts,
            "unlinked_evidence_count": len(unlinked),
            "sha256_match_count": sum(row["file_integrity_status"] == "sha256_match" for row in all_records),
            "sha256_mismatch_count": sum(row["file_integrity_status"] == "sha256_mismatch" for row in all_records),
        },
        "dossiers": dossiers,
        "unlinked_evidence_records": unlinked,
        "safety": {
            "network_downloads": 0,
            "database_writes": 0,
            "formal_lifecycle_writes": 0,
            "legal_fact_confirmations": 0,
            "automatic_date_overwrites": 0,
            "manual_review_required": True,
            "read_only": True,
        },
    }
