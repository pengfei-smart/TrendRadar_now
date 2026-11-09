# 本文件兼容行情门面返回的(报价列表,来源名称)，并由扫描持久化流程记录实际来源及数据时间。
"""Market opportunity radar orchestration layer."""

from datetime import datetime
from time import perf_counter
from typing import Mapping, Sequence

from trendradar.market_data.cross_market_mapper import discover_sector_evidence, resolve_trusted_relations
from trendradar.market_data.models import Candidate, MarketEvent, Security, MappingEvidence
from trendradar.market_data.opportunity_ranker import rank_candidates, score_candidate
from trendradar.market_data.quote_fetcher import QuoteFetcher
from trendradar.market_data.relation_store import CrossMarketRelationStore
from trendradar.market_data.result_store import RadarResultStore
from trendradar.market_data.sector_mapper import expand_by_sectors


class MarketRadar:
    """Composable orchestration with optional persistent relation and result stores."""

    def __init__(self, quote_fetcher: QuoteFetcher, max_candidates: int = 100,
                 push_limit: int = 10, relation_store: CrossMarketRelationStore | None = None,
                 result_store: RadarResultStore | None = None):
        self.quote_fetcher = quote_fetcher
        self.max_candidates = max(1, max_candidates)
        self.push_limit = max(1, push_limit)
        self.relation_store = relation_store
        self.result_store = result_store

    def _scan(self, events: Sequence[MarketEvent], securities: Mapping[str, Security],
              trusted_relations: Mapping[str, Sequence[MappingEvidence]] | None = None,
              direct_event_evidence: Mapping[str, Sequence[MappingEvidence]] | None = None,
              limit_results: bool = True) -> tuple[list[Candidate], str, str]:
        direct_event_evidence = direct_event_evidence or {}
        relation_map = trusted_relations
        if relation_map is None:
            relation_map = {}
            if self.relation_store is not None:
                sector_codes = {item.target_code for event in events
                    for item in discover_sector_evidence(event) if item.target_code.startswith("sector:")}
                persisted = self.relation_store.trusted_relations_by_sector(sorted(sector_codes))
                relation_map = {code.removeprefix("sector:"): evidence for code, evidence in persisted.items()}

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
            for item in resolve_trusted_relations(event_evidence, relation_map):
                direct_codes.add(item.target_code)
                all_evidence.setdefault(item.target_code, []).append(item)

        expanded = expand_by_sectors(direct_codes, securities, target_sectors)
        codes = list(dict.fromkeys([*sorted(direct_codes), *expanded]))[:self.max_candidates]
        if not codes:
            return [], "empty_candidate_set", ""

        fetched = self.quote_fetcher.fetch([securities[code] for code in codes if code in securities])
        # QuoteFetcher facade returns (quotes, source); accept list-only providers
        # as well for backward-compatible lightweight test doubles.
        if isinstance(fetched, tuple) and len(fetched) == 2:
            quote_items, quote_source = fetched
        else:
            quote_items, quote_source = fetched, getattr(self.quote_fetcher, "source_name", "")
        quotes = {q.code: q for q in quote_items}
        quote_data_at = max((q.timestamp for q in quote_items if q.timestamp), default="")

        candidates: list[Candidate] = []
        for code in codes:
            security = securities.get(code)
            if not security or security.listing_status != "active" or security.risk_flag:
                continue
            evidence = all_evidence.get(code, [])
            if code not in direct_codes:
                evidence = [MappingEvidence(target_code=code, mapping_type="sector_expansion",
                    confidence=0.4, evidence="由关联板块扩展得到，个股因果关联尚未证实",
                    direction="neutral", verified=False)]
            quote = quotes.get(code)\n            # A relation alone must never make a stock push-eligible: require a usable quote.\n            if quote is None or quote.price is None or quote.price <= 0:\n                continue\n            if quote.change_pct is None or quote.turnover is None or quote.turnover < 0:\n                continue\n            candidates.append(score_candidate(security, evidence, quote))
        return rank_candidates(candidates, self.max_candidates if not limit_results else self.push_limit), quote_source, quote_data_at

    def scan(self, events: Sequence[MarketEvent], securities: Mapping[str, Security],
             trusted_relations: Mapping[str, Sequence[MappingEvidence]] | None = None,
             direct_event_evidence: Mapping[str, Sequence[MappingEvidence]] | None = None,
             limit_results: bool = True) -> list[Candidate]:
        """Return ranked candidates; set limit_results=False to retain full candidate list."""
        candidates, _, _ = self._scan(events, securities, trusted_relations, direct_event_evidence, limit_results)
        return candidates

    def scan_and_persist(self, events: Sequence[MarketEvent], securities: Mapping[str, Security], *,
                         run_at: str | None = None, run_type: str = "snapshot",
                         trusted_relations: Mapping[str, Sequence[MappingEvidence]] | None = None,
                         direct_event_evidence: Mapping[str, Sequence[MappingEvidence]] | None = None,
                         source_status: str = "", quote_source: str = "", quote_data_at: str = "",
                         notes: str = "") -> list[Candidate]:
        """Persist full ranked candidates and return top push-limit selection."""
        if self.result_store is None:
            raise RuntimeError("result_store is required for scan_and_persist")
        run_at = run_at or datetime.now().astimezone().isoformat(timespec="seconds")
        started = perf_counter()
        run_id = self.result_store.start_run(run_at, run_type, notes)
        try:
            ranked, actual_source, actual_data_at = self._scan(
                events, securities, trusted_relations, direct_event_evidence, limit_results=False)
            selected = ranked[:self.push_limit]
            self.result_store.finish_run(run_id, ranked, status="success",
                pushed_codes=[candidate.security.code for candidate in selected],
                source_status=source_status or (f"quote:success:{actual_source}" if actual_source else "quote:empty"),
                quote_source=quote_source or actual_source, quote_data_at=quote_data_at or actual_data_at,
                duration_ms=int((perf_counter()-started)*1000), notes=notes)
            return selected
        except Exception as exc:
            self.result_store.finish_run(run_id, [], status="failed",
                source_status=source_status or f"quote:failed:{type(exc).__name__}",
                quote_source=quote_source, quote_data_at=quote_data_at,
                duration_ms=int((perf_counter()-started)*1000),
                notes=f"{notes} | {type(exc).__name__}: {exc}".strip(" |"))
            raise
