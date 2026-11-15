# 本模块将NEEQ公告批次名单与官方终止结果候选清单交叉审计，只输出只读报告，不写正式生命周期数据。
"""Reconcile NEEQ batch-notice memberships against the official result candidate manifest."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any


def _code(value: Any) -> str:
    text = str(value or "").strip()
    return text if len(text) == 6 and text.isdigit() else ""


def _name(value: Any) -> str:
    return "".join(str(value or "").split()).strip("；;，,。 ")


def _candidate_map(candidate_manifest: dict) -> dict[str, list[dict]]:
    root = candidate_manifest.get("manifest") if isinstance(candidate_manifest.get("manifest"), dict) else candidate_manifest
    candidates = root.get("candidates") or []
    output: dict[str, list[dict]] = defaultdict(list)
    for candidate in candidates:
        code = _code(candidate.get("company_code"))
        if code:
            output[code].append(candidate)
    return output


def _coverage_lookup(coverage_audit: dict) -> dict[tuple[str, str, str], dict]:
    lookup = {}
    for item in coverage_audit.get("items") or []:
        url = str(item.get("url") or "")
        for notice in item.get("notices") or []:
            code = _code(notice.get("security_code"))
            number = str(notice.get("document_number") or "")
            if code:
                lookup[(code, number, url)] = notice
    return lookup


def build_neeq_termination_batch_reconciliation(
    review_report: dict,
    coverage_audit: dict,
    candidate_manifest: dict,
    *,
    generated_at: str | None = None,
) -> dict:
    """Build batch membership, candidate coverage, and conflict rows without writes.

    Parsed formal notices are decision memberships. Parser contextual identities are
    retained as background memberships only. A later formal decision for a code that
    previously appeared in a background appendix is additionally labelled as a
    later-batch decision; the original background membership is never overwritten.
    """
    candidates_by_code = _candidate_map(candidate_manifest)
    coverage = _coverage_lookup(coverage_audit)
    memberships: list[dict] = []
    unresolved: list[dict] = []
    seen_memberships: set[tuple[str, str, str, str]] = set()

    for item in review_report.get("items") or []:
        url = str(item.get("url") or "")
        attachment_id = item.get("attachment_id")
        for notice in item.get("notices") or []:
            code = _code(notice.get("security_code"))
            number = str(notice.get("document_number") or "")
            if not code:
                unresolved.append({
                    "membership_type": "formal_decision_company",
                    "security_code": None,
                    "company_name": notice.get("company_name"),
                    "company_short_name": notice.get("short_name"),
                    "document_number": number or None,
                    "source_attachment_url": url or None,
                    "identity_source": notice.get("identity_source"),
                    "review_status": "pending_manual_review",
                    "issue": "identity_unresolved",
                    "confirmed_legal_fact": False,
                    "formal_lifecycle_write_allowed": False,
                })
                continue
            key = (code, number, url, "formal_decision_company")
            if key in seen_memberships:
                continue
            seen_memberships.add(key)
            refs = candidates_by_code.get(code, [])
            coverage_row = coverage.get((code, number, url), {})
            candidate_names = sorted({_name(row.get("company_name")) for row in refs if _name(row.get("company_name"))})
            notice_name = _name(notice.get("company_name"))
            notice_short = _name(notice.get("short_name"))
            name_match = None
            if candidate_names and notice_name:
                name_match = notice_name in candidate_names
            elif refs and notice_short:
                short_names = {_name(row.get("company_short_name")) for row in refs if _name(row.get("company_short_name"))}
                name_match = notice_short in short_names if short_names else None
            issues = []
            if not refs:
                issues.append("candidate_absent_from_result_manifest")
            if name_match is False:
                issues.append("company_name_mismatch")
            classification = coverage_row.get("coverage_classification")
            if classification and classification not in ("linked_to_this_attachment",):
                issues.append(classification)
            memberships.append({
                "security_code": code,
                "company_name": notice.get("company_name"),
                "company_short_name": notice.get("short_name"),
                "batch_notice_number": number or None,
                "batch_announcement_date": notice.get("announcement_date"),
                "formal_decision_date": notice.get("decision_date"),
                "conditional_termination_date": notice.get("planned_termination_date"),
                "stated_termination_date": notice.get("stated_termination_date"),
                "suspension_date": notice.get("suspension_date"),
                "membership_type": "formal_decision_company",
                "batch_progression_type": None,
                "source_attachment_id": attachment_id,
                "source_attachment_url": url or None,
                "identity_source": notice.get("identity_source"),
                "result_candidate_references": [{
                    "record_id": row.get("record_id"),
                    "result_list_date": row.get("delisting_date"),
                    "company_name": row.get("company_name"),
                    "attachment_url": row.get("attachment_url"),
                } for row in refs],
                "candidate_name_match": name_match,
                "coverage_classification": classification or ("candidate_absent_from_manifest" if not refs else "not_classified"),
                "issues": sorted(set(issues)),
                "review_status": "pending_manual_review",
                "confirmed_legal_fact": False,
                "formal_lifecycle_write_allowed": False,
            })

        for identity in item.get("contextual_identities") or []:
            code = _code(identity.get("security_code"))
            if not code:
                unresolved.append({
                    "membership_type": "background_pending_company",
                    "security_code": None,
                    "company_short_name": identity.get("short_name"),
                    "source_attachment_url": url or None,
                    "identity_source": identity.get("identity_source"),
                    "review_status": "pending_manual_review",
                    "issue": "identity_unresolved",
                    "confirmed_legal_fact": False,
                    "formal_lifecycle_write_allowed": False,
                })
                continue
            key = (code, "", url, "background_pending_company")
            if key in seen_memberships:
                continue
            seen_memberships.add(key)
            refs = candidates_by_code.get(code, [])
            candidate_names = sorted({_name(row.get("company_name")) for row in refs if _name(row.get("company_name"))})
            expected_short = _name(identity.get("short_name"))
            short_names = {_name(row.get("company_short_name")) for row in refs if _name(row.get("company_short_name"))}
            name_match = (expected_short in short_names) if expected_short and short_names else None
            memberships.append({
                "security_code": code,
                "company_name": identity.get("company_name"),
                "company_short_name": identity.get("short_name"),
                "batch_notice_number": None,
                "batch_announcement_date": None,
                "formal_decision_date": None,
                "conditional_termination_date": None,
                "stated_termination_date": None,
                "suspension_date": None,
                "membership_type": "background_pending_company",
                "batch_progression_type": None,
                "source_attachment_id": attachment_id,
                "source_attachment_url": url or None,
                "identity_source": identity.get("identity_source"),
                "result_candidate_references": [{
                    "record_id": row.get("record_id"),
                    "result_list_date": row.get("delisting_date"),
                    "company_name": row.get("company_name"),
                    "attachment_url": row.get("attachment_url"),
                } for row in refs],
                "candidate_name_match": name_match,
                "issues": ["background_membership_is_not_a_formal_decision"],
                "review_status": "pending_manual_review",
                "confirmed_legal_fact": False,
                "formal_lifecycle_write_allowed": False,
            })

    background_codes = {row["security_code"] for row in memberships if row["membership_type"] == "background_pending_company"}
    formal_codes = {row["security_code"] for row in memberships if row["membership_type"] == "formal_decision_company"}
    for row in memberships:
        if row["membership_type"] == "formal_decision_company" and row["security_code"] in background_codes:
            row["batch_progression_type"] = "later_batch_decision_company"

    candidate_rows = []
    for code, refs in sorted(candidates_by_code.items()):
        code_memberships = [row for row in memberships if row["security_code"] == code]
        types = sorted({row["membership_type"] for row in code_memberships})
        candidate_rows.append({
            "security_code": code,
            "result_candidate_count": len(refs),
            "result_record_ids": sorted({str(row.get("record_id") or "") for row in refs}),
            "batch_membership_types": types,
            "batch_notice_numbers": sorted({row["batch_notice_number"] for row in code_memberships if row.get("batch_notice_number")}),
            "has_formal_decision_membership": "formal_decision_company" in types,
            "has_background_membership": "background_pending_company" in types,
            "batch_progression_type": "later_batch_decision_company" if code in background_codes and code in formal_codes else None,
            "coverage_status": "matched_to_batch_notice" if code_memberships else "no_parsed_batch_membership",
            "review_status": "pending_manual_review",
            "confirmed_legal_fact": False,
            "formal_lifecycle_write_allowed": False,
        })

    membership_counts = Counter(row["membership_type"] for row in memberships)
    progression_count = sum(row.get("batch_progression_type") == "later_batch_decision_company" for row in memberships)
    name_conflicts = [row for row in memberships if row.get("candidate_name_match") is False]
    candidate_without_batch = [row for row in candidate_rows if row["coverage_status"] == "no_parsed_batch_membership"]
    return {
        "schema_version": 1,
        "audit_version": "neeq_termination_batch_reconciliation_v1",
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_reports": {
            "review_parser_version": review_report.get("parser_version"),
            "coverage_audit_type": coverage_audit.get("audit_type"),
            "candidate_manifest_source": (candidate_manifest.get("manifest") or candidate_manifest).get("source"),
            "candidate_manifest_generated_at": (candidate_manifest.get("manifest") or candidate_manifest).get("generated_at"),
            "candidate_manifest_count": len(candidates_by_code),
        },
        "summary": {
            "candidate_security_code_count": len(candidate_rows),
            "batch_membership_count": len(memberships),
            "formal_decision_membership_count": membership_counts.get("formal_decision_company", 0),
            "background_membership_count": membership_counts.get("background_pending_company", 0),
            "later_batch_decision_membership_count": progression_count,
            "candidate_without_parsed_batch_membership_count": len(candidate_without_batch),
            "candidate_without_parsed_batch_membership_security_codes": [row["security_code"] for row in candidate_without_batch],
            "candidate_name_conflict_count": len(name_conflicts),
            "unresolved_identity_count": len(unresolved),
            "supplemental_notice_identity_count": sum(1 for row in memberships if not row.get("result_candidate_references")),
        },
        "batch_memberships": sorted(memberships, key=lambda row: (row["security_code"], row["batch_notice_number"] or "", row["membership_type"], row["source_attachment_url"] or "")),
        "candidate_coverage": candidate_rows,
        "unresolved_identities": unresolved,
        "name_conflicts": name_conflicts,
        "safety": {
            "database_writes": 0,
            "formal_lifecycle_writes": 0,
            "legal_fact_confirmations": 0,
            "candidate_manifest_mutations": 0,
            "background_membership_promotions": 0,
            "manual_review_required": True,
            "read_only": True,
        },
    }
