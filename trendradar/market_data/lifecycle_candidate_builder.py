# 本文件将官方公告抓取结果与解析证据转换为待人工审核的生命周期候选记录。
"""Build review-only lifecycle evidence candidate rows from fetched documents."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Iterable
from urllib.parse import urljoin, urlsplit

from trendradar.market_data.lifecycle_evidence_fetcher import FetchedDocument
from trendradar.market_data.lifecycle_evidence_parser import ParsedLifecycleEvidence
from trendradar.market_data.lifecycle_source_registry import get_source, is_allowed_url


EXCHANGE_BY_SOURCE = {"sse": "SSE", "szse": "SZSE", "neeq": "NEEQ"}
CORPORATE_ACTION_TYPES = {
    "cash_tender_offer": "cash_tender_offer",
    "reorganization": "reorganization",
    "merger": "merger",
}
RELATIONSHIP_TYPES = {
    "merger_into", "spin_off", "restructuring_successor", "other",
}


def _candidate_id(
    source_id: str,
    source_url: str,
    candidate_kind: str,
    security_code: str,
    related_security_code: str,
    event_type: str,
    action_type: str,
    relationship_type: str,
) -> str:
    identity = "|".join((
        source_id, source_url, candidate_kind,
        security_code, related_security_code,
        event_type, action_type, relationship_type,
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
    announcement_metadata: dict | None = None,
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
    issuer_identity_hint = None
    if announcement_metadata is not None:
        if document.source_id != "neeq" or not isinstance(announcement_metadata, dict):
            raise ValueError("announcement_metadata 仅允许用于 NEEQ 公告，且必须是 dict")
        record = announcement_metadata.get("record")
        if not isinstance(record, dict):
            raise ValueError("announcement_metadata.record 必须是公告列表原始记录")
        company_code = str(record.get("companyCd") or "").strip()
        company_name = str(record.get("companyName") or "").strip()
        attachment_path = str(record.get("destFilePath") or "").strip()
        metadata_source_url = str(announcement_metadata.get("source_url") or "").strip()
        metadata_hash = str(announcement_metadata.get("response_sha256") or "").lower().strip()
        metadata_retrieved_at = str(announcement_metadata.get("retrieved_at") or "").strip()
        if not (len(company_code) == 6 and company_code.isdigit() and company_name and attachment_path):
            raise ValueError("NEEQ 公告元数据缺少有效 companyCd、companyName 或 destFilePath")
        attachment_url = urljoin("https://www.neeq.com.cn/", attachment_path)
        if (
            not is_allowed_url("neeq", attachment_url)
            or urlsplit(attachment_url).path != urlsplit(document.final_url).path
        ):
            raise ValueError("NEEQ 公告元数据附件路径与当前 PDF 不一致")
        if parsed.security_codes and company_code not in parsed.security_codes:
            raise ValueError("NEEQ 公告元数据 companyCd 与正文证券代码不一致")
        if not is_allowed_url("neeq", metadata_source_url):
            raise ValueError("NEEQ 公告列表元数据来源 URL 不在官方白名单内")
        if len(metadata_hash) != 64 or any(ch not in "0123456789abcdef" for ch in metadata_hash):
            raise ValueError("NEEQ 公告列表响应必须提供有效 SHA-256")
        if not metadata_retrieved_at:
            raise ValueError("NEEQ 公告列表元数据必须提供采集时间")
        issuer_identity_hint = {
            "exchange": "NEEQ",
            "issuer_code": company_code,
            "issuer_name": company_name,
            "identity_resolution_status": "metadata_linked_pending_review",
            "match_method": "exact_attachment_path",
            "announcement_title": str(record.get("disclosureTitle") or ""),
            "publish_date": str(record.get("publishDate") or ""),
            "attachment_url": attachment_url,
            "metadata_source_url": metadata_source_url,
            "metadata_response_sha256": metadata_hash,
            "metadata_retrieved_at": metadata_retrieved_at,
            "review_required": True,
        }
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
        if issuer_identity_hint:
            payload["issuer_identity_hint"] = issuer_identity_hint
        candidates.append({
            "candidate_id": _candidate_id(
                document.source_id, document.final_url,
                candidate_kind, security_code, related_security_code,
                event_type, action_type,
                relationship_type,
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

    # 公告正文未出现六位代码时，仍保留事件线索；证券身份留待后续人工匹配。
    if not parsed.security_codes:
        for event_type in parsed.event_types:
            if event_type in CORPORATE_ACTION_TYPES:
                append_candidate(
                    candidate_kind="corporate_action",
                    security_code="",
                    event_type=event_type,
                    action_type=CORPORATE_ACTION_TYPES[event_type],
                    extra_payload={
                        "identity_resolution_status": (
                            "metadata_linked_pending_review" if issuer_identity_hint else "unresolved"
                        ),
                        "identity_resolution_reason": (
                            "official_announcement_metadata_linked_by_attachment_path"
                            if issuer_identity_hint else "security_code_not_found_in_document"
                        ),
                    },
                )
            else:
                append_candidate(
                    candidate_kind="event",
                    security_code="",
                    event_type=event_type,
                    extra_payload={
                        "identity_resolution_status": "unresolved",
                        "identity_resolution_reason": "security_code_not_found_in_document",
                    },
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
