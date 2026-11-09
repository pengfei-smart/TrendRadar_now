# 本文件实现美股/美国宏观事件到A股的证据驱动映射与可信关系库读取接口，属于逐文件开发清单第八项。
"""Evidence-first cross-market event mapping.

Mappings are discovery candidates, not investment recommendations. A mapping only
becomes trusted after evidence is reviewed and persisted in the trusted relation
store (schema is defined in market_schema.sql).
"""

from typing import Iterable, Mapping, Sequence

from trendradar.market_data.models import MappingEvidence, MarketEvent


# Carefully scoped starter rules. Add or refine rules only with a documented source.
_RULES = (
    (("nvidia", "英伟达", "gpu", "ai accelerator"), "半导体", "industry_sentiment", 0.72),
    (("semiconductor", "芯片", "半导体", "存储芯片"), "半导体", "industry_sentiment", 0.68),
    (("cloud", "data center", "数据中心", "光模块", "服务器"), "数据中心", "supply_chain", 0.65),
    (("ev", "electric vehicle", "电动车", "动力电池"), "新能源汽车", "supply_chain", 0.62),
    (("biotech", "clinical trial", "创新药", "生物科技"), "创新药", "industry_sentiment", 0.60),
    (("oil", "crude", "原油", "油价"), "油气", "macro_transmission", 0.58),
    (("gold", "黄金", "copper", "铜"), "有色金属", "macro_transmission", 0.58),
)


def discover_sector_evidence(event: MarketEvent) -> list[MappingEvidence]:
    """Return low-to-medium confidence sector evidence from event title/summary."""
    text = f"{event.title} {event.summary}".casefold()
    results: list[MappingEvidence] = []
    for terms, sector, mapping_type, confidence in _RULES:
        matched = next((term for term in terms if term.casefold() in text), None)
        if matched:
            results.append(MappingEvidence(
                target_code=f"sector:{sector}",
                mapping_type=mapping_type,
                confidence=confidence,
                evidence=f"事件文本命中关联词：{matched}",
                source=event.source,
                source_url=event.url,
                direction=event.direction,
                verified=False,
            ))
    return results


def resolve_trusted_relations(
    sector_evidence: Iterable[MappingEvidence],
    trusted_relations: Mapping[str, Sequence[MappingEvidence]],
) -> list[MappingEvidence]:
    """Resolve sector evidence to securities using caller-provided trusted links."""
    resolved: list[MappingEvidence] = []
    for evidence in sector_evidence:
        if not evidence.target_code.startswith("sector:"):
            continue
        sector = evidence.target_code.removeprefix("sector:")
        for relation in trusted_relations.get(sector, ()):
            resolved.append(MappingEvidence(
                target_code=relation.target_code,
                mapping_type=relation.mapping_type,
                confidence=min(evidence.confidence, relation.confidence),
                evidence=f"{evidence.evidence}; 关系库证据：{relation.evidence}",
                source=relation.source or evidence.source,
                source_url=relation.source_url or evidence.source_url,
                direction=evidence.direction,
                verified=relation.verified,
            ))
    return resolved
