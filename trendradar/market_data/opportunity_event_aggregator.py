"""Conservative aggregation of persisted event-to-industry evidence.

This module deduplicates evidence rows at the event/industry level while keeping
source observations and review state visible. It deliberately does not infer
event strength, industry relevance, or causal direction from keyword matches.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from .opportunity_data_provider import OpportunityDataSnapshot
from .models import MarketEvent


@dataclass(frozen=True)
class AggregatedEventEvidence:
    event: MarketEvent
    evidence_rows: tuple[object, ...]
    age_hours: float | None
    verified: bool
    direction: str


@dataclass(frozen=True)
class IndustryEventAggregate:
    industry_code: str
    events: tuple[AggregatedEventEvidence, ...]
    event_count: int
    verified_event_count: int
    evidence_row_count: int
    max_mapping_confidence: float | None
    evidence_types: tuple[str, ...]
    direction: str


def _parse_aware_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _event_age_hours(event: MarketEvent, as_of: datetime) -> float | None:
    published = _parse_aware_datetime(event.published_at)
    if published is None:
        return None
    age = (as_of - published.astimezone(as_of.tzinfo)).total_seconds() / 3600.0
    if age < 0:
        return None
    return round(age, 4)


def _verified_direction(rows: Iterable[object]) -> str:
    directions = {
        str(row.direction).strip().lower()
        for row in rows
        if bool(row.verified)
        and str(row.direction).strip().lower() in {"positive", "negative"}
    }
    return next(iter(directions)) if len(directions) == 1 else "neutral"


def aggregate_industry_event_evidence(
    snapshot: OpportunityDataSnapshot,
) -> tuple[IndustryEventAggregate, ...]:
    """Group event evidence by SW2 industry, counting each event only once.

    Evidence rows are deduplicated by their persisted identity fields. Multiple
    rows for one event and industry remain inspectable but contribute one event
    to event_count. Orphan evidence and non-SW2 targets are ignored.

    Mapping confidence is exposed only as mapping confidence. It is never
    converted into relevance_score or event_strength.
    """
    if not isinstance(snapshot, OpportunityDataSnapshot):
        raise TypeError("snapshot must be an OpportunityDataSnapshot")

    as_of = _parse_aware_datetime(snapshot.generated_at)
    if as_of is None:
        raise ValueError("snapshot.generated_at must be timezone-aware")

    events_by_id = {event.event_id: event for event in snapshot.events}
    grouped: dict[str, dict[str, list[object]]] = {}
    seen_rows: set[tuple[object, ...]] = set()

    for row in snapshot.mapping_evidence:
        target_code = str(row.target_code).strip().upper()
        event_id = str(row.event_id).strip()
        if not target_code.startswith("SW2:") or event_id not in events_by_id:
            continue
        identity = (
            event_id, target_code, str(row.mapping_type), str(row.evidence_type),
            str(row.related_sector_code), str(row.evidence), str(row.source),
            str(row.source_url), float(row.confidence), str(row.direction),
            bool(row.verified),
        )
        if identity in seen_rows:
            continue
        seen_rows.add(identity)
        grouped.setdefault(target_code, {}).setdefault(event_id, []).append(row)

    results: list[IndustryEventAggregate] = []
    for industry_code, event_rows in sorted(grouped.items()):
        aggregated_events: list[AggregatedEventEvidence] = []
        all_rows: list[object] = []
        for event_id, rows in event_rows.items():
            event = events_by_id[event_id]
            ordered_rows = tuple(sorted(
                rows,
                key=lambda row: (
                    str(row.evidence_type), str(row.mapping_type),
                    str(row.source), str(row.source_url), str(row.evidence),
                ),
            ))
            all_rows.extend(ordered_rows)
            aggregated_events.append(AggregatedEventEvidence(
                event=event,
                evidence_rows=ordered_rows,
                age_hours=_event_age_hours(event, as_of),
                verified=any(bool(row.verified) for row in ordered_rows),
                direction=_verified_direction(ordered_rows),
            ))
        aggregated_events.sort(
            key=lambda item: (
                item.age_hours is None,
                item.age_hours if item.age_hours is not None else float("inf"),
                item.event.event_id,
            )
        )
        confidence_values = [
            float(row.confidence) for row in all_rows
            if 0.0 <= float(row.confidence) <= 1.0
        ]
        evidence_types = tuple(sorted({
            str(row.evidence_type) for row in all_rows
        }))
        directions = {item.direction for item in aggregated_events
                      if item.direction in {"positive", "negative"}}
        aggregate_direction = (
            next(iter(directions)) if len(directions) == 1 else "neutral"
        )
        results.append(IndustryEventAggregate(
            industry_code=industry_code,
            events=tuple(aggregated_events),
            event_count=len(aggregated_events),
            verified_event_count=sum(item.verified for item in aggregated_events),
            evidence_row_count=len(all_rows),
            max_mapping_confidence=max(confidence_values) if confidence_values else None,
            evidence_types=evidence_types,
            direction=aggregate_direction,
        ))
    return tuple(results)
