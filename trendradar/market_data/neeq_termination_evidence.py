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

    # 官方终止挂牌结果名录属于独立证据类型：不得误按普通公告正文解析，
    # 但其证券代码、结果日期及附表交叉匹配可形成待人工审核的官方证据。
    if payload.get("evidence_kind") == "official_termination_result_list":
        record = payload.get("result_record") or {}
        match = payload.get("document_match") or {}
        code = str(record.get("companyCode") or candidate.get("security_code") or "")
        date_value = str(record.get("delistingDateISO") or candidate.get("event_date") or "")
        code_match = bool(code and code == str(candidate.get("security_code") or ""))
        roster_match = bool(match.get("roster_code_match"))
        broker_match = bool(match.get("broker_match"))
        date_evidence = []
        if date_value:
            date_evidence.append({
                "date": date_value,
                "date_status": "pending",
                "evidence_source": "official_neeq_termination_result_list",
                "context": "NEEQ官方终止挂牌结果名录记录；日期字段尚待人工法律审核",
            })
        if not code_match:
            outcome = "identity_unresolved"
        elif not (roster_match and broker_match):
            outcome = "official_result_record_identity_crosscheck_incomplete"
        else:
            outcome = "official_result_record_and_roster_match_pending_human_review"
        return {
            "outcome": outcome,
            "notice_category": "official_termination_result_list_record",
            "issuer_role_hint": "official_exchange_result_list",
            "identity_status": "official_result_record_code_and_roster_match" if code_match and roster_match else "identity_review_required",
            "body_security_codes": [code] if code else [],
            "metadata_issuer_code": code,
            "delisting_date_evidence": date_evidence,
            "roster_code_match": roster_match,
            "broker_match": broker_match,
            "confirmed_legal_fact": False,
            "formal_lifecycle_write_allowed": False,
            "review_required": True,
        }

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
