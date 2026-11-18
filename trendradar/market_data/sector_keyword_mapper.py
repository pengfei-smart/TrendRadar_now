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
    # First reviewed technology batch. Terms are deliberately specific where
    # adjacent SW2 industries share broad vocabulary.
    ("半导体", (
        "半导体", "芯片", "集成电路", "晶圆代工", "晶圆制造",
        "半导体设备", "半导体材料", "光刻机", "刻蚀设备",
        "薄膜沉积", "封装测试", "存储芯片", "功率半导体",
        "模拟芯片", "GPU芯片", "CPU芯片", "HBM",
    ), 0.72),
    ("元件", (
        "印制电路板", "PCB", "覆铜板", "CCL", "MLCC",
        "多层陶瓷电容", "被动元件", "电感器", "电容器", "电子连接器",
    ), 0.70),
    ("光学光电子", (
        "显示面板", "OLED面板", "Mini LED", "Micro LED",
        "光学镜头", "光学膜", "偏光片", "背光模组", "激光显示",
    ), 0.70),
    ("消费电子", (
        "消费电子", "智能手机", "折叠屏手机", "TWS耳机",
        "智能穿戴", "可穿戴设备", "AR眼镜", "VR头显",
        "苹果产业链", "手机代工",
    ), 0.68),
    ("通信设备", (
        "光模块", "光通信", "CPO", "硅光", "交换机",
        "路由器", "5G基站", "基站设备", "通信设备",
        "卫星通信终端",
    ), 0.72),
    ("电池", (
        "动力电池", "储能电池", "锂电池", "固态电池",
        "钠离子电池", "电芯", "电池管理系统", "电池回收",
    ), 0.70),
    ("光伏设备", (
        "光伏组件", "光伏电池", "光伏设备", "TOPCon",
        "HJT", "异质结电池", "BC电池", "钙钛矿太阳能电池",
    ), 0.72),
    ("电网设备", (
        "特高压", "输变电设备", "电力变压器", "配电网",
        "智能电网", "电网投资", "高压开关设备",
        "GIS组合电器", "继电保护", "电力电缆",
    ), 0.72),
    # Second reviewed batch: commodity and upstream energy chains. Keep
    # resource extraction, processing, and downstream equipment distinct.
    ("工业金属", (
        "电解铝", "氧化铝价格", "铜价", "铝价", "铜冶炼",
        "铝加工", "铜加工", "再生铝", "铜精矿", "铝土矿",
    ), 0.70),
    ("能源金属", (
        "碳酸锂", "氢氧化锂", "锂矿", "锂盐", "锂辉石",
        "锂云母", "钴矿", "镍矿", "硫酸镍", "硫酸钴",
    ), 0.72),
    ("贵金属", (
        "黄金价格", "国际金价", "金价", "黄金矿山",
        "黄金冶炼", "白银价格", "白银现货", "铂金价格",
    ), 0.70),
    ("小金属", (
        "稀土价格", "稀土矿", "氧化镨钕", "钨价",
        "钼价", "锑价", "锗价", "镓价", "铟价",
    ), 0.70),
    ("冶钢原料", (
        "铁矿石价格", "铁矿石", "废钢价格", "废钢",
        "焦炭价格", "锰硅", "硅铁", "炼焦煤价格",
    ), 0.68),
    ("普钢", (
        "螺纹钢价格", "热轧卷板", "冷轧板卷",
        "钢材价格", "钢铁产量", "钢厂高炉",
    ), 0.68),
    ("特钢Ⅱ", (
        "特种钢", "特殊钢", "轴承钢", "工模具钢",
        "高温合金钢", "不锈钢价格",
    ), 0.68),
    ("煤炭开采", (
        "动力煤价格", "动力煤", "煤炭价格", "煤矿产能",
        "煤炭产量", "煤炭库存", "煤炭开采",
    ), 0.70),
    ("油气开采Ⅱ", (
        "布伦特原油", "WTI原油", "原油价格", "天然气价格",
        "油气田", "页岩油", "页岩气", "油气勘探",
    ), 0.72),
    ("油服工程", (
        "油服工程", "油田服务", "钻井平台", "海上钻井",
        "油气工程服务", "压裂设备", "钻完井服务",
    ), 0.70),
    ("炼化及贸易", (
        "炼化一体化", "炼油产能", "炼油厂", "成品油价格",
        "石油化工贸易", "石化产品贸易", "炼化装置",
    ), 0.68),
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
    # Explicit, reviewed semantic terms take precedence over the generic
    # official-name match. This gives all current SW2 industries baseline
    # coverage without inventing broad synonyms for industries not yet reviewed.
    rules_by_name = {
        name.casefold(): (terms, confidence)
        for name, terms, confidence in _SECTOR_KEYWORD_RULES
    }
    for key, (code, display_name) in sector_by_name.items():
        terms, confidence = rules_by_name.get(key, ((), 0.56))
        matched_terms = [term for term in terms if term.casefold() in normalized_text]
        if display_name.casefold() in normalized_text and display_name not in matched_terms:
            matched_terms.append(display_name)
        if not matched_terms:
            continue
        # Exact official industry-name matches are weaker than reviewed
        # industry-specific terminology; keep both evidence terms if present.
        match_confidence = confidence if any(
            term.casefold() in normalized_text for term in terms
        ) else 0.56
        evidence_terms = "、".join(matched_terms)
        results.append(MappingEvidence(
            target_code=code,
            mapping_type="industry_keyword",
            confidence=match_confidence,
            evidence=f"新闻文本命中关键词：{evidence_terms}；候选行业：{display_name}",
            source=source,
            source_url=source_url,
            direction="neutral",
            verified=False,
        ))
    return results


