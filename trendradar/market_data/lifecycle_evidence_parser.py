# 本文件从公告文本中提取证券代码、生命周期事件及带语义标签的日期证据。
"""Rule-based extraction of lifecycle facts from Chinese exchange announcement text."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date


CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")
DATE_PATTERNS = (
    re.compile(r"(?P<year>20\d{2})年(?P<month>\d{1,2})月(?P<day>\d{1,2})日?"),
    re.compile(r"(?P<year>20\d{2})[-/.](?P<month>\d{1,2})[-/.](?P<day>\d{1,2})"),
)

EVENT_RULES = (
    ("delisting", ("终止上市", "摘牌", "退市")),
    ("suspension", ("暂停上市", "停牌", "暂停交易")),
    ("resumption", ("恢复上市", "恢复交易", "复牌")),
    ("listing", ("首次公开发行", "上市交易", "上市公告书", "正式上市")),
    ("relisting", ("重新上市",)),
    ("transfer_to_neeq", ("退市板块", "两网及退市公司", "转入全国股转系统")),
    ("cash_tender_offer", ("现金选择权", "现金要约收购", "要约收购")),
    ("reorganization", ("破产重整", "重整计划", "司法重整")),
    ("merger", ("吸收合并", "合并实施")),
)

DATE_LABEL_RULES = (
    ("last_trading_date", ("最后交易日", "最后一个交易日", "终止上市前最后交易日")),
    ("delisting_date", ("摘牌日", "终止上市日", "终止上市日期")),
    ("decision_date", ("决定日期", "作出决定之日", "核准日期", "批复日期")),
    ("effective_date", ("生效日", "实施日", "正式生效")),
    ("transfer_start_date", ("开始转让日", "转让起始日", "挂牌转让日")),
    ("announcement_date", ("公告日期", "公告日", "发布日期", "发布于")),
)


@dataclass(frozen=True)
class DateEvidence:
    value: str
    date_type: str
    date_status: str
    context: str


@dataclass(frozen=True)
class ParsedLifecycleEvidence:
    security_codes: tuple[str, ...]
    event_types: tuple[str, ...]
    date_evidence: tuple[DateEvidence, ...]
    matched_terms: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "security_codes": list(self.security_codes),
            "event_types": list(self.event_types),
            "date_evidence": [asdict(item) for item in self.date_evidence],
            "matched_terms": list(self.matched_terms),
        }


def _normalize_date(match: re.Match) -> str | None:
    try:
        parsed = date(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
        )
    except ValueError:
        return None
    return parsed.isoformat()


def _date_type(context: str) -> str:
    before, separator, after = context.partition("⟦DATE⟧")
    # 只取当前日期之前最近出现的标签，不能让更早日期的标签跨日期继承。
    preceding = []
    for date_type, terms in DATE_LABEL_RULES:
        for term in terms:
            position = before.rfind(term)
            if position >= 0:
                preceding.append((position, len(term), date_type))
    if preceding:
        return max(preceding)[2]

    # 仅当前文没有标签时才使用日期后的标签作为弱回退。
    following = []
    if separator:
        for date_type, terms in DATE_LABEL_RULES:
            for term in terms:
                position = after.find(term)
                if position >= 0:
                    following.append((position, -len(term), date_type))
    if following:
        return min(following)[2]
    return "unspecified"


def _date_context(text: str, start: int, end: int) -> str:
    # 日期上下文必须限制在相邻日期之间，防止前一日期的标签污染后一日期。
    all_matches = [
        match
        for pattern in DATE_PATTERNS
        for match in pattern.finditer(text)
    ]
    previous_ends = [match.end() for match in all_matches if match.end() <= start]
    next_starts = [match.start() for match in all_matches if match.start() >= end]
    before_start = max(max(previous_ends, default=0), start - 32)
    after_end = min(min(next_starts, default=len(text)), end + 24)
    before = text[before_start:start]
    after = text[end:after_end]
    return f"{before}⟦DATE⟧{after}"


def parse_lifecycle_announcement(text: str) -> ParsedLifecycleEvidence:
    """Extract textual clues only; extracted dates remain unverified legal evidence."""
    if not isinstance(text, str):
        raise TypeError("公告文本必须是字符串")
    if not text.strip():
        return ParsedLifecycleEvidence((), (), (), ())

    codes = tuple(sorted(set(CODE_RE.findall(text))))
    events = []
    terms = []
    for event_type, keywords in EVENT_RULES:
        hits = [keyword for keyword in keywords if keyword in text]
        if hits:
            events.append(event_type)
            terms.extend(hits)

    date_items = []
    seen = set()
    for pattern in DATE_PATTERNS:
        for match in pattern.finditer(text):
            value = _normalize_date(match)
            if not value:
                continue
            context = _date_context(text, match.start(), match.end())
            date_type = _date_type(context)
            key = (value, date_type)
            if key in seen:
                continue
            seen.add(key)
            date_items.append(DateEvidence(
                value=value,
                date_type=date_type,
                date_status="unknown",
                context=context.strip(),
            ))

    return ParsedLifecycleEvidence(
        security_codes=codes,
        event_types=tuple(events),
        date_evidence=tuple(sorted(date_items, key=lambda item: (item.value, item.date_type))),
        matched_terms=tuple(dict.fromkeys(terms)),
    )


def extract_security_codes(text: str) -> tuple[str, ...]:
    """Convenience helper returning distinct six-digit codes in source order."""
    if not isinstance(text, str):
        raise TypeError("公告文本必须是字符串")
    return tuple(dict.fromkeys(CODE_RE.findall(text)))
