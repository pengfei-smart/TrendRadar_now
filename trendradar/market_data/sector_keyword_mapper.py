"""Keyword-based discovery of Shenwan level-two industries from news text.

Matches are candidate associations only. They do not imply positive/negative impact
or establish that any constituent security benefits from an event.
"""
from collections.abc import Mapping, Sequence

from trendradar.market_data.models import MappingEvidence


# Curated starter rules. Canonical names are resolved against the current sector
# master so the emitted identity is always the database stable SW2 code.
# Keep this list conservative; expand it only with reviewed terminology.
_SECTOR_KEYWORD_RULES = (
    ("种植业", ("种植业", "粮食种植", "玉米种植", "小麦种植", "水稻种植", "转基因种子", "种业"), 0.68),
    ("养殖业", ("生猪养殖", "猪价", "猪周期", "能繁母猪", "肉鸡养殖", "白羽鸡", "水产养殖", "养殖业"), 0.70),
    ("饲料", ("饲料", "豆粕", "菜粕", "饲料添加剂"), 0.68),
    ("农产品加工", ("农产品加工", "粮食加工", "植物油加工", "食品原料加工"), 0.62),
    ("动物保健Ⅱ", ("兽药", "动物疫苗", "口蹄疫疫苗", "禽流感疫苗", "动保"), 0.68),
    ("化学原料", ("磷化工", "钛白粉", "纯碱", "烧碱", "氯碱", "工业硅", "黄磷"), 0.66),
    ("化学制品", ("农药", "化肥", "涂料", "染料", "氟化工", "聚氨酯", "化学制品"), 0.64),
)


def discover_industry_keyword_evidence(
    text: str,
    sectors: Sequence[Mapping[str, str]],
    *,
    source: str = "",
    source_url: str = "",
) -> list[MappingEvidence]:
    """Match news text to known SW2 sectors and return evidence keyed by SW2 code.

    Sector rows must expose sector_code and sector_name; only SW2 rows are
    eligible. Level-one names are intentionally not emitted as direct matches.
    """
    normalized_text = " ".join(str(text or "").casefold().split())
    if not normalized_text:
        return []

    sector_by_name: dict[str, tuple[str, str]] = {}
    for sector in sectors:
        code = str(sector.get("sector_code", "")).strip()
        name = str(sector.get("sector_name", "")).strip()
        if not code.startswith("SW2:") or not name:
            continue
        key = " ".join(name.casefold().split())
        if key in sector_by_name and sector_by_name[key][0] != code:
            raise ValueError(f"申万二级行业名称不唯一，无法安全映射：{name}")
        sector_by_name[key] = (code, name)

    results: list[MappingEvidence] = []
    for canonical_name, terms, confidence in _SECTOR_KEYWORD_RULES:
        resolved = sector_by_name.get(canonical_name.casefold())
        if resolved is None:
            continue
        code, display_name = resolved
        matched_terms = [term for term in terms if term.casefold() in normalized_text]
        if not matched_terms:
            continue
        evidence_terms = "、".join(matched_terms)
        results.append(MappingEvidence(
            target_code=code,
            mapping_type="industry_keyword",
            confidence=confidence,
            evidence=f"新闻文本命中关键词：{evidence_terms}；候选行业：{display_name}",
            source=source,
            source_url=source_url,
            # Event direction is not the same as industry impact direction.
            direction="neutral",
            verified=False,
        ))
    return results
