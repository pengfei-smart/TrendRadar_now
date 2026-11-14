# 本文件按NEEQ公告标题、正文日期语义及身份线索评估终止挂牌证据完整度，不确认或写入正式生命周期事实。
"""Conservative evidence completeness assessment for NEEQ termination notices."""

from __future__ import annotations

import json
import re


def _payload(candidate: dict) -> dict:
    value = candidate.get("payload_json") or {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return {}
    return value if isinstance(value, dict) else {}


def assess_neeq_termination_evidence(candidate: dict) -> dict:
    """Return review requirements; this function never confirms legal delisting."""
    if not isinstance(candidate, dict):
        raise TypeError("candidate 必须是 dict")
    from trendradar.market_data.neeq_notice_classifier import classify_neeq_notice

    title = str(candidate.get("source_title") or candidate.get("disclosure_title") or "")
    classification = classify_neeq_notice(title)
    payload = _payload(candidate)
    parsed = payload.get("parsed_evidence") or {}
    if not isinstance(parsed, dict):
        parsed = {}
    codes = {str(value) for value in parsed.get("security_codes", [])}
    hint = payload.get("issuer_identity_hint") or {}
    issuer_code = str(hint.get("issuer_code") or "")
    if issuer_code and issuer_code in codes:
        identity_status = "body_code_matches_official_metadata"
    elif issuer_code and hint.get("match_method") == "exact_attachment_path":
        identity_status = "official_metadata_linked_identity_pending_review"
    else:
        identity_status = "identity_unresolved"

    date_rows = parsed.get("date_evidence") or []
    delisting_dates = []
    for item in date_rows:
        if not isinstance(item, dict) or item.get("date_type") != "delisting_date":
            continue
        context = str(item.get("context") or "")
        # 双重防线：即使上游日期解析回归，也不把“最后交易日”当作摘牌日。
        if "最后交易日" in context or "最后一个交易日" in context:
            continue
        if not re.search(r"终止挂牌|终止上市", context):
            continue
        delisting_dates.append({
            "date": str(item.get("value") or ""),
            "date_status": str(item.get("date_status") or "unknown"),
            "context": context,
        })

    broker_notice = classification["issuer_role_hint"] == "broker_or_sponsor_notice"
    formal_title = classification["category"] == "formal_termination_notice_candidate"
    if broker_notice:
        outcome = "risk_or_intermediary_notice_not_final_proof"
    elif not formal_title:
        outcome = "formal_termination_notice_title_not_established"
    elif not delisting_dates:
        outcome = "explicit_termination_date_not_established"
    elif identity_status != "body_code_matches_official_metadata":
        outcome = "identity_review_required"
    else:
        outcome = "candidate_for_human_legal_review"

    return {
        "outcome": outcome,
        "notice_category": classification["category"],
        "issuer_role_hint": classification["issuer_role_hint"],
        "identity_status": identity_status,
        "body_security_codes": sorted(codes),
        "metadata_issuer_code": issuer_code,
        "delisting_date_evidence": delisting_dates,
        "review_required": True,
        "confirmed_legal_fact": False,
        "formal_lifecycle_write_allowed": False,
    }
