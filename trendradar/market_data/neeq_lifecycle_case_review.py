# 本模块汇总NEEQ历史证据登记册中的证券生命周期日期、证据来源和待补证缺口；只生成审核报告，不确认法律事实或写数据库。
"""Build a read-only, per-security NEEQ lifecycle evidence review report."""
from __future__ import annotations

from datetime import datetime, timezone


TARGET_SECURITY_CODES = ("834502", "834606", "834904")


def _clean(value):
    return str(value).strip() if value is not None else ""


def build_neeq_lifecycle_case_review(
    evidence_register: dict,
    *,
    security_codes=None,
    generated_at: str | None = None,
) -> dict:
    """Aggregate registered evidence by security without inferring missing lifecycle events."""
    selected = tuple(_clean(code) for code in (security_codes or TARGET_SECURITY_CODES) if _clean(code))
    selected_set = set(selected)
    dossiers_by_code = {}
    for dossier in evidence_register.get("dossiers") or []:
        code = _clean(dossier.get("security_code"))
        if code in selected_set:
            dossiers_by_code.setdefault(code, []).append(dossier)

    cases = []
    for code in selected:
        source_dossiers = dossiers_by_code.get(code, [])
        evidence_records = []
        for dossier in source_dossiers:
            for record in dossier.get("evidence_records") or []:
                evidence_records.append(record)

        observations = []
        for record in evidence_records:
            for assertion in record.get("date_assertions") or []:
                if not assertion.get("date_value") or not assertion.get("date_type"):
                    continue
                observations.append({
                    "date_value": assertion.get("date_value"),
                    "date_type": assertion.get("date_type"),
                    "date_type_category": assertion.get("date_type_category"),
                    "source_evidence_id": record.get("evidence_id"),
                    "evidence_type": record.get("evidence_type"),
                    "source_title": record.get("source_title"),
                    "source_url": record.get("source_url"),
                    "source_file": record.get("source_file"),
                    "source_sha256": record.get("source_sha256"),
                    "source_quote": assertion.get("source_quote"),
                    "page_number": assertion.get("page_number"),
                    "assertion_review_status": assertion.get("assertion_review_status"),
                    "evidence_review_status": record.get("review_status"),
                })
        observations.sort(key=lambda row: (row["date_value"], row["date_type"], row["source_evidence_id"] or ""))

        date_types = {row["date_type"] for row in observations}
        has_resumption = "transfer_resumption_date" in date_types
        has_planned_termination = "planned_termination_date" in date_types
        has_actual_result = bool({"official_result_delisting_date", "legal_termination_date"} & date_types)
        metadata_incomplete = sum(
            record.get("review_status") == "source_metadata_incomplete"
            for record in evidence_records
        )
        integrity_problem = sum(
            record.get("file_integrity_status") != "sha256_match"
            for record in evidence_records
        )
        flags = []
        if not has_resumption:
            flags.append({
                "code": "explicit_resumption_evidence_not_found",
                "severity": "evidence_gap",
                "message": "当前登记册未找到明确的恢复转让日期断言；不得根据暂停时长或下一个交易日推断。",
            })
        if has_planned_termination and not has_actual_result:
            flags.append({
                "code": "actual_termination_result_not_corroborated",
                "severity": "evidence_gap",
                "message": "已有计划终止日期，但当前登记册未找到官方结果日期或明确的实际法律生效日期证据。",
            })
        if metadata_incomplete:
            flags.append({
                "code": "source_metadata_incomplete",
                "severity": "review_required",
                "message": f"有{metadata_incomplete}条证据记录缺少完整来源元数据，需补充可核验的官方来源信息。",
            })
        if integrity_problem:
            flags.append({
                "code": "file_integrity_not_verified",
                "severity": "review_required",
                "message": f"有{integrity_problem}条证据记录的本地文件完整性未达到SHA256匹配状态。",
            })
        if not evidence_records:
            flags.append({
                "code": "no_evidence_records",
                "severity": "evidence_gap",
                "message": "当前登记册没有该证券的关联证据记录。",
            })

        cases.append({
            "security_code": code,
            "company_short_name": next(
                (dossier.get("company_short_name") for dossier in source_dossiers if dossier.get("company_short_name")),
                None,
            ),
            "notice_document_numbers": sorted({
                _clean(dossier.get("notice_document_number"))
                for dossier in source_dossiers
                if _clean(dossier.get("notice_document_number"))
            }),
            "evidence_record_count": len(evidence_records),
            "date_assertion_count": len(observations),
            "evidence_records": [
                {
                    "evidence_id": record.get("evidence_id"),
                    "evidence_type": record.get("evidence_type"),
                    "source_title": record.get("source_title"),
                    "source_url": record.get("source_url"),
                    "source_file": record.get("source_file"),
                    "source_sha256": record.get("source_sha256"),
                    "file_integrity_status": record.get("file_integrity_status"),
                    "review_status": record.get("review_status"),
                    "source_authority_status": record.get("source_authority_status"),
                }
                for record in evidence_records
            ],
            "timeline": observations,
            "coverage": {
                "explicit_transfer_resumption_evidence_found": has_resumption,
                "planned_termination_date_found": has_planned_termination,
                "actual_termination_result_evidence_found": has_actual_result,
                "source_metadata_incomplete_record_count": metadata_incomplete,
                "file_integrity_problem_count": integrity_problem,
            },
            "review_flags": flags,
            "review_status": "pending_manual_review",
            "confirmed_legal_fact": False,
            "formal_lifecycle_write_allowed": False,
        })

    return {
        "schema_version": 1,
        "report_version": "neeq_lifecycle_case_review_v1",
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "source_register_version": evidence_register.get("register_version"),
        "security_codes": list(selected),
        "summary": {
            "security_count": len(cases),
            "evidence_record_count": sum(case["evidence_record_count"] for case in cases),
            "date_assertion_count": sum(case["date_assertion_count"] for case in cases),
            "securities_with_explicit_resumption_evidence": sum(
                case["coverage"]["explicit_transfer_resumption_evidence_found"] for case in cases
            ),
            "securities_missing_explicit_resumption_evidence": sum(
                not case["coverage"]["explicit_transfer_resumption_evidence_found"] for case in cases
            ),
            "securities_with_actual_termination_result_evidence": sum(
                case["coverage"]["actual_termination_result_evidence_found"] for case in cases
            ),
        },
        "cases": cases,
        "safety": {
            "network_downloads": 0,
            "database_writes": 0,
            "formal_lifecycle_writes": 0,
            "legal_fact_confirmations": 0,
            "automatic_date_inferences": 0,
            "read_only": True,
            "manual_review_required": True,
        },
    }
