# 本文件定义A股新闻事件到证券/行业候选的标准映射接口，属于逐文件开发清单第八项。
"""China event-to-security mapping interfaces."""

from typing import Mapping, Sequence

from trendradar.market_data.models import MappingEvidence, MarketEvent


class CNEventMapper:
    """Map event identifiers to externally prepared evidence.

    AI batch extraction may be plugged into this interface. The first version
    deliberately avoids per-stock LLM calls and keeps source evidence explicit.
    """

    def map_events(
        self,
        events: Sequence[MarketEvent],
        evidence_by_event: Mapping[str, Sequence[MappingEvidence]],
    ) -> dict[str, list[MappingEvidence]]:
        event_ids = {event.event_id for event in events}
        return {
            event_id: list(evidence)
            for event_id, evidence in evidence_by_event.items()
            if event_id in event_ids
        }
