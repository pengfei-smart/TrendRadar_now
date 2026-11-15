# 本模块对比NEEQ公告计划终止日期与官方结果列表日期，并整理候选清单缺口；只生成只读核验报告。
"""Build a read-only NEEQ date reconciliation and supplemental identity review."""

from __future__ import annotations

from datetime import date, datetime, timezone


def _as_iso_date(value):
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return None


def _day_difference(planned, official):
    left = _as_iso_date(planned)
    right = _as_iso_date(official)
    if not left or not right:
        return None
    return (date.fromisoformat(right) - date.fromisoformat(left)).days


def _find_notice(review_items, comparison):
    code = str(comparison.get("security_code") or "")
    number = str(comparison.get("notice_document_number") or "")
    for item in review_items:
        for notice in item.get("notices") or []:
            if str(notice.get("security_code") or "") != code:
                continue
            if number and str(notice.get("document_number") or "") != number:
                continue
            return item, notice
    return {}, {}


def build_neeq_date_reconciliation_audit(
    review_report: dict,
    coverage_audit: dict,
    manifest: dict,
    *,
    generated_at: str | None = None,
) -> dict:
    """Create date-difference cases and supplemental candidate evidence without writes."""
    review_items = review_report.get("items") or []
    date_cases = []
    for item in review_items:
        for comparison in item.get("comparisons") or []:
            planned = comparison.get("notice_planned_termination_date") or comparison.get("notice_stated_termination_date")
            official = comparison.get("result_list_date")
            delta = _day_difference(planned, official)
            status = comparison.get("date_consistency_status")
            if status in (None, "", "consistent", "not_comparable") and delta in (None, 0):
                continue
            source_item, notice = _find_notice(review_items, comparison)
            date_cases.append({
                "security_code": comparison.get("security_code"),
                "company_name": comparison.get("candidate_company_name") or notice.get("company_name"),
                "company_short_name": comparison.get("candidate_company_short_name") or notice.get("short_name"),
                "notice_document_number": comparison.get("notice_document_number"),
                "notice_decision_date": notice.get("decision_date"),
                "notice_announcement_date": notice.get("announcement_date"),
                "notice_planned_termination_date": _as_iso_date(planned),
                "official_result_delisting_date": _as_iso_date(official),
                "date_difference_days": delta,
                "date_reconciliation_status": (
                    "matched" if delta == 0 else
                    "later_than_planned" if delta is not None and delta > 0 else
                    "earlier_than_planned" if delta is not None and delta < 0 else
                    "missing_official_date" if planned else
                    "pending_manual_review"
                ),
                "source_attachment_url": source_item.get("url") or item.get("url"),
                "official_result_record_id": comparison.get("record_id"),
                "original_comparison_status": status,
                "original_issues": comparison.get("issues") or [],
                "review_status": "pending_manual_review",
                "confirmed_legal_fact": False,
                "formal_lifecycle_write_allowed": False,
            })

    candidate_refs = {}
    for task in manifest.get("tasks") or []:
        for candidate in task.get("linked_candidates") or []:
            code = str(candidate.get("company_code") or "").strip()
            if code:
                candidate_refs.setdefault(code, []).append({
                    "record_id": candidate.get("record_id"),
                    "attachment_id": task.get("attachment_id"),
                    "attachment_url": task.get("url"),
                    "company_name": candidate.get("company_name"),
                    "company_short_name": candidate.get("company_short_name"),
                })

    supplemental = []
    seen = set()
    for audit_item in coverage_audit.get("items") or []:
        for row in audit_item.get("notices") or []:
            if row.get("coverage_classification") != "candidate_absent_from_manifest":
                continue
            code = str(row.get("security_code") or "").strip()
            if not code:
                continue
            key = (code, str(row.get("document_number") or ""), str(audit_item.get("url") or ""))
            if key in seen:
                continue
            seen.add(key)
            supplemental.append({
                "security_code": code,
                "company_short_name": row.get("short_name"),
                "notice_document_number": row.get("document_number"),
                "source_attachment_url": audit_item.get("url"),
                "coverage_classification": row.get("coverage_classification"),
                "existing_manifest_references": candidate_refs.get(code, []),
                "candidate_status": "supplemental_candidate_pending_manual_evidence_review",
                "confirmed_legal_fact": False,
                "formal_lifecycle_write_allowed": False,
            })

    date_cases.sort(key=lambda row: (row.get("security_code") or "", row.get("notice_document_number") or ""))
    supplemental.sort(key=lambda row: (row["security_code"], row["notice_document_number"] or ""))
    statuses = {}
    for row in date_cases:
        key = row["date_reconciliation_status"]
        statuses[key] = statuses.get(key, 0) + 1

    return {
        "schema_version": 1,
        "audit_version": "neeq_date_reconciliation_audit_v1",
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "source_reports": {
            "review_parser_version": review_report.get("parser_version"),
            "coverage_audit_version": coverage_audit.get("audit_version") or coverage_audit.get("schema_version"),
            "manifest_source": manifest.get("source"),
        },
        "summary": {
            "date_case_count": len(date_cases),
            "date_reconciliation_status_counts": statuses,
            "supplemental_candidate_count": len(supplemental),
            "supplemental_candidate_security_codes": sorted({row["security_code"] for row in supplemental}),
        },
        "date_reconciliation_cases": date_cases,
        "supplemental_candidate_evidence": supplemental,
        "safety": {
            "database_writes": 0,
            "formal_lifecycle_writes": 0,
            "legal_fact_confirmations": 0,
            "automatic_candidate_manifest_mutations": 0,
            "manual_review_required": True,
            "read_only": True,
        },
    }
}
