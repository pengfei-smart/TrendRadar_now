# 本文件负责候选A股批量行情获取、接口返回规范化、来源时间标记及主备行情源降级。
"""Batch A-share quote providers and a failover facade.

Eastmoney public endpoints are unofficial and do not provide a contractual SLA.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone\nfrom zoneinfo import ZoneInfo
from typing import Iterable, Mapping, Protocol, Sequence

import requests

from trendradar.market_data.models import QuoteSnapshot, Security
from trendradar.market_data.security_master import normalize_security_code


EASTMONEY_ULIST_URL = "https://push2.eastmoney.com/api/qt/ulist.np/get"
EASTMONEY_FIELDS = "f2,f3,f4,f5,f6,f8,f10,f12,f13,f14,f15,f16,f17,f18,f124"


class QuoteSourceError(RuntimeError):
    """Raised when a quote source cannot return a valid candidate snapshot."""


class QuoteProvider(Protocol):
    source_name: str

    def fetch_quotes(self, securities: Sequence[Security]) -> Sequence[QuoteSnapshot]: ...


def eastmoney_secid(security: Security) -> str:
    """Build Eastmoney secid for batch ulist requests."""
    code = normalize_security_code(security.code)
    if security.exchange == "SH" or code.startswith("6"):
        return f"1.{code}"
    # Eastmoney uses market id 0 for Shenzhen and BJ instruments in its quote list.
    return f"0.{code}"


def _parse_number(value: object, *, allow_zero: bool = True) -> float | None:
    if value is None or value == "" or value == "-" or value == "—":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        return None
    if not allow_zero and parsed == 0:
        return None
    return parsed


def _provider_timestamp(value: object, fallback: str) -> str:
    \"\"\"Convert provider Unix seconds/milliseconds to local ISO timestamp.\"\"\"
    parsed = _parse_number(value, allow_zero=False)
    if parsed is None:
        return fallback
    try:
        if parsed > 10_000_000_000:
            parsed /= 1000
        return datetime.fromtimestamp(parsed, timezone.utc).astimezone().isoformat(timespec=\"seconds\")
    except (OverflowError, OSError, ValueError):
        return fallback


def _diff_records(diff: object) -> list[Mapping[str, object]]:
    if isinstance(diff, list):
        values = diff
    elif isinstance(diff, dict):
        values = list(diff.values())
    else:
        raise QuoteSourceError("行情响应data.diff类型不合法")
    if not all(isinstance(item, dict) for item in values):
        raise QuoteSourceError("行情响应diff包含非对象记录")
    return values


class EastmoneyBatchQuoteProvider:
    """Fetch quotes for only the supplied candidate securities in bounded batches.

    This avoids whole-market pagination drift and makes request volume proportional
    to the event-driven candidate pool, not to all listed A-share securities.
    """

    source_name = "eastmoney_ulist"

    def __init__(
        self,
        session: requests.Session | None = None,
        batch_size: int = 100,
        timeout: tuple[float, float] = (5.0, 20.0),
    ):
        if batch_size < 1 or batch_size > 500:
            raise ValueError("batch_size必须在1到500之间")
        self.session = session or requests.Session()
        self.batch_size = batch_size
        self.timeout = timeout

    def _fetch_batch(self, secids: Sequence[str]) -> list[Mapping[str, object]]:
        params = {
            "fltt": "2",
            "secids": ",".join(secids),
            "fields": EASTMONEY_FIELDS,
        }
        try:
            response = self.session.get(
                EASTMONEY_ULIST_URL, params=params, timeout=self.timeout,
                headers={"User-Agent": "TrendRadar-market-data/1.0"},
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise QuoteSourceError(f"东方财富批量行情请求失败: {exc}") from exc

        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict) or "diff" not in data:
            raise QuoteSourceError("东方财富批量行情响应结构异常")
        return _diff_records(data.get("diff"))

    def fetch_quotes(self, securities: Sequence[Security]) -> Sequence[QuoteSnapshot]:
        by_code: dict[str, Security] = {}
        for security in securities:
            code = normalize_security_code(security.code)
            if code in by_code:
                # Duplicate input should not inflate result counts or requests.
                continue
            by_code[code] = security
        if not by_code:
            return []

        secid_to_code = {eastmoney_secid(item): code for code, item in by_code.items()}
        secids = list(secid_to_code)
        collected_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        output: list[QuoteSnapshot] = []
        seen: set[str] = set()

        for start in range(0, len(secids), self.batch_size):
            batch = secids[start:start + self.batch_size]
            records = self._fetch_batch(batch)
            for record in records:
                code_raw = str(record.get("f12", "")).strip()
                if not code_raw.isdigit():
                    continue
                code = normalize_security_code(code_raw)
                if code not in by_code:
                    continue
                if code in seen:
                    raise QuoteSourceError(f"行情响应重复证券代码: {code}")
                seen.add(code)
                price = _parse_number(record.get("f2"), allow_zero=False)
                change_pct = _parse_number(record.get("f3"))
                turnover = _parse_number(record.get("f6"))
                # f10 is volume ratio in the clist endpoint; ulist payloads can
                # omit it, so leave it unset unless supplied by the provider.
                volume_ratio = _parse_number(record.get("f10"))
                if price is None:
                    # zero/missing latest price may indicate suspension or a
                    # provider anomaly. It is omitted; callers can flag missing.
                    continue
                output.append(QuoteSnapshot(
                    code=code,
                    timestamp=_provider_timestamp(record.get("f124"), collected_at),
                    price=price,
                    change_pct=change_pct,
                    turnover=turnover,
                    volume_ratio=volume_ratio,
                    source=self.source_name,
                    delayed=True,
                ))

        if not output:
            raise QuoteSourceError("东方财富批量行情未返回可用候选报价")
        return output


class QuoteFetcher:
    """Primary/fallback orchestration; a source failure does not crash news collection."""

    def __init__(self, providers: Iterable[QuoteProvider]):
        self.providers = tuple(providers)
        if not self.providers:
            raise ValueError("至少需要配置一个行情provider")

    def fetch(self, securities: Sequence[Security]) -> tuple[list[QuoteSnapshot], str]:
        if not securities:
            return [], "empty_candidate_set"
        errors: list[str] = []
        for provider in self.providers:
            try:
                quotes = list(provider.fetch_quotes(securities))
                if not quotes:
                    raise QuoteSourceError("行情源返回零条候选报价")
                requested = {normalize_security_code(item.code) for item in securities}
                unexpected = [quote.code for quote in quotes if quote.code not in requested]
                if unexpected:
                    raise QuoteSourceError(f"行情源返回非候选证券: {unexpected[:5]}")
                return quotes, provider.source_name
            except (QuoteSourceError, requests.RequestException) as exc:
                errors.append(f"{provider.source_name}: {exc}")
        raise QuoteSourceError("所有行情源均失败；" + " | ".join(errors))
