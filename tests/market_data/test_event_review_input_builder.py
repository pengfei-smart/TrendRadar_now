"""Tests for conservative event-review input construction."""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from trendradar.market_data.event_review_input_builder import build_event_review_input
from trendradar.market_data.models import MarketEvent, MappingEvidence
from trendradar.market_data.storage import MarketStorage


class EventReviewInputBuilderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = MarketStorage(str(Path(self.tmp.name) / "market.db"))

    def tearDown(self):
        self.tmp.cleanup()

    def _save_event(self, source="fed_monetary"):
        self.storage.save_events([
            MarketEvent(
                event_id="event-input-1", source=source,
                title="Federal Reserve announces policy decision",
                published_at="2026-11-18T08:30:00-05:00",
                event_type="monetary_policy", direction="neutral",
                region="US", url="https://example.test/event",
                summary="Official statement summary",
            )
        ])

    def test_registered_primary_source_is_labelled_primary_without_inventing_event_time(self):
        self._save_event()
        result = build_event_review_input(
            storage=self.storage, event_id="event-input-1"
        )

        self.assertEqual(result.event_id, "event-input-1")
        self.assertEqual(len(result.evidence), 1)
        self.assertEqual(result.evidence[0].evidence_kind, "primary")
        self.assertEqual(result.evidence[0].published_at, "2026-11-18T08:30:00-05:00")
        self.assertNotIn("event_time", result.event_payload)
        self.assertEqual(result.event_payload["source_assessment"]["assessment_status"], "registered")
        self.assertIn("仅表示文章发布时间", result.event_payload["input_warnings"][0])

    def test_unknown_source_is_context_and_never_promoted_to_primary(self):
        self._save_event(source="unregistered_feed")
        result = build_event_review_input(
            storage=self.storage, event_id="event-input-1"
        )

        self.assertEqual(result.evidence[0].evidence_kind, "context")
        self.assertFalse(result.event_payload["source_assessment"]["is_primary_source"])
        self.assertEqual(result.event_payload["source_assessment"]["assessment_status"], "unassessed")

    def test_mapping_rows_are_context_not_primary_fact_evidence(self):
        self._save_event()
        self.storage.save_mapping_evidence(
            "event-input-1",
            [
                MappingEvidence(
                    target_code="600000", mapping_type="keyword",
                    confidence=0.91, evidence="关键词命中：银行",
                    source="sector_mapper", source_url="",
                    direction="neutral", verified=True,
                    evidence_type="direct",
                )
            ],
        )
        result = build_event_review_input(
            storage=self.storage, event_id="event-input-1"
        )

        self.assertEqual(len(result.evidence), 2)
        mapping = result.evidence[1]
        self.assertEqual(mapping.evidence_kind, "context")
        self.assertEqual(mapping.supports_dimensions, ())
        self.assertIn("不证明证券受益或受损", mapping.excerpt)
        self.assertEqual(mapping.published_at, "")

    def test_missing_event_and_invalid_limits_are_rejected(self):
        with self.assertRaises(LookupError):
            build_event_review_input(storage=self.storage, event_id="missing")
        self._save_event()
        with self.assertRaises(ValueError):
            build_event_review_input(
                storage=self.storage, event_id="event-input-1",
                max_mapping_evidence=1001,
            )


if __name__ == "__main__":
    unittest.main()
