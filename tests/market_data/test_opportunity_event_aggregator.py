import unittest

from trendradar.market_data.models import MarketEvent
from trendradar.market_data.opportunity_data_provider import (
    EventMappingObservation,
    OpportunityDataSnapshot,
)
from trendradar.market_data.opportunity_event_aggregator import (
    aggregate_industry_event_evidence,
)


def event(event_id, published_at="2026-11-18T09:00:00+08:00"):
    return MarketEvent(
        event_id=event_id,
        source="test_source",
        title=f"Event {event_id}",
        published_at=published_at,
        event_type="unknown",
        direction="neutral",
        region="CN",
    )


def evidence(
    event_id,
    target_code="SW2:801081",
    *,
    confidence=0.7,
    evidence_type="direct",
    direction="neutral",
    verified=False,
    evidence_text="matched term",
):
    return EventMappingObservation(
        event_id=event_id,
        target_code=target_code,
        mapping_type="industry_keyword",
        confidence=confidence,
        evidence=evidence_text,
        source="test_source",
        source_url="https://example.test/news",
        direction=direction,
        verified=verified,
        evidence_type=evidence_type,
        related_sector_code="",
        relation_evidence="",
    )


def snapshot(events, rows, generated_at="2026-11-18T10:00:00+08:00"):
    return OpportunityDataSnapshot(
        generated_at=generated_at,
        window_start="2026-11-16T10:00:00+08:00",
        window_end=generated_at,
        events=tuple(events),
        mapping_evidence=tuple(rows),
        eligible_securities=(),
        quotes=(),
        security_sector_codes=(),
        quote_freshness=(),
    )


class OpportunityEventAggregatorTests(unittest.TestCase):
    def test_multiple_evidence_rows_count_one_event(self):
        result = aggregate_industry_event_evidence(snapshot(
            [event("evt-1")],
            [
                evidence("evt-1", evidence_type="direct"),
                evidence("evt-1", evidence_type="official_name", confidence=0.56),
            ],
        ))

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].event_count, 1)
        self.assertEqual(result[0].evidence_row_count, 2)
        self.assertEqual(
            result[0].evidence_types,
            ("direct", "official_name"),
        )

    def test_distinct_events_are_counted_once_each_and_sorted_by_age(self):
        result = aggregate_industry_event_evidence(snapshot(
            [
                event("evt-old", "2026-11-18T08:00:00+08:00"),
                event("evt-new", "2026-11-18T09:30:00+08:00"),
            ],
            [
                evidence("evt-old"),
                evidence("evt-new"),
                evidence("evt-new", evidence_type="official_name"),
            ],
        ))[0]

        self.assertEqual(result.event_count, 2)
        self.assertEqual(
            tuple(item.event.event_id for item in result.events),
            ("evt-new", "evt-old"),
        )
        self.assertEqual(result.events[0].age_hours, 0.5)

    def test_unverified_direction_is_not_promoted(self):
        result = aggregate_industry_event_evidence(snapshot(
            [event("evt-1")],
            [evidence("evt-1", direction="positive", verified=False)],
        ))[0]

        self.assertEqual(result.direction, "neutral")
        self.assertEqual(result.events[0].direction, "neutral")
        self.assertEqual(result.verified_event_count, 0)

    def test_conflicting_verified_directions_remain_neutral(self):
        result = aggregate_industry_event_evidence(snapshot(
            [event("evt-1"), event("evt-2")],
            [
                evidence("evt-1", direction="positive", verified=True),
                evidence("evt-2", direction="negative", verified=True),
            ],
        ))[0]

        self.assertEqual(result.direction, "neutral")
        self.assertEqual(result.verified_event_count, 2)

    def test_orphan_and_non_sw2_evidence_are_ignored(self):
        result = aggregate_industry_event_evidence(snapshot(
            [event("evt-1")],
            [
                evidence("evt-1", target_code="SW1:801080"),
                evidence("missing-event"),
            ],
        ))

        self.assertEqual(result, ())

    def test_duplicate_evidence_rows_are_deduplicated(self):
        row = evidence("evt-1")
        result = aggregate_industry_event_evidence(
            snapshot([event("evt-1")], [row, row])
        )[0]

        self.assertEqual(result.event_count, 1)
        self.assertEqual(result.evidence_row_count, 1)

    def test_invalid_event_timestamp_keeps_event_but_has_no_age(self):
        result = aggregate_industry_event_evidence(snapshot(
            [event("evt-1", "not-a-timestamp")],
            [evidence("evt-1")],
        ))[0]

        self.assertEqual(result.event_count, 1)
        self.assertIsNone(result.events[0].age_hours)

    def test_mapping_confidence_is_exposed_but_not_relevance(self):
        result = aggregate_industry_event_evidence(snapshot(
            [event("evt-1")],
            [evidence("evt-1", confidence=0.73)],
        ))[0]

        self.assertEqual(result.max_mapping_confidence, 0.73)
        self.assertFalse(hasattr(result, "relevance_score"))
        self.assertFalse(hasattr(result, "event_strength"))

    def test_snapshot_timestamp_must_be_timezone_aware(self):
        with self.assertRaises(ValueError):
            aggregate_industry_event_evidence(snapshot(
                [event("evt-1")],
                [evidence("evt-1")],
                generated_at="2026-11-18T10:00:00",
            ))


if __name__ == "__main__":
    unittest.main()
