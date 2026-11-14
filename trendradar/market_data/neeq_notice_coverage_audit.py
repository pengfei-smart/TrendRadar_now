# 本模块比较NEEQ公告解析身份与候选附件关联覆盖，并反向列出未在对应附件出现的候选证券；只生成只读审计结果。
"""Read-only coverage audit between parsed NEEQ notices and linked candidates."""

from __future__ import annotations

from collections import Counter, defaultdict


def build_neeq_notice_coverage_audit(review_report: dict, manifest: dict) -> dict:
    """Classify notice identities and report reverse candidate-to-notice gaps."""
    tasks = {str(t.get("attachment_id") or ""): t for t in manifest.get("tasks") or []}
    tasks_by_url = {str(t.get("url") or ""): t for t in tasks.values()}

    global_candidates: dict[str, list[dict]] = defaultdict(list)
    for task in tasks.values():
        for candidate in task.get("linked_candidates") or []:
            code = str(candidate.get("company_code") or "").strip()
            if code:
                global_candidates[code].append({
                    "attachment_id": task.get("attachment_id"),
                    "url": task.get("url"),
                    "record_id": candidate.get("record_id"),
                    "company_name": candidate.get("company_name"),
                    "company_short_name": candidate.get("company_short_name"),
                })

    items = []
    totals = Counter()
    global_notice_occurrences: dict[str, list[dict]] = defaultdict(list)
    for report_item in review_report.get("items") or []:
        attachment_id = str(report_item.get("attachment_id") or "")
        task = tasks.get(attachment_id) or tasks_by_url.get(str(report_item.get("url") or ""), {})
        linked = task.get("linked_candidates") or []
        linked_by_code: dict[str, list[dict]] = defaultdict(list)
        for candidate in linked:
            code = str(candidate.get("company_code") or "").strip()
            if code:
                linked_by_code[code].append(candidate)
        notices = report_item.get("notices") or []
        notice_code_counts = Counter(
            str(notice.get("security_code") or "").strip()
            for notice in notices if str(notice.get("security_code") or "").strip()
        )
        rows = []
        for notice in notices:
            code = str(notice.get("security_code") or "").strip()
            if not code:
                category = "identity_unresolved"
            elif code in linked_by_code:
                category = "linked_to_this_attachment"
            elif code in global_candidates:
                category = "candidate_exists_on_other_attachment"
            else:
                category = "candidate_absent_from_manifest"
            totals[category] += 1
            if code:
                global_notice_occurrences[code].append({
                    "attachment_id": attachment_id or task.get("attachment_id"),
                    "url": report_item.get("url"),
                    "short_name": notice.get("short_name"),
                    "document_number": notice.get("document_number"),
                })
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

        missing_candidates = []
        for code in sorted(set(linked_by_code) - set(notice_code_counts)):
            refs = [{
                "record_id": candidate.get("record_id"),
                "company_name": candidate.get("company_name"),
                "company_short_name": candidate.get("company_short_name"),
                "delisting_date": candidate.get("delisting_date"),
            } for candidate in linked_by_code[code]]
            missing_candidates.append({
                "security_code": code,
                "candidate_occurrence_count": len(refs),
                "candidate_references": refs,
                "review_status": "pending_manual_review",
                "confirmed_legal_fact": False,
                "formal_lifecycle_write_allowed": False,
            })

        repeated_notice_codes = [{
            "security_code": code,
            "occurrence_count": count,
        } for code, count in sorted(notice_code_counts.items()) if count > 1]
        items.append({
            "attachment_id": report_item.get("attachment_id") or task.get("attachment_id"),
            "url": report_item.get("url") or task.get("url"),
            "parsed_notice_count": len(notices),
            "distinct_notice_security_code_count": len(notice_code_counts),
            "repeated_notice_code_occurrence_count": sum(count - 1 for count in notice_code_counts.values() if count > 1),
            "repeated_notice_codes": repeated_notice_codes,
            "linked_candidate_count": len(linked),
            "linked_candidate_distinct_security_code_count": len(linked_by_code),
            "linked_candidate_codes_without_notice": sorted(set(linked_by_code) - set(notice_code_counts)),
            "candidate_codes_without_notice": missing_candidates,
            "coverage_counts": dict(Counter(r["coverage_classification"] for r in rows)),
            "notices": rows,
        })

    repeated_codes = [{
        "security_code": code,
        "occurrence_count": len(occurrences),
        "attachment_ids": sorted({str(item.get("attachment_id") or "") for item in occurrences}),
        "occurrences": occurrences,
    } for code, occurrences in sorted(global_notice_occurrences.items()) if len(occurrences) > 1]

    return {
        "schema_version": 2,
        "audit_type": "neeq_notice_candidate_coverage",
        "attachment_count": len(items),
        "notice_count": sum(x["parsed_notice_count"] for x in items),
        "distinct_notice_security_code_count": len(global_notice_occurrences),
        "repeated_notice_code_occurrence_count": sum(len(v) - 1 for v in global_notice_occurrences.values() if len(v) > 1),
        "repeated_notice_codes": repeated_codes,
        "manifest_distinct_candidate_security_code_count": len(global_candidates),
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
