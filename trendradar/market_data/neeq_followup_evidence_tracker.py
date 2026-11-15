# 本模块建立NEEQ历史后续公告追踪任务，并将人工提供的证据元数据关联到证券及初始公告；只读，不确认法律事实。
"""Build a read-only NEEQ follow-up notice and evidence tracking register."""
from __future__ import annotations
import hashlib
from datetime import datetime, timezone

FOLLOW_UP_EVIDENCE_TYPES = (
    "reconsideration_application", "reconsideration_decision",
    "supplementary_termination_notice", "final_termination_announcement",
    "transfer_or_delisting_notice", "issuer_announcement", "other_official_document",
)
RECOMMENDED_EVIDENCE_TYPES = [
    "reconsideration_application", "reconsideration_decision",
    "supplementary_termination_notice", "final_termination_announcement",
    "transfer_or_delisting_notice",
]

def _clean(value):
    return str(value).strip() if value is not None else ""

def _case_key(row):
    return (_clean(row.get("security_code")), _clean(row.get("notice_document_number")))

def _fingerprint(row):
    raw = "|".join(_clean(row.get(k)) for k in (
        "security_code", "notice_document_number", "source_url",
        "source_document_number", "source_sha256",
    ))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def _search_terms(row):
    code = _clean(row.get("security_code"))
    name = _clean(row.get("company_short_name") or row.get("company_name"))
    number = _clean(row.get("notice_document_number"))
    terms = [
        f'"{code}" "{name}" 全国股转 终止挂牌',
        f'"{code}" "{name}" 复核 终止挂牌',
        f'"{code}" "{name}" 后续公告',
    ]
    if number:
        terms.append(f'"{number}" "{code}"')
    return terms

def build_neeq_followup_evidence_tracker(reconciliation_report, evidence_records=None, *, generated_at=None):
    """Create research tasks and attach supplied evidence metadata without legal promotion."""
    cases = {}
    for case in reconciliation_report.get("date_reconciliation_cases") or []:
        key = _case_key(case)
        if key[0]:
            cases.setdefault(key, dict(case))
    for candidate in reconciliation_report.get("supplemental_candidate_evidence") or []:
        key = _case_key(candidate)
        if key[0]:
            row = cases.setdefault(key, dict(candidate))
            row["supplemental_candidate"] = True

    evidence_by_key = {key: [] for key in cases}
    unlinked, fingerprints = [], set()
    for source in evidence_records or []:
        row = dict(source)
        code = _clean(row.get("security_code"))
        if not code:
            unlinked.append({"input_record": row, "link_status": "rejected_missing_security_code"})
            continue
        if row.get("evidence_type") not in FOLLOW_UP_EVIDENCE_TYPES:
            unlinked.append({"input_record": row, "link_status": "rejected_unknown_evidence_type"})
            continue
        key = (code, _clean(row.get("notice_document_number")))
        if key not in evidence_by_key and (code, "") in evidence_by_key:
            key = (code, "")
        if key not in evidence_by_key:
            unlinked.append({"input_record": row, "link_status": "no_matching_security_notice_case"})
            continue
        fingerprint = _fingerprint(row)
        if fingerprint in fingerprints:
            continue
        fingerprints.add(fingerprint)
        evidence_by_key[key].append({
            "evidence_id": _clean(row.get("evidence_id")) or f"evidence-{fingerprint[:16]}",
            "evidence_type": row["evidence_type"],
            "source_title": _clean(row.get("source_title")) or None,
            "source_url": _clean(row.get("source_url")) or None,
            "source_document_number": _clean(row.get("source_document_number")) or None,
            "published_date": _clean(row.get("published_date")) or None,
            "source_sha256": _clean(row.get("source_sha256")) or None,
            "related_dates": row.get("related_dates") or {},
            "review_notes": _clean(row.get("review_notes")) or None,
            "evidence_fingerprint": fingerprint,
            "review_status": "pending_manual_review",
            "supports_legal_fact": None,
            "confirmed_legal_fact": False,
            "formal_lifecycle_write_allowed": False,
        })

    tasks = []
    for key, case in sorted(cases.items()):
        code, notice_number = key
        attached = evidence_by_key.get(key, [])
        task_id = hashlib.sha256(f"{code}|{notice_number}".encode("utf-8")).hexdigest()[:16]
        date_status = case.get("date_reconciliation_status")
        tasks.append({
            "task_id": f"neeq-followup-{task_id}",
            "security_code": code,
            "company_name": case.get("company_name"),
            "company_short_name": case.get("company_short_name"),
            "notice_document_number": notice_number or None,
            "notice_decision_date": case.get("notice_decision_date"),
            "notice_announcement_date": case.get("notice_announcement_date"),
            "notice_planned_termination_date": case.get("notice_planned_termination_date"),
            "official_result_delisting_date": case.get("official_result_delisting_date"),
            "official_result_record_id": case.get("official_result_record_id"),
            "initial_notice_source_url": case.get("source_attachment_url"),
            "date_reconciliation_status": date_status,
            "date_difference_days": case.get("date_difference_days"),
            "supplemental_candidate": bool(case.get("supplemental_candidate")),
            "priority": "high" if date_status in ("later_than_planned", "earlier_than_planned") else "normal",
            "recommended_evidence_types": RECOMMENDED_EVIDENCE_TYPES,
            "suggested_search_terms": _search_terms(case),
            "evidence_count": len(attached),
            "evidence_records": attached,
            "task_status": "evidence_attached_pending_review" if attached else "pending_evidence_search",
            "confirmed_legal_fact": False,
            "formal_lifecycle_write_allowed": False,
        })
    tasks.sort(key=lambda x: (0 if x["priority"] == "high" else 1, x["security_code"], x["notice_document_number"] or ""))
    status_counts = {}
    for task in tasks:
        status_counts[task["task_status"]] = status_counts.get(task["task_status"], 0) + 1
    return {
        "schema_version": 1,
        "tracker_version": "neeq_followup_evidence_tracker_v1",
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "source_reconciliation_version": reconciliation_report.get("audit_version"),
        "summary": {
            "task_count": len(tasks),
            "high_priority_task_count": sum(t["priority"] == "high" for t in tasks),
            "evidence_record_count": sum(t["evidence_count"] for t in tasks),
            "task_status_counts": status_counts,
            "unlinked_evidence_count": len(unlinked),
            "supplemental_candidate_task_count": sum(t["supplemental_candidate"] for t in tasks),
        },
        "tasks": tasks,
        "unlinked_evidence_records": unlinked,
        "safety": {
            "network_downloads": 0, "database_writes": 0, "formal_lifecycle_writes": 0,
            "legal_fact_confirmations": 0, "automatic_date_overwrites": 0,
            "manual_review_required": True, "read_only": True,
        },
    }
