"""Conservative, explainable event taxonomy classification.

Classification is separate from event impact, direction, relevance and scoring.
A matched category is a content label only; it is not a bullish/bearish signal.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable

from .models import MarketEvent


@dataclass(frozen=True)
class EventClassification:
    event_id: str
    event_category: str
    classification_confidence: float
    matched_terms: tuple[str, ...]
    classification_status: str
    candidate_categories: tuple[str, ...] = ()


# Confidence represents confidence in the text label, not event importance.
_RULES: tuple[tuple[str, tuple[str, ...], float], ...] = (
    ("monetary_policy", (
        "联邦公开市场委员会", "联邦储备", "美联储", "联储利率", "基准利率",
        "加息", "降息", "货币政策", "monetary policy", "federal reserve",
        "fomc", "interest rate", "rate hike", "rate cut",
    ), 0.88),
    ("inflation_data", (
        "消费者价格指数", "生产者价格指数", "居民消费价格", "工业生产者出厂价格",
        "通货膨胀", "通胀数据", "cpi", "core cpi", "ppi", "inflation",
    ), 0.88),
    ("employment_data", (
        "非农就业", "非农数据", "失业率", "就业报告", "新增就业",
        "职位空缺", "就业人数", "nonfarm payroll", "unemployment rate",
        "employment report", "job openings",
    ), 0.88),
    ("fiscal_policy", (
        "财政预算", "财政支出", "政府补贴", "税收减免", "税收政策",
        "专项债", "国债发行", "fiscal policy", "government subsidy",
        "tax credit", "budget deficit",
    ), 0.82),
    ("industry_policy", (
        "产业政策", "行业政策", "行业监管", "产业规划", "准入政策",
        "监管新规", "出口管制", "进口关税", "反倾销", "industry policy",
        "industry regulation", "export control", "import tariff",
    ), 0.82),
    ("supply_demand", (
        "供给收缩", "供应短缺", "产能扩张", "产能退出", "停产检修",
        "库存下降", "库存上升", "供需缺口", "供需失衡", "supply shortage",
        "capacity expansion", "production cut", "inventory draw",
    ), 0.78),
    ("commodity_price", (
        "原油价格", "油价", "黄金价格", "金价", "铜价", "铝价", "锂价",
        "铁矿石价格", "大宗商品", "commodity prices", "crude oil price",
        "gold price", "copper price", "lithium price",
    ), 0.78),
    ("corporate_earnings", (
        "业绩预告", "业绩快报", "年度报告", "季度报告", "净利润",
        "营收增长", "盈利预警", "业绩上修", "业绩下修", "earnings",
        "quarterly results", "annual report", "profit warning",
        "revenue growth",
    ), 0.82),
    ("product_technology", (
        "新产品发布", "新品发布", "量产", "技术突破", "技术发布",
        "产品发布", "首发产品", "新一代芯片", "product launch",
        "technology breakthrough", "mass production", "new product",
    ), 0.76),
    ("geopolitical", (
        "地缘政治", "军事冲突", "武装冲突", "国际制裁", "经济制裁",
        "贸易战", "外交制裁", "geopolitical", "military conflict",
        "economic sanctions", "trade war",
    ), 0.82),
)


def _normalize_text(event: MarketEvent) -> str:
    return f"{event.title or ''} {event.summary or ''}".casefold()


def _term_matches(text: str, term: str) -> bool:
    normalized = term.casefold()
    if normalized.isascii() and any(char.isalpha() for char in normalized):
        pattern = rf"(?<![a-z0-9]){re.escape(normalized)}(?![a-z0-9])"
        return re.search(pattern, text) is not None
    return normalized in text


def classify_market_event(event: MarketEvent) -> EventClassification:
    """Classify an event from title/summary without inferring impact or direction."""
    if not isinstance(event, MarketEvent):
        raise TypeError("event must be a MarketEvent")

    text = _normalize_text(event)
    matches: list[tuple[str, str, float]] = []
    for category, terms, confidence in _RULES:
        for term in terms:
            if _term_matches(text, term):
                matches.append((category, term, confidence))

    if not matches:
        return EventClassification(
            event_id=event.event_id,
            event_category="unknown",
            classification_confidence=0.0,
            matched_terms=(),
            classification_status="unknown",
        )

    best_confidence = max(item[2] for item in matches)
    best_categories = sorted({
        category for category, _, confidence in matches
        if confidence == best_confidence
    })
    if len(best_categories) != 1:
        return EventClassification(
            event_id=event.event_id,
            event_category="unknown",
            classification_confidence=best_confidence,
            matched_terms=tuple(sorted({
                term for category, term, confidence in matches
                if confidence == best_confidence
            })),
            classification_status="review_required",
            candidate_categories=tuple(best_categories),
        )

    category = best_categories[0]
    category_matches = tuple(sorted({
        term for matched_category, term, _ in matches
        if matched_category == category
    }))
    status = "auto_classified" if best_confidence >= 0.75 else "review_required"
    return EventClassification(
        event_id=event.event_id,
        event_category=category,
        classification_confidence=best_confidence,
        matched_terms=category_matches,
        classification_status=status,
        candidate_categories=(category,),
    )


def classify_market_events(
    events: Iterable[MarketEvent],
) -> tuple[EventClassification, ...]:
    """Classify events deterministically, preserving input event order."""
    return tuple(classify_market_event(event) for event in events)
