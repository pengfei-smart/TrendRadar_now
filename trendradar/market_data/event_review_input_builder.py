"""Build conservative AI-review inputs from persisted market events.

An article publication timestamp is never promoted to an event-occurrence time.
Event-to-security mapping rows are included only as contextual evidence, not
as primary factual sources or proof of market impact.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

from .event_classifier import classify_market_event
from .event_source_registry import assess_event_source
from .event_review_models import ReviewEvidence
from .models import MarketEvent
from .storage import MarketStorage


@dataclass(frozen=True)
class EventReviewInput:
    """Prepared event payload and traceable evidence for one review run."""

    event_id: str
    event_payload: dict
    evidence: tuple[ReviewEvidence, ...]


def _stable_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def build_event_review_input(
    *,
    storage: MarketStorage,
    event_id: str,
    max_mapping_evidence: int = 100,
    max_excerpt_chars: int = 4000,
) -> EventReviewInput:
    """Load one event and build bounded, provenance-aware review inputs.

    Registered official feeds can be labelled primary; registered aggregators
    secondary; unknown feeds remain context. Mapping rows are always context.
    No event occurrence time, direction, importance or surprise baseline is
    inferred by this adapter.
    """
    if not isinstance(storage, MarketStorage):
        raise TypeError("storage must be a MarketStorage instance")
    if not isinstance(event_id, str) or not event_id.strip():
        raise ValueError("event_id must be a non-empty string")
    if isinstance(max_mapping_evidence, bool) or not isinstance(max_mapping_evidence, int) or not 0 <= max_mapping_evidence <= 1000:
        raise ValueError("max_mapping_evidence must be an integer between 0 and 1000")
    if isinstance(max_excerpt_chars, bool) or not isinstance(max_excerpt_chars, int) or not 100 <= max_excerpt_chars <= 20000:
        raise ValueError("max_excerpt_chars must be an integer between 100 and 20000")

    normalized_id = event_id.strip()
    events = storage.load_market_events(limit=10000)
    event = next((item for item in events if item.event_id == normalized_id), None)
    if event is None:
        raise LookupError(f"market event not found: {normalized_id}")

    source_assessment = assess_event_source(event.source)
    classification = classify_market_event(event)
    if source_assessment.is_primary_source:
        event_evidence_kind = "primary"
    elif source_assessment.assessment_status == "registered":
        event_evidence_kind = "secondary"
    else:
        event_evidence_kind = "context"

    source_excerpt = "标题：" + event.title
    if event.summary.strip():
        source_excerpt += "\\n摘要：" + event.summary.strip()
    source_excerpt = source_excerpt[:max_excerpt_chars]

    evidence_items: list[ReviewEvidence] = [
        ReviewEvidence(
            evidence_id="event-source-" + _stable_id(normalized_id + "|" + event.source + "|" + event.url),
            event_id=normalized_id,
            source=event.source or "unknown",
            source_url=event.url,
            excerpt=source_excerpt,
            evidence_kind=event_evidence_kind,
            published_at=event.published_at,
            captured_at="",
            supports_dimensions=(),
        )
    ]

    mappings = storage.load_event_mapping_evidence(
        event_id=normalized_id,
        min_confidence=0.0,
        limit=max(1, max_mapping_evidence),
    ) if max_mapping_evidence else []
    for item in mappings:
        evidence_text = {
            "kind": "event_security_mapping_context",
            "mapping_type": item["mapping_type"],
            "mapping_evidence_type": item["evidence_type"],
            "confidence": item["confidence"],
            "verified": bool(item["verified"]),
            "direction": item["direction"],
            "target_code": item["target_code"],
            "evidence": item["evidence"],
            "related_sector_code": item["related_sector_code"],
            "relation_evidence": item["relation_evidence"],
            "warning": "该记录是事件与证券的映射上下文，不是独立事实来源，也不证明证券受益或受损。",
        }
        evidence_items.append(
            ReviewEvidence(
                evidence_id="event-mapping-" + _stable_id(
                    normalized_id + "|" + item["target_code"] + "|" + item["mapping_type"]
                ),
                event_id=normalized_id,
                source=item["source"] or "event_mapping",
                source_url=item["source_url"],
                excerpt=json.dumps(
                    evidence_text, ensure_ascii=False, sort_keys=True,
                    separators=(",", ":"),
                )[:max_excerpt_chars],
                evidence_kind="context",
                published_at="",
                captured_at="",
                supports_dimensions=(),
            )
        )

    event_payload = {
        "event_id": event.event_id,
        "source": event.source,
        "source_url": event.url,
        "title": event.title,
        "summary": event.summary,
        "published_at": event.published_at,
        "event_type": event.event_type,
        "stored_direction": event.direction,
        "region": event.region,
        "source_assessment": {
            "source_class": source_assessment.source_class,
            "source_role": source_assessment.source_role,
            "assessment_status": source_assessment.assessment_status,
            "is_primary_source": source_assessment.is_primary_source,
            "rationale": source_assessment.rationale,
        },
        "classification": {
            "category": classification.event_category,
            "confidence": classification.classification_confidence,
            "status": classification.classification_status,
            "matched_terms": list(classification.matched_terms),
            "candidate_categories": list(classification.candidate_categories),
        },
        "input_warnings": [
            "published_at仅表示文章发布时间，不代表事件发生时间。",
            "未提供独立的事件发生时间；不得据此计算surprise事前基准。",
            "证券映射记录仅作为上下文，不代表已验证的因果传导。",
        ],
    }
    return EventReviewInput(
        event_id=normalized_id,
        event_payload=event_payload,
        evidence=tuple(evidence_items),
    )
