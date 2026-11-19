"""Traceable, currency-separated cost calculation for event-review model calls.

Provider routing is never guessed: callers must explicitly map the persisted
adapter/provider + exact model pair to the actual billing provider.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping

from .event_review_pricing_repository import EventReviewPricingRepository
from .event_review_temporal import parse_aware_datetime
from .storage import MarketStorage


@dataclass(frozen=True)
class EventReviewCallCost:
    usage_id: int
    event_id: str
    reviewer_role: str
    recorded_provider: str
    billing_provider: str
    model: str
    request_status: str
    cost_status: str
    currency: str | None
    pricing_id: str
    prompt_tokens: int | None
    completion_tokens: int | None
    known_cost: Decimal | None
    complete_cost: Decimal | None
    call_started_at: str


@dataclass(frozen=True)
class CostTotals:
    calls: int
    calculated_calls: int
    partial_usage_calls: int
    missing_usage_calls: int
    missing_pricing_calls: int
    unresolved_provider_calls: int
    ambiguous_pricing_calls: int
    known_cost: Decimal
    complete_cost: Decimal


@dataclass(frozen=True)
class CurrencyCostTotals:
    currency: str | None
    totals: CostTotals


@dataclass(frozen=True)
class CostBreakdown:
    key: str
    currency: str | None
    totals: CostTotals


@dataclass(frozen=True)
class EventReviewCostReport:
    start_at: str
    end_at: str
    event_id: str
    totals_by_currency: tuple[CurrencyCostTotals, ...]
    by_role: tuple[CostBreakdown, ...]
    by_model: tuple[CostBreakdown, ...]
    by_event: tuple[CostBreakdown, ...]
    by_day: tuple[CostBreakdown, ...]
    lines: tuple[EventReviewCallCost, ...]


_ZERO = Decimal("0")


def _timestamp_filter(value: str | None, field: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str) or parse_aware_datetime(value) is None:
        raise ValueError(f"{field} must be an ISO-8601 timestamp with timezone")
    return value


def _money(tokens: int, price_per_million: Decimal) -> Decimal:
    return Decimal(tokens) * price_per_million / Decimal(1_000_000)


def _cost_status(row, billing_provider: str, pricing_repo: EventReviewPricingRepository):
    prompt = row["prompt_tokens"]
    completion = row["completion_tokens"]
    if prompt is None and completion is None:
        return "missing_usage", None, None, None, ""
    if not billing_provider:
        return "unresolved_provider", None, None, None, ""
    try:
        price = pricing_repo.find_price(
            provider=billing_provider, model=row["model"], at=row["call_started_at"]
        )
    except RuntimeError:
        return "ambiguous_pricing", None, None, None, ""
    if price is None:
        return "missing_pricing", None, None, None, ""

    known = _ZERO
    if prompt is not None:
        known += _money(prompt, price.input_price_per_million)
    if completion is not None:
        known += _money(completion, price.output_price_per_million)
    if prompt is None or completion is None:
        return "partial_usage", price.currency, known, None, price.pricing_id
    return "calculated", price.currency, known, known, price.pricing_id


def _totals(lines: list[EventReviewCallCost]) -> CostTotals:
    counts = defaultdict(int)
    known = _ZERO
    complete = _ZERO
    for line in lines:
        counts["calls"] += 1
        if line.cost_status == "calculated":
            counts["calculated_calls"] += 1
        elif line.cost_status == "partial_usage":
            counts["partial_usage_calls"] += 1
        elif line.cost_status == "missing_usage":
            counts["missing_usage_calls"] += 1
        elif line.cost_status == "missing_pricing":
            counts["missing_pricing_calls"] += 1
        elif line.cost_status == "unresolved_provider":
            counts["unresolved_provider_calls"] += 1
        elif line.cost_status == "ambiguous_pricing":
            counts["ambiguous_pricing_calls"] += 1
        if line.known_cost is not None:
            known += line.known_cost
        if line.complete_cost is not None:
            complete += line.complete_cost
    return CostTotals(
        calls=counts["calls"],
        calculated_calls=counts["calculated_calls"],
        partial_usage_calls=counts["partial_usage_calls"],
        missing_usage_calls=counts["missing_usage_calls"],
        missing_pricing_calls=counts["missing_pricing_calls"],
        unresolved_provider_calls=counts["unresolved_provider_calls"],
        ambiguous_pricing_calls=counts["ambiguous_pricing_calls"],
        known_cost=known,
        complete_cost=complete,
    )


def _breakdown(lines: tuple[EventReviewCallCost, ...], field: str, limit: int):
    buckets: dict[tuple[str, str | None], list[EventReviewCallCost]] = defaultdict(list)
    for line in lines:
        key = getattr(line, field)
        buckets[(key, line.currency)].append(line)
    return tuple(
        CostBreakdown(key=key, currency=currency, totals=_totals(items))
        for (key, currency), items in list(sorted(
            buckets.items(), key=lambda item: (item[0][0], item[0][1] or "")
        ))[:limit]
    )


def get_event_review_cost_report(
    storage: MarketStorage,
    pricing_repository: EventReviewPricingRepository,
    *,
    provider_mapping: Mapping[tuple[str, str], str],
    start_at: str | None = None,
    end_at: str | None = None,
    event_id: str = "",
    limit: int = 100,
) -> EventReviewCostReport:
    """Calculate costs from persisted usage and historical prices.

    provider_mapping keys are exact (persisted provider, exact model) pairs;
    values are the actual billing-provider identifiers used in the price catalog.
    Missing mappings are reported as unresolved_provider, never guessed.
    Date filtering is start-inclusive/end-exclusive. Currency totals are kept
    separate; no foreign-exchange conversion is attempted.
    """
    if not isinstance(storage, MarketStorage):
        raise TypeError("storage must be a MarketStorage instance")
    if not isinstance(pricing_repository, EventReviewPricingRepository):
        raise TypeError("pricing_repository must be an EventReviewPricingRepository")
    if pricing_repository.storage is not storage:
        raise ValueError("pricing_repository must use the same MarketStorage instance")
    if not isinstance(provider_mapping, Mapping):
        raise TypeError("provider_mapping must be a mapping")
    normalized_mapping: dict[tuple[str, str], str] = {}
    for key, value in provider_mapping.items():
        if (
            not isinstance(key, tuple) or len(key) != 2
            or not all(isinstance(part, str) and part.strip() for part in key)
            or not isinstance(value, str) or not value.strip()
        ):
            raise ValueError("provider_mapping must map non-empty (provider, model) pairs to billing providers")
        normalized_mapping[(key[0].strip(), key[1].strip())] = value.strip()

    start = _timestamp_filter(start_at, "start_at")
    end = _timestamp_filter(end_at, "end_at")
    if start and end and parse_aware_datetime(end) <= parse_aware_datetime(start):
        raise ValueError("end_at must be later than start_at")
    if not isinstance(event_id, str):
        raise TypeError("event_id must be a string")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
        raise ValueError("limit must be an integer between 1 and 500")

    clauses: list[str] = []
    params: list[object] = []
    if start:
        clauses.append("julianday(call_started_at) >= julianday(?)")
        params.append(start)
    if end:
        clauses.append("julianday(call_started_at) < julianday(?)")
        params.append(end)
    if event_id.strip():
        clauses.append("event_id=?")
        params.append(event_id.strip())
    where = "".join(f" AND ({clause})" for clause in clauses)

    with storage.connect() as connection:
        rows = connection.execute(
            """SELECT usage_id,event_id,reviewer_role,provider,model,request_status,
                      prompt_tokens,completion_tokens,call_started_at
               FROM event_review_model_usage WHERE 1=1"""
            + where + " ORDER BY call_started_at,usage_id",
            params,
        ).fetchall()

    pricing_repo = pricing_repository
    lines: list[EventReviewCallCost] = []
    for row in rows:
        recorded_provider = row["provider"] or ""
        model = row["model"] or ""
        billing_provider = normalized_mapping.get((recorded_provider, model), "")
        status, currency, known, complete, pricing_id = _cost_status(
            row, billing_provider, pricing_repo
        )
        lines.append(EventReviewCallCost(
            usage_id=row["usage_id"],
            event_id=row["event_id"],
            reviewer_role=row["reviewer_role"],
            recorded_provider=recorded_provider,
            billing_provider=billing_provider,
            model=model,
            request_status=row["request_status"],
            cost_status=status,
            currency=currency,
            pricing_id=pricing_id,
            prompt_tokens=row["prompt_tokens"],
            completion_tokens=row["completion_tokens"],
            known_cost=known,
            complete_cost=complete,
            call_started_at=row["call_started_at"],
        ))

    all_lines = tuple(lines)
    currency_groups: dict[str | None, list[EventReviewCallCost]] = defaultdict(list)
    for line in all_lines:
        currency_groups[line.currency].append(line)
    totals_by_currency = tuple(
        CurrencyCostTotals(currency=currency, totals=_totals(items))
        for currency, items in sorted(currency_groups.items(), key=lambda item: item[0] or "")
    )
    day_lines = tuple(
        EventReviewCallCost(
            usage_id=line.usage_id,
            event_id=line.call_started_at[:10],
            reviewer_role=line.reviewer_role,
            recorded_provider=line.recorded_provider,
            billing_provider=line.billing_provider,
            model=line.model,
            request_status=line.request_status,
            cost_status=line.cost_status,
            currency=line.currency,
            pricing_id=line.pricing_id,
            prompt_tokens=line.prompt_tokens,
            completion_tokens=line.completion_tokens,
            known_cost=line.known_cost,
            complete_cost=line.complete_cost,
            call_started_at=line.call_started_at,
        )
        for line in all_lines
    )
    return EventReviewCostReport(
        start_at=start, end_at=end, event_id=event_id.strip(),
        totals_by_currency=totals_by_currency,
        by_role=_breakdown(all_lines, "reviewer_role", limit),
        by_model=_breakdown(all_lines, "model", limit),
        by_event=_breakdown(all_lines, "event_id", limit),
        by_day=_breakdown(day_lines, "event_id", limit),
        lines=all_lines,
    )


__all__ = [
    "CostBreakdown", "CostTotals", "CurrencyCostTotals",
    "EventReviewCallCost", "EventReviewCostReport",
    "get_event_review_cost_report",
]
