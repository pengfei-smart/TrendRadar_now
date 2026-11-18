"""Read-only assembly of persisted market data for the opportunity radar.

This provider deliberately returns source observations rather than scoring inputs.
It does not infer event strength, promote mapping evidence, or synthesize missing
liquidity/market-confirmation metrics.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from math import isfinite
from typing import Callable, Iterable

import pytz

from .models import MarketEvent, QuoteSnapshot, Security


_SHANGHAI = pytz.timezone("Asia/Shanghai")


@dataclass(frozen=True)
class EventMappingObservation:
    """One event-to-target mapping row with its review state preserved."""

    event_id: str
    target_code: str
    mapping_type: str
    confidence: float
    evidence: str
    source: str
    source_url: str
    direction: str
    verified: bool
    evidence_type: str
    related_sector_code: str
    relation_evidence: str


@dataclass(frozen=True)
class QuoteFreshness:
    """Freshness assessment for a provider-timestamped quote."""

    security_code: str
    source_timestamp: str
    age_seconds: float | None
    status: str


@dataclass(frozen=True)
class OpportunityDataSnapshot:
    """Immutable raw-data snapshot consumed by later opportunity transformations."""

    generated_at: str
    window_start: str
    window_end: str
    events: tuple[MarketEvent, ...]
    mapping_evidence: tuple[EventMappingObservation, ...]
    eligible_securities: tuple[Security, ...]
    quotes: tuple[QuoteSnapshot, ...]
    security_sector_codes: tuple[tuple[str, tuple[str, ...]], ...]
    quote_freshness: tuple[QuoteFreshness, ...]
    warnings: tuple[str, ...] = ()

    @property
    def fresh_quote_count(self) -> int:
        return sum(item.status == "fresh" for item in self.quote_freshness)

    @property
    def stale_quote_count(self) -> int:
        return sum(item.status == "stale" for item in self.quote_freshness)

    @property
    def quoted_security_codes(self) -> tuple[str, ...]:
        return tuple(item.code for item in self.quotes)


class OpportunityDataProvider:
    """Load a consistent-enough read snapshot from the MarketStorage interfaces.

    The provider is intentionally read-only. The supplied storage object must
    implement the corresponding MarketStorage load methods.
    """

    def __init__(
        self,
        storage,
        *,
        max_quote_age_seconds: int = 900,
        future_tolerance_seconds: int = 120,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if isinstance(max_quote_age_seconds, bool) or not isinstance(max_quote_age_seconds, int):
            raise TypeError("max_quote_age_seconds必须为整数")
        if max_quote_age_seconds <= 0:
            raise ValueError("max_quote_age_seconds必须为正整数")
        if isinstance(future_tolerance_seconds, bool) or not isinstance(future_tolerance_seconds, int):
            raise TypeError("future_tolerance_seconds必须为整数")
        if future_tolerance_seconds < 0:
            raise ValueError("future_tolerance_seconds不能为负数")
        self.storage = storage
        self.max_quote_age_seconds = int(max_quote_age_seconds)
        self.future_tolerance_seconds = int(future_tolerance_seconds)
        self.clock = clock or (lambda: datetime.now(_SHANGHAI))

    @staticmethod
    def _normalize_as_of(value: datetime) -> datetime:
        if not isinstance(value, datetime):
            raise TypeError("as_of必须是datetime")
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("as_of必须包含时区")
        return value.astimezone(_SHANGHAI)

    @staticmethod
    def _mapping_observation(row: dict) -> EventMappingObservation:
        return EventMappingObservation(
            event_id=str(row["event_id"]),
            target_code=str(row["target_code"]),
            mapping_type=str(row["mapping_type"]),
            confidence=float(row["confidence"]),
            evidence=str(row["evidence"]),
            source=str(row["source"]),
            source_url=str(row["source_url"]),
            direction=str(row["direction"]),
            verified=bool(row["verified"]),
            evidence_type=str(row["evidence_type"]),
            related_sector_code=str(row["related_sector_code"]),
            relation_evidence=str(row["relation_evidence"]),
        )

    def _assess_quote(
        self,
        quote: QuoteSnapshot,
        as_of: datetime,
    ) -> QuoteFreshness:
        if quote.timestamp_quality != "provider" or not quote.source_timestamp:
            return QuoteFreshness(
                quote.code, quote.source_timestamp, None, "invalid"
            )
        try:
            source_time = datetime.fromisoformat(
                quote.source_timestamp.replace("Z", "+00:00")
            )
            if source_time.tzinfo is None or source_time.utcoffset() is None:
                raise ValueError("provider timestamp has no timezone")
            age = (as_of - source_time.astimezone(_SHANGHAI)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return QuoteFreshness(
                quote.code, quote.source_timestamp, None, "invalid"
            )

        if age < -self.future_tolerance_seconds:
            status = "future"
        elif age > self.max_quote_age_seconds:
            status = "stale"
        else:
            status = "fresh"
        return QuoteFreshness(
            quote.code, quote.source_timestamp, round(age, 3), status
        )

    def load_snapshot(
        self,
        *,
        lookback_hours: float = 48.0,
        as_of: datetime | None = None,
        regions: Iterable[str] | None = None,
        event_limit: int = 1000,
        evidence_limit: int = 20000,
    ) -> OpportunityDataSnapshot:
        """Load recent events, associated evidence, eligible securities and quotes."""
        if isinstance(lookback_hours, bool):
            raise TypeError("lookback_hours必须是正数")
        lookback_hours = float(lookback_hours)
        if not isfinite(lookback_hours) or lookback_hours <= 0:
            raise ValueError("lookback_hours必须是有限正数")

        snapshot_time = self._normalize_as_of(as_of or self.clock())
        window_start = snapshot_time - timedelta(hours=lookback_hours)
        start_text = window_start.isoformat(timespec="seconds")
        end_text = snapshot_time.isoformat(timespec="seconds")

        region_values = None
        if regions is not None:
            region_values = tuple(sorted({
                str(region).strip() for region in regions if str(region).strip()
            }))

        events = tuple(self.storage.load_market_events(
            start_published_at=start_text,
            end_published_at=end_text,
            regions=region_values,
            limit=event_limit,
        ))
        event_ids = tuple(event.event_id for event in events)
        evidence_rows = (
            self.storage.load_event_mapping_evidence(
                event_ids=event_ids,
                limit=evidence_limit,
            )
            if event_ids else []
        )
        evidence = tuple(
            self._mapping_observation(row) for row in evidence_rows
        )

        securities = tuple(self.storage.load_securities(eligible_only=True))
        security_codes = tuple(item.code for item in securities)
        quotes = tuple(
            self.storage.load_latest_quotes(codes=security_codes)
            if security_codes else ()
        )
        sector_map = self.storage.load_security_sector_codes(
            codes=security_codes
        ) if security_codes else {}
        sector_codes = tuple(
            (code, tuple(sector_map[code]))
            for code in sorted(sector_map)
        )

        freshness = tuple(
            self._assess_quote(quote, snapshot_time) for quote in quotes
        )
        fresh_count = sum(item.status == "fresh" for item in freshness)
        stale_count = sum(item.status == "stale" for item in freshness)
        future_count = sum(item.status == "future" for item in freshness)
        quoted_codes = {quote.code for quote in quotes}
        missing_quote_count = len(set(security_codes) - quoted_codes)

        warnings: list[str] = []
        if not events:
            warnings.append("no_events_in_window")
        if events and not evidence:
            warnings.append("no_mapping_evidence_for_events")
        if not securities:
            warnings.append("no_eligible_securities")
        if not quotes:
            warnings.append("no_provider_timestamped_quotes")
        if missing_quote_count:
            warnings.append(f"quotes_missing_for_{missing_quote_count}_eligible_securities")
        if stale_count:
            warnings.append(f"stale_quotes:{stale_count}")
        if future_count:
            warnings.append(f"future_quotes:{future_count}")
        if quotes and fresh_count == 0:
            warnings.append("no_fresh_quotes")

        return OpportunityDataSnapshot(
            generated_at=end_text,
            window_start=start_text,
            window_end=end_text,
            events=events,
            mapping_evidence=evidence,
            eligible_securities=securities,
            quotes=quotes,
            security_sector_codes=sector_codes,
            quote_freshness=freshness,
            warnings=tuple(warnings),
        )
