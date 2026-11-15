# 只读审计 NEEQ dataType=0 候选的接口日期、公告日期和记录创建时间。
# -*- coding: utf-8 -*-
"""生成 NEEQ dataType=0 日期对照报告；不确认法律事实，不写数据库。"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

CHINA_TZ = timezone(timedelta(hours=8))
FORMAL_MEMBERSHIP = "formal_decision_company"


def _parse_date(value: Any) -> datetime.date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _epoch_ms_to_china_date(value: Any) -> str | None:
    if not isinstance(value, (int, float)):
        return None
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).astimezone(CHINA_TZ).date().isoformat()


def _days_between(left: str | None, right: str | None) -> int | None:
    left_date, right_date = _parse_date(left), _parse_date(right)
    return (left_date - right_date).days if left_date and right_date else None


def _is_formal_decision(evidence: dict[str, Any]) -> bool:
    return (
        evidence.get("evidence_type") == "supplemental_independent_decision"
        and evidence.get("evidence_status") not in {
            "third_party_notice_index_lead_not_officially_verified",
        }
    ) or (
        evidence.get("evidence_type") == "parsed_batch_membership"
        and evidence.get("membership_type") == FORMAL_MEMBERSHIP
    )


def _formal_decision_key(security_code: str | None, evidence: dict[str, Any]) -> tuple[str | None, ...]:
    """以证券代码、公告文号和条件终止日期识别决定，避免缺失日期造成重复计数。"""
    conditional_date = evidence.get("conditional_termination_date")
    return (
        security_code,
        evidence.get("notice_number"),
        conditional_date or evidence.get("decision_date"),
    )


def build_report(crosscheck_report: dict[str, Any]) -> dict[str, Any]:
    """区分背景名单、正式决定、补充线索和条件日期；保持只读。"""
    candidates = crosscheck_report.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError("crosscheck report missing candidates list")

    rows: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    unique_formal_decision_keys: set[tuple[str | None, ...]] = set()
    duplicate_formal_evidence_count = 0
    for item in candidates:
        interface_date = item.get("interface_result_date")
        create_date = _epoch_ms_to_china_date(item.get("interface_create_time_epoch_ms"))
        decision_evidence: list[dict[str, Any]] = []
        for membership in item.get("batch_memberships") or []:
            decision_evidence.append({
                "evidence_type": "parsed_batch_membership",
                "membership_type": membership.get("membership_type"),
                "notice_number": membership.get("batch_notice_number"),
                "decision_date": membership.get("formal_decision_date"),
                "conditional_termination_date": membership.get("conditional_termination_date") or membership.get("stated_termination_date"),
                "source_url": membership.get("source_attachment_url"),
                "review_status": membership.get("review_status"),
                "confirmed_legal_fact": membership.get("confirmed_legal_fact", False),
            })
        for decision in item.get("supplemental_independent_decisions") or []:
            decision_evidence.append({
                "evidence_type": "supplemental_independent_decision",
                "membership_type": "independent_formal_decision",
                "notice_number": decision.get("notice_number"),
                "decision_date": decision.get("decision_date"),
                "conditional_termination_date": decision.get("conditional_termination_date"),
                "source_url": decision.get("source_url"),
                "evidence_status": decision.get("evidence_status"),
                "review_status": "pending_manual_review",
                "confirmed_legal_fact": False,
                "reported_final_delisting_date": decision.get("reported_final_delisting_date"),
                "reported_final_date_source_status": decision.get("reported_final_date_source_status"),
            })
        formal_evidence = [e for e in decision_evidence if _is_formal_decision(e)]
        security_code = item.get("security_code")
        row_decision_map: dict[tuple[str | None, ...], dict[str, Any]] = {}
        for evidence in formal_evidence:
            key = _formal_decision_key(security_code, evidence)
            existing = row_decision_map.setdefault(key, {
                "security_code": security_code,
                "notice_number": evidence.get("notice_number"),
                "decision_date": None,
                "conditional_termination_date": evidence.get("conditional_termination_date"),
            })
            if not existing["decision_date"] and evidence.get("decision_date"):
                existing["decision_date"] = evidence.get("decision_date")
        row_decision_keys = set(row_decision_map)
        unique_formal_decision_keys.update(row_decision_keys)
        duplicate_formal_evidence_count += len(formal_evidence) - len(row_decision_keys)
        background_evidence = [e for e in decision_evidence if e.get("membership_type") == "background_pending_company"]
        decision_dates = sorted({e["decision_date"] for e in formal_evidence if _parse_date(e.get("decision_date"))})
        conditional_dates = sorted({e["conditional_termination_date"] for e in formal_evidence if _parse_date(e.get("conditional_termination_date"))})
        if formal_evidence and not conditional_dates:
            comparison_status = "formal_decision_found_conditional_date_missing"
        elif formal_evidence and conditional_dates and interface_date in conditional_dates:
            comparison_status = "interface_date_matches_conditional_termination_date"
        elif formal_evidence and conditional_dates:
            comparison_status = "interface_date_differs_from_conditional_termination_date"
        elif background_evidence:
            comparison_status = "background_membership_only"
        else:
            comparison_status = "no_parsed_decision_evidence"
        status_counts[comparison_status] += 1

        leads = item.get("supplemental_leads") or []
        follow_up_required = bool(formal_evidence)
        follow_up_tracking = {
            "follow_up_required": follow_up_required,
            "task_status": "pending_evidence_search" if follow_up_required else "not_started_no_formal_decision_evidence",
            "procedure_stage": "post_decision_procedure" if follow_up_required else "formal_decision_not_established",
            "review_application_status": "unknown",
            "review_decision_status": "unknown",
            "supplementary_decision_evidence": [],
            "final_termination_date": None,
            "final_termination_source_url": None,
            "final_termination_evidence_status": "not_found",
            "required_evidence_types": [
                "review_application_or_non_application_evidence",
                "review_acceptance_or_rejection_decision",
                "supplementary_or_revised_termination_decision",
                "final_termination_announcement",
                "securities_registration_or_transfer_exit_record",
            ] if follow_up_required else [],
            "review_status": "pending_manual_review",
        }
        rows.append({
            "record_id": item.get("record_id"),
            "security_code": item.get("security_code"),
            "company_name": item.get("company_name"),
            "interface_result_date": interface_date,
            "interface_create_date_china": create_date,
            "interface_date_minus_create_date_days": _days_between(interface_date, create_date),
            "decision_dates": decision_dates,
            "conditional_termination_dates": conditional_dates,
            "interface_date_minus_conditional_date_days": {d: _days_between(interface_date, d) for d in conditional_dates},
            "comparison_status": comparison_status,
            "decision_evidence": decision_evidence,
            "formal_decision_evidence_count": len(formal_evidence),
            "formal_decision_unique_count": len(row_decision_keys),
            "formal_decision_duplicate_evidence_count": len(formal_evidence) - len(row_decision_keys),
            "formal_decision_unique_keys": [
                row_decision_map[key]
                for key in sorted(row_decision_keys, key=lambda value: tuple(part or "" for part in value))
            ],
            "background_membership_evidence_count": len(background_evidence),
            "supplemental_unverified_leads": leads,
            "batch_relation_status": item.get("batch_relation_status"),
            "raw_record_sha256": item.get("raw_record_sha256"),
            "review_status": "pending_manual_review",
            "follow_up_tracking": follow_up_tracking,
            "actual_termination_date": None,
            "actual_termination_date_confirmed": False,
            "notes": [
                "接口日期仅作为原始结果字段保留，不自动解释为实际终止挂牌日期。",
                "条件终止日期不等同于已确认的实际终止挂牌日期。",
                "背景名单成员不等于正式终止挂牌决定对象。",
                "未匹配到已解析公告不代表历史上不存在公告。",
            ],
        })

    rows.sort(key=lambda row: (row.get("interface_result_date") or "", row.get("security_code") or ""))
    return {
        "schema_version": 4,
        "report_version": "neeq_datatype0_date_audit_v4",
        "source_report_version": crosscheck_report.get("report_version"),
        "summary": {
            "candidate_count": len(rows),
            "comparison_status_counts": dict(sorted(status_counts.items())),
            "formal_decision_evidence_candidate_count": sum(r["formal_decision_evidence_count"] > 0 for r in rows),
            "formal_decision_evidence_record_count": sum(r["formal_decision_evidence_count"] for r in rows),
            "formal_decision_unique_count": len(unique_formal_decision_keys),
            "duplicate_formal_decision_evidence_count": duplicate_formal_evidence_count,
            "background_membership_only_candidate_count": sum(r["comparison_status"] == "background_membership_only" for r in rows),
            "supplemental_unverified_lead_count": sum(len(r["supplemental_unverified_leads"]) for r in rows),
            "actual_termination_dates_confirmed": 0,
            "follow_up_task_candidate_count": sum(r["follow_up_tracking"]["follow_up_required"] for r in rows),
            "follow_up_pending_count": sum(r["follow_up_tracking"]["task_status"] == "pending_evidence_search" for r in rows),
            "manual_review_required": True,
        },
        "candidates": rows,
        "safety": {
            "read_only": True,
            "database_writes": 0,
            "formal_lifecycle_writes": 0,
            "legal_fact_confirmations": 0,
            "data_type_semantics_inferred": False,
            "interface_date_promoted_to_actual_termination_date": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--crosscheck-report", type=Path, default=Path("output/market/neeq_datatype0_notice_crosscheck_v2.json"))
    parser.add_argument("--output", type=Path, default=Path("output/market/neeq_datatype0_date_audit_v4.json"))
    args = parser.parse_args()
    with args.crosscheck_report.open("r", encoding="utf-8") as handle:
        source = json.load(handle)
    if not isinstance(source, dict):
        raise ValueError("crosscheck report root must be an object")
    report = build_report(source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"report_path={args.output}")
    print("safety=read_only; database_writes=0; formal_lifecycle_writes=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
