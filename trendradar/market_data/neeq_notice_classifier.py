# 本文件仅按NEEQ公告标题识别公告类型线索，不判断公告是否已产生正式终止挂牌法律效力。
"""Conservative title-based classification for NEEQ lifecycle notices."""

from __future__ import annotations


def classify_neeq_notice(title: str) -> dict:
    """Classify a title as a review hint; never assert a confirmed lifecycle fact."""
    if not isinstance(title, str):
        raise TypeError("公告标题必须是字符串")
    normalized = " ".join(title.split())
    if not normalized:
        category = "unclassified"
        basis = "empty_title"
    elif any(term in normalized for term in ("风险提示", "风险提示性公告", "风险提示公告")):
        category = "delisting_arrangement_risk_notice"
        basis = "title_contains_risk_warning"
    elif any(term in normalized for term in ("终止挂牌的公告", "终止挂牌公告")):
        category = "formal_termination_notice_candidate"
        basis = "title_contains_termination_announcement"
    elif any(term in normalized for term in ("终止挂牌决定", "收到终止挂牌决定", "终止上市决定")):
        category = "delisting_decision_notice_candidate"
        basis = "title_contains_termination_decision"
    elif any(term in normalized for term in ("摘牌整理期", "退市整理期", "终止挂牌", "终止上市", "摘牌")):
        category = "other_lifecycle_notice"
        basis = "title_contains_lifecycle_terms"
    else:
        category = "unclassified"
        basis = "no_lifecycle_title_marker"

    issuer_role = (
        "broker_or_sponsor_notice"
        if "[券商公告]" in normalized or "[主办券商]" in normalized
        else "issuer_or_other_notice"
    )
    return {
        "category": category,
        "basis": basis,
        "issuer_role_hint": issuer_role,
        "review_required": True,
        "confirmed_legal_fact": False,
    }
