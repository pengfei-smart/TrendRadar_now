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
    left_date = _parse_date(left)
    right_date = _parse_date(right)
    if left_date is None or right_date is None:
        return None
    return (left_date - right_date).days


def build_report(crosscheck_report: dict[str, Any]) -> dict[str, Any]:
    """对照原始接口日期、已解析决定日期、条件终止日期和记录创建日期。"""
    candidates = crosscheck_report.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError("crosscheck report missing candidates list")

    rows: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    for item in candidates:
        interface_date = item.get("interface_result_date")
        create_date = _epoch_ms_to_china_date(item.get("interface_create_time_epoch_ms"))
        memberships = item.get("batch_memberships") or []
        supplemental = item.get("supplemental_independent_decisions") or []

        decision_evidence: list[dict[str, Any]] = []
        for membership in memberships:
            decision_evidence.append({
                "evidence_type": "parsed_batch_membership",
                "membership_type": membership.get("membership_type"),
                "notice_number": membership.get("batch_notice_number"),
                "decision_date": membership.get("formal_decision_date"),
                "conditional_termination_date": (
                    membership.get("conditional_termination_date")
                    or membership.get("stated_termination_date")
                ),
                "source_url": membership.get("source_attachment_url"),
                "review_status": membership.get("review_status"),
                "confirmed_legal_fact": membership.get("confirmed_legal_fact", False),
            })
        for decision in supplemental:
            decision_evidence.append({
                "evidence_type": "supplemental_independent_decision",
                "membership_type": "independent_formal_decision",
                "notice_number": decision.get("notice_number"),
                "decision_date": decision.get("decision_date"),
                "conditional_termination_date": decision.get("conditional_termination_date"),
                "source_url": decision.get("source_url"),
                "review_status": decision.get("evidence_status"),
                "confirmed_legal_fact": False,
            })

        decision_dates = sorted({
            value for evidence in decision_evidence
            for value in [evidence.get("decision_date")] if _parse_date(value)
        })
        conditional_dates = sorted({
            value for evidence in decision_evidence
            for value in [evidence.get("conditional_termination_date")] if _parse_date(value)
        })
        matched_conditional_dates = [
            value for value in conditional_dates if value == interface_date
        ]
        if not decision_evidence:
            comparison_status = "no_parsed_decision_date_evidence"
        elif not conditional_dates:
            comparison_status = "decision_found_conditional_date_missing"
        elif matched_conditional_dates:
            comparison_status = "interface_date_matches_conditional_termination_date"
        else:
            comparison_status = "interface_date_differs_from_conditional_termination_date"
        status_counts[comparison_status] += 1

        rows.append({
            "record_id": item.get("record_id"),
            "security_code": item.get("security_code"),
            "company_name": item.get("company_name"),
            "interface_result_date": interface_date,
            "interface_create_date_china": create_date,
            "interface_date_minus_create_date_days": _days_between(interface_date, create_date),
            "decision_dates": decision_dates,
            "conditional_termination_dates": conditional_dates,
            "interface_date_minus_conditional_date_days": {
                value: _days_between(interface_date, value) for value in conditional_dates
            },
            "comparison_status": comparison_status,
            "decision_evidence": decision_evidence,
            "batch_relation_status": item.get("batch_relation_status"),
            "raw_record_sha256": item.get("raw_record_sha256"),
            "review_status": "pending_manual_review",
            "actual_termination_date": None,
            "actual_termination_date_confirmed": False,
            "notes": [
                "接口日期仅作为原始结果字段保留，不自动解释为实际终止挂牌日期。",
                "条件终止日期不等同于已确认的实际终止挂牌日期。",
                "未匹配到已解析公告不代表历史上不存在公告。",
            ],
        })

    rows.sort(key=lambda row: (row.get("interface_result_date") or "", row.get("security_code") or ""))
    return {
        "schema_version": 1,
        "report_version": "neeq_datatype0_date_audit_v1",
        "source_report_version": crosscheck_report.get("report_version"),
        "summary": {
            "candidate_count": len(rows),
            "comparison_status_counts": dict(sorted(status_counts.items())),
            "actual_termination_dates_confirmed": 0,
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
    parser.add_argument(
        "--crosscheck-report",
        type=Path,
        default=Path("output/market/neeq_datatype0_notice_crosscheck_v1.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("output/market/neeq_datatype0_date_audit_v1.json"),
    )
    args = parser.parse_args()
    with args.crosscheck_report.open("r", encoding="utf-8") as handle:
        source = json.load(handle)
    if not isinstance(source, dict):
        raise ValueError("crosscheck report root must be an object")
    report = build_report(source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"report_path={args.output}")
    print("safety=read_only; database_writes=0; formal_lifecycle_writes=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
