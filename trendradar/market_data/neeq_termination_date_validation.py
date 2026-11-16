# 本文件提供NEEQ终止挂牌日期语义与证据映射的纯函数校验，不执行数据库写入。
"""Pure validation rules for NEEQ termination procedure dates and evidence mappings."""

from __future__ import annotations

from datetime import date
from typing import Any, Mapping


_PROCEDURE_SEMANTICS = {
    "decision_issued": {"decision_issued", "supplementary_decision"},
    "decision_received": {"decision_received"},
    "conditional_termination": {"conditional_termination"},
    "announcement": {"final_termination", "other"},
    "last_trading_day": {"other"},
    "actual_termination": {"final_termination"},
    "transfer_start": {"transfer_market_entry"},
    "transfer_exit": {"transfer_exit"},
    "unknown": {"other"},
}

_ACTUAL_TERMINATION_EVIDENCE_KINDS = {
    "final_termination_notice",
    "formal_decision",
    "supplementary_decision",
}
_TRUSTED_ACTUAL_AUTHORITIES = {
    "neeq_official", "issuer_official", "government_or_registry",
}


def _issue(code: str, message: str, severity: str = "error") -> dict[str, str]:
    return {"code": code, "message": message, "severity": severity}


def _parse_iso_date(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        parsed = date.fromisoformat(value.strip())
    except ValueError:
        return False
    return len(value.strip()) == 10 and parsed.isoformat() == value.strip()


def validate_neeq_termination_procedure(
    procedure: Mapping[str, Any],
    evidence: Mapping[str, Any] | None = None,
    *,
    relation_type: str = "supports_procedure",
) -> dict[str, Any]:
    """Validate one procedure node and optional linked evidence without mutating input."""
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    procedure_type = str(procedure.get("procedure_type") or "").strip()
    semantics = str(procedure.get("date_semantics") or "unknown").strip()
    event_date = procedure.get("event_date")
    date_status = str(procedure.get("date_status") or "unknown").strip()

    known_procedure_types = {
        "decision_issued", "decision_received", "review_filed", "review_accepted",
        "review_rejected", "supplementary_decision", "conditional_termination",
        "final_termination", "transfer_market_entry", "transfer_exit", "other",
    }
    if procedure_type not in known_procedure_types:
        errors.append(_issue("unsupported_procedure_type", f"不支持的程序类型: {procedure_type}"))

    allowed_types = _PROCEDURE_SEMANTICS.get(semantics)
    if allowed_types is None:
        errors.append(_issue("unsupported_date_semantics", f"不支持的日期语义: {semantics}"))
    elif procedure_type not in allowed_types:
        errors.append(_issue(
            "procedure_semantics_mismatch",
            f"procedure_type={procedure_type} 与 date_semantics={semantics} 不匹配",
        ))

    if semantics != "unknown" and not event_date:
        errors.append(_issue("event_date_required", "已知日期语义必须提供event_date"))
    elif event_date and not _parse_iso_date(event_date):
        errors.append(_issue("invalid_event_date", "event_date必须是YYYY-MM-DD格式的有效日期"))
    if date_status not in {"confirmed", "approximate", "pending", "unknown"}:
        errors.append(_issue("invalid_date_status", f"非法date_status: {date_status}"))

    if semantics == "actual_termination":
        if date_status != "confirmed":
            errors.append(_issue(
                "actual_termination_not_confirmed",
                "actual_termination日期必须先达到confirmed状态",
            ))
        if not isinstance(evidence, Mapping):
            errors.append(_issue(
                "actual_termination_evidence_required",
                "确认实际终止日期必须关联并核验直接证据",
            ))
        else:
            metadata = evidence.get("metadata")
            if not isinstance(metadata, Mapping):
                metadata = {}
            if relation_type != "supports_procedure":
                errors.append(_issue(
                    "actual_termination_link_not_supporting",
                    "实际终止日期必须由supports_procedure关系的证据支持",
                ))
            if evidence.get("evidence_kind") not in _ACTUAL_TERMINATION_EVIDENCE_KINDS:
                errors.append(_issue(
                    "actual_termination_evidence_kind_invalid",
                    "实际终止日期证据必须是最终终止公告或明确记载实际生效日的正式决定/补充决定",
                ))
            if evidence.get("source_authority") not in _TRUSTED_ACTUAL_AUTHORITIES:
                errors.append(_issue(
                    "actual_termination_authority_untrusted",
                    "实际终止日期不能仅由主办券商转述或第三方线索确认",
                ))
            if evidence.get("evidence_status") != "crosschecked":
                errors.append(_issue(
                    "actual_termination_evidence_not_crosschecked",
                    "实际终止日期证据必须达到crosschecked状态",
                ))
            if metadata.get("confirmed_legal_fact") is not True:
                errors.append(_issue(
                    "actual_termination_fact_not_confirmed",
                    "证据metadata必须明确标记confirmed_legal_fact=true",
                ))
            if metadata.get("date_semantics") != "actual_termination":
                errors.append(_issue(
                    "actual_termination_semantics_not_explicit",
                    "证据metadata必须明确标记date_semantics=actual_termination",
                ))
            if not event_date or not _parse_iso_date(event_date):
                errors.append(_issue(
                    "actual_termination_date_missing",
                    "实际终止日期必须提供有效event_date",
                ))
    elif semantics == "conditional_termination":
        warnings.append(_issue(
            "conditional_date_not_actual",
            "条件终止日期仅表示条件成立时的日期，不得自动升级为实际终止日期",
            "warning",
        ))
    elif semantics == "announcement":
        warnings.append(_issue(
            "announcement_date_not_actual",
            "公告发布日期与公告正文中的实际终止生效日必须分开记录",
            "warning",
        ))
    elif semantics == "transfer_exit":
        warnings.append(_issue(
            "registration_exit_not_actual",
            "退出登记日期不得自动等同实际终止挂牌日期",
            "warning",
        ))

    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "procedure_id": str(procedure.get("procedure_id") or ""),
        "procedure_type": procedure_type,
        "date_semantics": semantics,
        "event_date": str(event_date or ""),
    }


def validate_neeq_date_mapping(
    source_semantics: str,
    target_field: str,
    *,
    procedure_validation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Reject unsafe promotion unless the complete procedure/evidence validation passed."""
    source = str(source_semantics or "").strip()
    target = str(target_field or "").strip()
    actual_targets = {"actual_termination_date", "delisting_date"}

    if target not in actual_targets:
        return {"valid": True, "errors": [], "source_semantics": source, "target_field": target}

    is_validated_actual = (
        isinstance(procedure_validation, Mapping)
        and procedure_validation.get("valid") is True
        and procedure_validation.get("date_semantics") == "actual_termination"
    )
    if source != "actual_termination" or not is_validated_actual:
        return {
            "valid": False,
            "errors": [_issue(
                "unsafe_actual_termination_mapping",
                f"禁止将{source or 'unknown'}映射到{target}；必须先由直接证据确认actual_termination",
            )],
            "source_semantics": source,
            "target_field": target,
        }
    return {
        "valid": True,
        "errors": [],
        "source_semantics": source,
        "target_field": target,
    }