def build_industry_keyword_coverage_report(
    sectors: Sequence[Mapping[str, str]],
) -> dict[str, object]:
    """Summarize reviewed-rule coverage separately from official-name coverage."""
    reviewed_names = {
        name.casefold(): terms
        for name, terms, _confidence in _SECTOR_KEYWORD_RULES
    }
    rows: list[dict[str, object]] = []
    seen_codes: set[str] = set()
    for sector in sectors:
        code = str(sector.get("sector_code", "")).strip()
        name = str(sector.get("sector_name", "")).strip()
        if not code.startswith("SW2:") or not name or code in seen_codes:
            continue
        seen_codes.add(code)
        terms = reviewed_names.get(name.casefold(), ())
        rows.append({
            "sector_code": code,
            "sector_name": name,
            "coverage": "reviewed_terms" if terms else "official_name_only",
            "reviewed_term_count": len(terms),
            "reviewed_terms": list(terms),
        })
    rows.sort(key=lambda row: str(row["sector_code"]))
    reviewed_count = sum(row["coverage"] == "reviewed_terms" for row in rows)
    total = len(rows)
    return {
        "total_level_two_sectors": total,
        "reviewed_terms_covered": reviewed_count,
        "official_name_only": total - reviewed_count,
        "reviewed_terms_coverage_ratio": round(reviewed_count / total, 4) if total else 0.0,
        "sectors": rows,
    }


def discover_industry_keyword_evidence_from_storage(
    text: str,
    storage,
    *,
    source: str = "",
    source_url: str = "",
    provider: str = "akshare_sw",
) -> list[MappingEvidence]:
    """Load the authoritative industry catalog from MarketStorage, then match text.

    The storage adapter is intentionally small (load_industry_sectors), keeping
    keyword rules independent from SQLite and making the lookup easy to test.
    """
    if not str(text or "").strip():
        return []
    sectors = storage.load_industry_sectors(provider=provider)
    return discover_industry_keyword_evidence(
        text, sectors, source=source, source_url=source_url,
    )
