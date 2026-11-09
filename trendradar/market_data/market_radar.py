# 本文件组装新闻/事件映射、可信关系库读取、候选扩展、批量行情与排序流程，属于逐文件开发清单第八项。
"""Market opportunity radar orchestration layer."""

from typing import Mapping, Sequence

from trendradar.market_data.cross_market_mapper import discover_sector_evidence, resolve_trusted_relations
from trendradar.market_data.models import Candidate, MarketEvent, Security, MappingEvidence
from trendradar.market_data.opportunity_ranker import rank_candidates, score_candidate
from trendradar.market_data.quote_fetcher import QuoteFetcher
from trendradar.market_data.relation_store import CrossMarketRelationStore
from trendradar.market_data.sector_mapper import expand_by_sectors


class MarketRadar:
    """Composable orchestration with optional persistent reviewed relation lookup."""

    def __init__(self, quote_fetcher: QuoteFetcher, max_candidates: int = 100,
                 push_limit: int = 10, relation_store: CrossMarketRelationStore | None = None):
        self.quote_fetcher = quote_fetcher
        self.max_candidates = max(1, max_candidates)
        self.push_limit = max(1, push_limit)
        self.relation_store = relation_store

    def scan(
        self,
        events: Sequence[MarketEvent],
        securities: Mapping[str, Security],
        trusted_relations: Mapping[str, Sequence[MappingEvidence]] | None = None,
        direct_event_evidence: Mapping[str, Sequence[MappingEvidence]] | None = None,
    ) -> list[Candidate]:
        """Scan candidates; persistent lookup is used when caller omits relation map."""
        direct_event_evidence = direct_event_evidence or {}
        relation_map = trusted_relations
        if relation_map is None:
            relation_map = {}
            if self.relation_store is not None:
                sector_codes = {
                    item.target_code for event in events
                    for item in discover_sector_evidence(event)
                    if item.target_code.startswith("sector:")
                }
                persisted = self.relation_store.trusted_relations_by_sector(sorted(sector_codes))
                relation_map = {
                    sector_code.removeprefix("sector:"): evidence
                    for sector_code, evidence in persisted.items()
                }

        all_evidence: dict[str, list[MappingEvidence]] = {}
        target_sectors: set[str] = set()
        direct_codes: set[str] = set()

        for event in events:
            event_evidence = list(direct_event_evidence.get(event.event_id, ()))
            event_evidence.extend(discover_sector_evidence(event))
            for item in event_evidence:
                if item.target_code.startswith("sector:"):
                    target_sectors.add(item.target_code.removeprefix("sector:"))
                else:
                    direct_codes.add(item.target_code)
                    all_evidence.setdefault(item.target_code, []).append(item)
            resolved = resolve_trusted_relations(event_evidence, relation_map)
            for item in resolved:
                direct_codes.add(item.target_code)
                all_evidence.setdefault(item.target_code, []).append(item)

        expanded = expand_by_sectors(direct_codes, securities, target_sectors)
        codes = list(dict.fromkeys([*sorted(direct_codes), *expanded]))[:self.max_candidates]
        quotes = {q.code: q for q in self.quote_fetcher.fetch(codes)}
        candidates: list[Candidate] = []

        for code in codes:
            security = securities.get(code)
            if not security or security.listing_status != "active" or security.risk_flag:
                continue
            evidence = all_evidence.get(code, [])
            if code not in direct_codes:
                evidence = [MappingEvidence(
                    target_code=code, mapping_type="sector_expansion", confidence=0.4,
                    evidence="由关联板块扩展得到，个股因果关联尚未证实",
                    direction="neutral", verified=False,
                )]
            candidates.append(score_candidate(security, evidence, quotes.get(code)))

        return rank_candidates(candidates, self.push_limit)
