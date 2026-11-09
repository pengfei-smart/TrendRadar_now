# 本文件提供基于证券主数据与行业/板块成员关系的候选扩展，属于逐文件开发清单第八项。
"""Sector and supply-chain expansion helpers."""

from typing import Iterable, Mapping, Sequence

from trendradar.market_data.models import Security


def expand_by_sectors(
    direct_codes: Iterable[str],
    securities: Mapping[str, Security],
    target_sectors: Iterable[str],
    max_per_sector: int = 30,
) -> list[str]:
    """Expand directly-mentioned codes to members of matching sectors.

    Output is deterministic and does not imply that an expanded security benefits
    from the event. The caller must preserve this distinction in its evidence.
    """
    direct = set(direct_codes)
    sectors = set(target_sectors)
    expanded: list[str] = []
    seen = set(direct)

    for sector in sorted(sectors):
        members = sorted(
            (s for s in securities.values() if sector in s.sectors),
            key=lambda s: s.code,
        )
        count = 0
        for security in members:
            if security.code in seen:
                continue
            if security.listing_status != "active" or security.risk_flag:
                continue
            expanded.append(security.code)
            seen.add(security.code)
            count += 1
            if count >= max_per_sector:
                break
    return expanded


def filter_eligible_securities(securities: Sequence[Security]) -> list[Security]:
    """Exclude suspended/inactive and risk-flagged stocks from eligible universe."""
    return [
        security for security in securities
        if security.listing_status == "active" and not security.risk_flag
    ]
