# 本模块比较NEEQ公告解析身份与候选附件关联覆盖，只生成审计结果，不写数据库。
"""Read-only coverage audit between parsed NEEQ notices and linked candidates."""

from __future__ import annotations

from collections import Counter


def build_neeq_notice_coverage_audit(review_report: dict, manifest: dict) -> dict:
    """Classify parsed notice identities against local and global manifest candidates."""
    tasks = {str(t.get("attachment_id") or ""): t for t in manifest.get("tasks") or []}
    global_candidates: dict[str, list[dict]] = {}
    for task in tasks.values():
        for candidate in task.get("linked_candidates") or []:
            code = str(candidate.get("company_code") or "").strip()
            if code:
                global_candidates.setdefault(code, []).append({
                    "attachment_id": task.get("attachment_id"),
                    "url": task.get("url"),
                    "record_id": candidate.get("record_id"),
                    "company_name": candidate.get("company_name"),
                    "company_short_name": candidate.get("company_short_name"),
                })

    items = []
    totals = Counter()
    for report_item in review_report.get("items") or []:
        task = next((t for t in tasks.values() if t.get("url") == report_item.get("url")), {})
        linked = task.get("linked_candidates") or []
        local_codes = {str(c.get("company_code") or "").strip() for c in linked}
        notices = report_item.get("notices") or []
        rows = []
        for notice in notices:
            code = str(notice.get("security_code") or "").strip()
            if not code:
                category = "identity_unresolved"
            elif code in local_codes:
                category = "linked_to_this_attachment"
            elif code in global_candidates:
                category = "candidate_exists_on_other_attachment"
            else:
                category = "candidate_absent_from_manifest"
            totals[category] += 1
            rows.append({
                "security_code": code or None,
                "short_name": notice.get("short_name"),
                "document_number": notice.get("document_number"),
                "identity_source": notice.get("identity_source"),
                "coverage_classification": category,
                "candidate_references": global_candidates.get(code, []) if code else [],
                "review_status": "pending_manual_review",
                "confirmed_legal_fact": False,
                "formal_lifecycle_write_allowed": False,
            })
        items.append({
            "attachment_id": report_item.get("attachment_id"),
            "url": report_item.get("url"),
            "parsed_notice_count": len(notices),
            "linked_candidate_count": len(linked),
            "coverage_counts": dict(Counter(r["coverage_classification"] for r in rows)),
            "notices": rows,
        })
    return {
        "schema_version": 1,
        "audit_type": "neeq_notice_candidate_coverage",
        "attachment_count": len(items),
        "notice_count": sum(x["parsed_notice_count"] for x in items),
        "coverage_classification_counts": dict(totals),
        "items": items,
        "safety": {
            "read_only": True,
            "database_writes": 0,
            "formal_lifecycle_writes": 0,
            "legal_fact_confirmations": 0,
            "manual_review_required": True,
        },
    }
