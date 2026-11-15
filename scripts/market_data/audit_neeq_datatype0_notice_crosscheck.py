# 只读核验 NEEQ dataType=0 候选与公告批次的关系。
# -*- coding: utf-8 -*-
"""只读核验全国股转系统 dataType=0 终止挂牌候选与已解析公告批次的关系。"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SUPPLEMENTAL_DECISIONS = {
    "836614": [{
        "notice_number": "2019-1071",
        "decision_date": "2019-07-19",
        "conditional_termination_date": "2019-07-29",
        "source_url": "https://www.neeq.com.cn/important_news/200006332.html",
        "evidence_status": "official_notice_identity_and_dates_crosschecked",
        "note": "独立于2019-984的后续正式决定；2019-984附表1背景身份不替代本决定。",
    }],
    "836654": [{
        "notice_number": "2019-1354",
        "decision_date": "2019-08-28",
        "conditional_termination_date": "2019-09-05",
        "source_url": "https://www.neeq.com.cn/",
        "evidence_status": "official_announcement_number_and_dates_crosschecked",
        "note": "已核对官方公告文号及日期；具体附件URL待补录。",
    }],
    "839832": [{
        "notice_number": "2019-2576",
        "decision_date": "2019-10-16",
        "conditional_termination_date": "2019-10-28",
        "source_url": "https://www.neeq.com.cn/",
        "evidence_status": "official_decision_notice_number_and_dates_crosschecked",
        "note": "已核对官方决定文号及日期；具体附件URL待补录。",
    }],
    "836554": [{
        "notice_number": "2019-1708",
        "decision_date": "2019-12-09",
        "conditional_termination_date": "2019-12-19",
        "source_url": "https://www.neeq.com.cn/",
        "evidence_status": "official_announcement_number_and_dates_crosschecked",
        "note": "已核对官方公告文号及日期；具体附件URL待补录。",
    }],
    "430136": [{
        "notice_number": "2020-697",
        "decision_date": "2020-09-07",
        "conditional_termination_date": "2020-09-17",
        "source_url": "https://www.neeq.com.cn/disclosure/2020/2020-09-09/1599636923_011253.pdf",
        "evidence_status": "official_notice_crosschecked",
        "note": "独立后续公告；接口结果日期与公告所载条件终止日期一致。",
    }],
    "430134": [{
        "notice_number": "2020-110",
        "decision_date": "2020-02-13",
        "conditional_termination_date": "2020-02-25",
        "source_url": "https://www.neeq.com.cn/uploads/1/file/public/202002/20200217153226_t75f1e2e80.pdf",
        "evidence_status": "official_decision_and_sponsor_notice_crosschecked",
        "note": "正式决定及主办券商公告线索已核对；接口日期2020-05-13晚于条件日期，后续实际终止日期仍待核验。",
    }],
    "833292": [{
        "notice_number": None,
        "decision_date": "2020-02-17",
        "conditional_termination_date": "2020-02-25",
        "source_url": None,
        "evidence_status": "third_party_notice_index_lead_not_officially_verified",
        "note": "第三方公告索引线索；不作为正式决定证据，需取得官方公告原文后升级。",
    }],
    "832115": [{
        "notice_number": "2021-926",
        "decision_date": "2021-09-07",
        "conditional_termination_date": None,
        "source_url": "https://www.neeq.cc/disclosure/2021/2021-09-07/2021-926.pdf",
        "evidence_status": "official_decision_notice_crosschecked",
        "note": "官方决定日期已核对；发行人公告所述最终摘牌日期2021-10-21另列为待核验结果线索，不当作条件终止日期。",
        "reported_final_delisting_date": "2021-10-21",
        "reported_final_date_source_status": "issuer_notice_copy_hosted_by_third_party_pending_official_copy",
    }],
}

SUPPLEMENTAL_LEADS = {
    "833028": [{
        "lead_type": "media_report",
        "date": "2021-10-21",
        "source_url": None,
        "status": "unverified_lead",
        "note": "媒体报道提及最终摘牌日期；尚未取得官方决定及最终摘牌公告。",
    }],
    "835479": [{
        "lead_type": "historical_background_batch_membership",
        "date": None,
        "source_url": "https://www.neeq.com.cn/",
        "status": "background_only",
        "note": "曾出现在2018年公告背景名单；该身份不等于正式决定。",
    }],
}


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def build_report(candidate_manifest: dict[str, Any], batch_report: dict[str, Any]) -> dict[str, Any]:
    """构建交叉核验报告；不写数据库，不把候选日期提升为法律事实。"""
    manifest = candidate_manifest.get("manifest", candidate_manifest)
    candidates = manifest.get("candidates")
    memberships = batch_report.get("batch_memberships")
    coverage = batch_report.get("candidate_coverage")
    if not isinstance(candidates, list):
        raise ValueError("candidate manifest missing manifest.candidates list")
    if not isinstance(memberships, list):
        raise ValueError("batch report missing batch_memberships list")
    if not isinstance(coverage, list):
        raise ValueError("batch report missing candidate_coverage list")

    by_code_memberships: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in memberships:
        code = str(item.get("security_code") or "").strip()
        if code:
            by_code_memberships[code].append(item)
    by_code_coverage = {
        str(item.get("security_code") or "").strip(): item
        for item in coverage if item.get("security_code")
    }

    selected = []
    for item in candidates:
        raw = item.get("raw_record") or {}
        if raw.get("dataType") != 0:
            continue
        code = str(item.get("company_code") or raw.get("companyCode") or "").strip()
        linked = by_code_memberships.get(code, [])
        cov = by_code_coverage.get(code)
        linked_copy = [{
            "membership_type": x.get("membership_type"),
            "batch_notice_number": x.get("batch_notice_number"),
            "batch_announcement_date": x.get("batch_announcement_date"),
            "formal_decision_date": x.get("formal_decision_date"),
            "conditional_termination_date": x.get("conditional_termination_date"),
            "stated_termination_date": x.get("stated_termination_date"),
            "source_attachment_url": x.get("source_attachment_url"),
            "source_attachment_id": x.get("source_attachment_id"),
            "candidate_name_match": x.get("candidate_name_match"),
            "issues": x.get("issues", []),
            "review_status": x.get("review_status"),
            "confirmed_legal_fact": x.get("confirmed_legal_fact", False),
            "formal_lifecycle_write_allowed": x.get("formal_lifecycle_write_allowed", False),
        } for x in linked]
        if not linked:
            relation_status = "no_parsed_batch_membership_found_not_proof_of_no_notice"
        elif any(x.get("membership_type") == "formal_decision_company" for x in linked):
            relation_status = "formal_decision_membership_found"
        else:
            relation_status = "background_membership_only_in_parsed_batches"

        selected.append({
            "record_id": str(item.get("record_id") or raw.get("id") or ""),
            "security_code": code,
            "company_name": item.get("company_name") or raw.get("companyName"),
            "data_type": raw.get("dataType"),
            "interface_result_date": item.get("delisting_date") or item.get("delistingDateISO"),
            "interface_reason": raw.get("delistingReason"),
            "interface_create_time_epoch_ms": (raw.get("createTime") or {}).get("time"),
            "source_attachment_url": item.get("attachment_url") or raw.get("delistingNotice"),
            "raw_record_sha256": item.get("raw_record_sha256"),
            "batch_relation_status": relation_status,
            "batch_memberships": linked_copy,
            "candidate_coverage": {
                "coverage_status": (cov or {}).get("coverage_status"),
                "batch_notice_numbers": (cov or {}).get("batch_notice_numbers", []),
                "batch_membership_types": (cov or {}).get("batch_membership_types", []),
                "has_formal_decision_membership": (cov or {}).get("has_formal_decision_membership", False),
                "has_background_membership": (cov or {}).get("has_background_membership", False),
                "parsed_batch_membership_found": (cov or {}).get("parsed_batch_membership_found", False),
                "limitation": "仅覆盖当前已解析附件；未匹配不代表历史上不存在公告。",
            },
            "supplemental_independent_decisions": SUPPLEMENTAL_DECISIONS.get(code, []),
            "supplemental_leads": SUPPLEMENTAL_LEADS.get(code, []),
            "review_status": "pending_manual_review",
            "confirmed_legal_fact": False,
            "formal_lifecycle_write_allowed": False,
        })

    type_counts = Counter(
        membership_type for row in selected
        for membership_type in (m.get("membership_type") for m in row["batch_memberships"])
    )
    dual_identity = [
        row["security_code"] for row in selected
        if row["candidate_coverage"]["has_formal_decision_membership"]
        and row["candidate_coverage"]["has_background_membership"]
    ]
    return {
        "schema_version": 1,
        "report_version": "neeq_datatype0_notice_crosscheck_v2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_reports": {
            "candidate_manifest_version": manifest.get("source", "neeq_official_termination_result_list"),
            "batch_reconciliation_version": batch_report.get("audit_version"),
        },
        "summary": {
            "data_type0_candidate_count": len(selected),
            "batch_membership_link_count": sum(len(row["batch_memberships"]) for row in selected),
            "membership_type_counts": dict(sorted(type_counts.items())),
            "formal_decision_membership_candidate_count": sum(row["candidate_coverage"]["has_formal_decision_membership"] for row in selected),
            "background_membership_candidate_count": sum(row["candidate_coverage"]["has_background_membership"] for row in selected),
            "dual_background_and_formal_membership_codes": sorted(dual_identity),
            "no_parsed_batch_membership_candidate_count": sum(not row["batch_memberships"] for row in selected),
            "supplemental_independent_decision_count": sum(len(row["supplemental_independent_decisions"]) for row in selected),
            "supplemental_unverified_lead_count": sum(len(row["supplemental_leads"]) for row in selected),
        },
        "candidates": selected,
        "safety": {
            "read_only": True,
            "database_writes": 0,
            "formal_lifecycle_writes": 0,
            "confirmed_legal_facts_created": 0,
            "manual_review_required": True,
            "data_type_semantics_inferred": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-manifest", type=Path, default=Path("output/market/neeq_termination_candidate_manifest_v2.json"))
    parser.add_argument("--batch-report", type=Path, default=Path("output/market/neeq_termination_batch_reconciliation_v2.json"))
    parser.add_argument("--output", type=Path, default=Path("output/market/neeq_datatype0_notice_crosscheck_v2.json"))
    args = parser.parse_args()
    report = build_report(_load_json(args.candidate_manifest), _load_json(args.batch_report))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"report_path={args.output}")
    print("safety=read_only; database_writes=0; formal_lifecycle_writes=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
