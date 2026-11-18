"""Pure market-observation derivations for opportunity scoring.

Industry breadth is calculated from eligible SW2 constituents with fresh,
provider-timestamped quotes. Quote coverage is reported independently so a
small observed subset cannot masquerade as broad market confirmation.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from .opportunity_data_provider import OpportunityDataSnapshot


@dataclass(frozen=True)
class IndustryMarketBreadth:
    """Cross-sectional breadth and data coverage for one SW2 industry.

    breadth_ratio is advancing securities / securities with a usable fresh
    change_pct observation. Unchanged securities remain in the denominator.
    quote_coverage_ratio is usable fresh observations / eligible SW2 members.
    """

    industry_code: str
    eligible_security_count: int
    fresh_quote_count: int
    observed_quote_count: int
    advancing_count: int
    declining_count: int
    unchanged_count: int
    breadth_ratio: float | None
    quote_coverage_ratio: float


def calculate_industry_market_breadth(
    snapshot: OpportunityDataSnapshot,
) -> tuple[IndustryMarketBreadth, ...]:
    """Calculate SW2 breadth from one OpportunityDataSnapshot.

    Only securities in snapshot.eligible_securities contribute to the
    denominator. Only quotes whose freshness status is exactly "fresh" and
    whose change_pct is finite contribute to breadth and coverage.
    """
    eligible_codes = {item.code for item in snapshot.eligible_securities}
    members_by_sector: dict[str, set[str]] = {}
    for security_code, sector_codes in snapshot.security_sector_codes:
        if security_code not in eligible_codes:
            continue
        for sector_code in set(sector_codes):
            if sector_code.startswith("SW2:"):
                members_by_sector.setdefault(sector_code, set()).add(security_code)

    freshness_by_code = {}
    for item in snapshot.quote_freshness:
        if item.security_code in freshness_by_code:
            raise ValueError(f"重复行情新鲜度记录: {item.security_code}")
        freshness_by_code[item.security_code] = item.status

    quotes_by_code = {}
    for quote in snapshot.quotes:
        if quote.code in quotes_by_code:
            raise ValueError(f"重复行情记录: {quote.code}")
        quotes_by_code[quote.code] = quote

    result: list[IndustryMarketBreadth] = []
    for industry_code in sorted(members_by_sector):
        members = members_by_sector[industry_code]
        fresh_quote_count = 0
        observed_changes: list[float] = []
        for security_code in members:
            quote = quotes_by_code.get(security_code)
            if quote is None or freshness_by_code.get(security_code) != "fresh":
                continue
            fresh_quote_count += 1
            change_pct = quote.change_pct
            if change_pct is None:
                continue
            try:
                change_value = float(change_pct)
            except (TypeError, ValueError, OverflowError):
                continue
            if isfinite(change_value):
                observed_changes.append(change_value)

        advancing = sum(value > 0 for value in observed_changes)
        declining = sum(value < 0 for value in observed_changes)
        unchanged = sum(value == 0 for value in observed_changes)
        observed_count = len(observed_changes)
        eligible_count = len(members)
        result.append(IndustryMarketBreadth(
            industry_code=industry_code,
            eligible_security_count=eligible_count,
            fresh_quote_count=fresh_quote_count,
            observed_quote_count=observed_count,
            advancing_count=advancing,
            declining_count=declining,
            unchanged_count=unchanged,
            breadth_ratio=(
                advancing / observed_count if observed_count else None
            ),
            quote_coverage_ratio=(
                observed_count / eligible_count if eligible_count else 0.0
            ),
        ))

    return tuple(result)
