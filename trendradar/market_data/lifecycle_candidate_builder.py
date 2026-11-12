# 本文件将官方公告抓取结果与解析证据转换为待人工审核的生命周期候选记录。
"""Build review-only lifecycle evidence candidate rows from fetched documents."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Iterable

from trendradar.market_data.lifecycle_evidence_fetcher import FetchedDocument
from trendradar.market_data.lifecycle_evidence_parser import ParsedLifecycleEvidence
from trendradar.market_data.lifecycle_source_registry import get_source, is_allowed_url


EXCHANGE_BY_SOURCE = {"sse": "SSE", "szse": "SZSE"}
CORPORATE_ACTION_TYPES = {
    "cash_tender_offer": "cash_tender_offer",
    "reorganization": "reorganization",
    "merger": "merger",
}
RELATIONSHIP_TYPES = {
    "merger_into", "spin_off", "restructuring_successor", "other",
}


def _candidate_id(
    content_sha256: str,
    source_id: str,
    source_url: str,
    candidate_kind: str,
    security_code: str,
    related_security_code: str,
    event_type: str,
    action_type: str,
    relationship_type: str,
    event_date: str,
) -> str:
    identity = "|".join((
        content_sha256, source_id, source_url, candidate_kind,
        security_code, related_security_code,
        event_type, action_type, relationship_type, event_date,
    ))
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
    return f"life-{digest}"


def build_lifecycle_evidence_candidates(
    document: FetchedDocument,
    parsed: ParsedLifecycleEvidence,
    *,
    source_title: str,
    source_published_at: str = "",
    retrieved_at: str | None = None,
    relationship_hints: Iterable[dict] = (),
) -> list[dict]:
    """Build pending review rows; relationship direction must be supplied explicitly.

    The parser deliberately does not infer successor direction from code order.
    Optional relationship_hints are therefore caller-supplied evidence proposals,
    never inferred from the sorted security-code list.
    """
    if not isinstance(document, FetchedDocument):
        raise TypeError("document 必须是 FetchedDocument")
    if not isinstance(parsed, ParsedLifecycleEvidence):
        raise TypeError("parsed 必须是 ParsedLifecycleEvidence")
    if not source_title or not source_title.strip():
        raise ValueError("source_title 不能为空")

    source = get_source(document.source_id)
    if (
        document.status_code != 200
        or not is_allowed_url(document.source_id, document.requested_url)
        or not is_allowed_url(document.source_id, document.final_url)
    ):
        raise ValueError("只有成功获取且请求/最终URL均在官方白名单内的文档才能构建候选")

    actual_hash = hashlib.sha256(document.body).hexdigest()
    if actual_hash != document.content_sha256:
        raise ValueError("FetchedDocument 的 content_sha256 与原文不一致")

    retrieved = retrieved_at or datetime.now(timezone.utc).isoformat()
    exchange = EXCHANGE_BY_SOURCE.get(document.source_id, "")
    decoded_text = document.body.decode("utf-8", errors="replace")
    candidates: list[dict] = []

    def append_candidate(
        *,
        candidate_kind: str,
        security_code: str,
        related_security_code: str = "",
        event_type: str = "",
        action_type: str = "",
        relationship_type: str = "",
        event_date: str = "",
        date_status: str = "unknown",
        context: str = "",
        extra_payload: dict | None = None,
    ) -> None:
        payload = {
            "requested_url": document.requested_url,
            "final_url": document.final_url,
            "status_code": document.status_code,
            "content_type": document.content_type,
            "parsed_evidence": parsed.to_dict(),
            "relationship_direction_inferred": False,
        }
        if extra_payload:
            payload.update(extra_payload)
        candidates.append({
            "candidate_id": _candidate_id(
                document.content_sha256, document.source_id, document.final_url,
                candidate_kind, security_code, related_security_code,
                event_type, action_type,
                relationship_type, event_date,
            ),
            "candidate_kind": candidate_kind,
            "security_code": security_code,
            "related_security_code": related_security_code,
            "exchange": exchange,
            "event_type": event_type,
            "action_type": action_type,
            "relationship_type": relationship_type,
            "event_date": event_date,
            "date_status": date_status,
            "source_name": source.name,
            "source_title": source_title.strip(),
            "source_url": document.final_url,
            "source_published_at": source_published_at,
            "retrieved_at": retrieved,
            "content_sha256": document.content_sha256,
            "evidence_excerpt": context[:1200] or decoded_text[:1200],
            "payload_json": payload,
            "evidence_grade": "official",
        })

    # 解析器尚未建立事件与日期之间的句法依存关系；仅在事件和日期都唯一时关联，
    # 否则把日期证据保留在 payload 中，不冒险把公告日/最后交易日错配到事件。
    associated_date = (
        parsed.date_evidence[0]
        if len(parsed.event_types) == 1 and len(parsed.date_evidence) == 1
        else None
    )
    for code in parsed.security_codes:
        for event_type in parsed.event_types:
            if event_type in CORPORATE_ACTION_TYPES:
                append_candidate(
                    candidate_kind="corporate_action",
                    security_code=code,
                    event_type=event_type,
                    action_type=CORPORATE_ACTION_TYPES[event_type],
                    event_date=associated_date.value if associated_date else "",
                    date_status=associated_date.date_status if associated_date else "unknown",
                    context=associated_date.context if associated_date else "",
                )
            else:
                append_candidate(
                    candidate_kind="event",
                    security_code=code,
                    event_type=event_type,
                    event_date=associated_date.value if associated_date else "",
                    date_status=associated_date.date_status if associated_date else "unknown",
                    context=associated_date.context if associated_date else "",
                )

    for hint in relationship_hints:
        if not isinstance(hint, dict):
            raise TypeError("relationship_hints 的每项必须是 dict")
        security_code = str(hint.get("security_code") or "").strip()
        related_code = str(hint.get("related_security_code") or "").strip()
        relationship_type = str(hint.get("relationship_type") or "").strip()
        if security_code not in parsed.security_codes or related_code not in parsed.security_codes:
            raise ValueError("关系候选中的证券代码必须同时出现在解析证据中")
        if security_code == related_code or relationship_type not in RELATIONSHIP_TYPES:
            raise ValueError("关系候选证券代码或 relationship_type 无效")
        event_date = str(hint.get("event_date") or "").strip()
        date_status = str(hint.get("date_status") or "unknown").strip()
        if date_status not in {"confirmed", "approximate", "pending", "unknown"}:
            raise ValueError("关系候选 date_status 无效")
        append_candidate(
            candidate_kind="relationship",
            security_code=security_code,
            related_security_code=related_code,
            relationship_type=relationship_type,
            event_date=event_date,
            date_status=date_status,
            context=str(hint.get("evidence_excerpt") or ""),
            extra_payload={"relationship_hint": hint},
        )

    return candidates


def save_lifecycle_evidence_candidates(storage, candidates: Iterable[dict]) -> int:
    """Persist builder output using MarketStorage's review-only candidate API."""
    return storage.save_lifecycle_evidence_candidates(candidates)
