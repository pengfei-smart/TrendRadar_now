"""Explicit registry for event-source provenance.

Source trust is metadata about where an event came from. It is not event
importance, event strength, market direction, or evidence that an event is true.
Unknown sources remain unassessed rather than receiving an inferred score.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EventSourceAssessment:
    source: str
    source_class: str
    source_role: str
    assessment_status: str
    is_primary_source: bool
    rationale: str


# Registry keys are stable source identifiers, not display names or URLs.
_SOURCE_REGISTRY: dict[str, EventSourceAssessment] = {
    "fed_monetary": EventSourceAssessment(
        source="fed_monetary",
        source_class="official_primary",
        source_role="central_bank_policy",
        assessment_status="registered",
        is_primary_source=True,
        rationale="Federal Reserve official monetary-policy feed",
    ),
    "federal_reserve": EventSourceAssessment(
        source="federal_reserve",
        source_class="official_primary",
        source_role="central_bank_publication",
        assessment_status="registered",
        is_primary_source=True,
        rationale="Federal Reserve official publication feed",
    ),
    "bls_latest": EventSourceAssessment(
        source="bls_latest",
        source_class="official_primary",
        source_role="government_statistics",
        assessment_status="registered",
        is_primary_source=True,
        rationale="US Bureau of Labor Statistics official feed",
    ),
    "bls_cpi": EventSourceAssessment(
        source="bls_cpi",
        source_class="official_primary",
        source_role="inflation_statistics",
        assessment_status="registered",
        is_primary_source=True,
        rationale="US Bureau of Labor Statistics official CPI feed",
    ),
    "bls_employment": EventSourceAssessment(
        source="bls_employment",
        source_class="official_primary",
        source_role="employment_statistics",
        assessment_status="registered",
        is_primary_source=True,
        rationale="US Bureau of Labor Statistics official employment feed",
    ),
    "eia_energy": EventSourceAssessment(
        source="eia_energy",
        source_class="official_primary",
        source_role="energy_statistics",
        assessment_status="registered",
        is_primary_source=True,
        rationale="US Energy Information Administration official feed",
    ),
    "nasdaq_news": EventSourceAssessment(
        source="nasdaq_news",
        source_class="news_aggregator",
        source_role="secondary_news",
        assessment_status="registered",
        is_primary_source=False,
        rationale="Nasdaq news RSS is treated as a secondary news feed, not an original issuer filing",
    ),
}


def assess_event_source(source: str) -> EventSourceAssessment:
    """Return registered provenance metadata; never infer trust for unknown IDs."""
    normalized = str(source or "").strip().casefold()
    if not normalized:
        return EventSourceAssessment(
            source="",
            source_class="unknown",
            source_role="unknown",
            assessment_status="unassessed",
            is_primary_source=False,
            rationale="Source identifier is missing",
        )
    registered = _SOURCE_REGISTRY.get(normalized)
    if registered is not None:
        return registered
    return EventSourceAssessment(
        source=normalized,
        source_class="unknown",
        source_role="unknown",
        assessment_status="unassessed",
        is_primary_source=False,
        rationale="Source identifier is not present in the reviewed source registry",
    )
