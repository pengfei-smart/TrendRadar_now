# 本文件负责A股证券主数据源采集、字段规范化、全量快照安全校验与数据库增量UPSERT。
"""A-share security master ingestion, normalization and guarded refresh."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Iterable, Mapping, Protocol, Sequence

import requests

from trendradar.market_data.models import Security
from trendradar.market_data.security_master import normalize_security_code


EASTMONEY_LIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"
EASTMONEY_FIELDS = "f12,f14,f13,f15,f16,f17,f18,f19,f20,f21,f22,f23,f24,f25,f26"
EASTMONEY_MARKET_FILTER = "m:1+t:2,m:1+t:23,m:0+t:6,m:0+t:80,m:0+t:81"


class SecurityMasterSourceError(RuntimeError):
    """Raised when the upstream security-master source is unavailable or malformed."""


class SecurityMasterProvider(Protocol):
    def fetch_all(self) -> Sequence[Security]: ...


@dataclass(frozen=True)
class SecurityMasterRefreshResult:
    source: str
    fetched_count: int
    normalized_count: int
    risk_count: int
    rejected_count: int
    updated_count: int


def infer_exchange(code: str, market_id: str = "") -> str:
    """Infer exchange conservatively from canonical A-share code prefixes."""
    value = normalize_security_code(code)
    if value.startswith(("4", "8", "9")):
        return "BJ"
    if value.startswith("6"):
        return "SH"
    if value.startswith(("0", "3")):
        return "SZ"
    return {"1": "SH", "0": "SZ", "2": "BJ"}.get(str(market_id).strip(), "")


def infer_board(code: str, exchange: str) -> str:
    value = normalize_security_code(code)
    if exchange == "BJ":
        return "北交所"
    if exchange == "SH":
        return "科创板" if value.startswith("688") else "沪主板"
    if exchange == "SZ":
        return "创业板" if value.startswith(("300", "301")) else "深主板"
    return "未知"


def is_risk_warning_name(name: str) -> bool:
    """Recognize risk warning labels without filtering unrelated names like 明星电力."""
    normalized = re.sub(r"\s+", "", str(name)).upper()
    return normalized.startswith(("*ST", "ST", "S*ST", "SST", "退市ST", "退"))


def normalize_eastmoney_record(record: Mapping[str, object]) -> Security:
    """Convert Eastmoney clist fields to the canonical Security model."""
    code = normalize_security_code(str(record.get("f12", "")))
    name = str(record.get("f14", "")).strip()
    if not name:
        raise ValueError(f"证券名称缺失: {code}")
    exchange = infer_exchange(code, str(record.get("f13", "")))
    if not exchange:
        raise ValueError(f"无法判定证券交易所: {code}")
    status_raw = str(record.get("listing_status", "active")).strip().lower()
    status_aliases = {
        "active": "active", "上市": "active",
        "suspended": "suspended", "停牌": "suspended",
        "delisted": "delisted", "退市": "delisted",
        "pending": "pending", "待上市": "pending",
    }
    status = status_aliases.get(status_raw, "unknown")
    risk = bool(record.get("risk_flag", False)) or is_risk_warning_name(name)
    return Security(
        code=code,
        name=name,
        exchange=exchange,
        listing_status=status,
        risk_flag=risk,
        sectors=(),
    )


class EastmoneySecurityMasterProvider:
    """Paginated A-share list via public unofficial Eastmoney endpoint (no SLA implied)."""

    source_name = "eastmoney_clist"

    def __init__(
        self,
        session: requests.Session | None = None,
        page_size: int = 500,
        timeout: tuple[float, float] = (5.0, 20.0),
        max_pages: int = 200,
    ):
        if page_size < 1 or max_pages < 1:
            raise ValueError("page_size和max_pages必须为正数")
        self.session = session or requests.Session()
        self.page_size = page_size
        self.timeout = timeout
        # 接口可能静默缩小单页返回量；页数上限按实际页容量动态计算。
        # 200页可覆盖当前全市场规模并留有增长余量，超过上限仍拒绝不完整快照。
        self.max_pages = max_pages

    def _fetch_page(self, page: int) -> tuple[int, list[Mapping[str, object]]]:
        params = {
            "pn": str(page), "pz": str(self.page_size), "po": "1", "np": "1",
            "fltt": "2", "invt": "2", "fid": "f12",
            "fs": EASTMONEY_MARKET_FILTER, "fields": EASTMONEY_FIELDS,
        }
        try:
            response = self.session.get(
                EASTMONEY_LIST_URL, params=params, timeout=self.timeout,
                headers={"User-Agent": "TrendRadar-market-data/1.0"},
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise SecurityMasterSourceError(f"Eastmoney证券列表请求失败(page={page}): {exc}") from exc

        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict) or not isinstance(data.get("diff"), list):
            raise SecurityMasterSourceError(f"Eastmoney响应结构异常(page={page})")
        try:
            total_count = int(data.get("total") or data.get("totalCount"))
        except (TypeError, ValueError) as exc:
            raise SecurityMasterSourceError(f"Eastmoney缺少有效total(page={page})") from exc
        records = data["diff"]
        if not all(isinstance(item, dict) for item in records):
            raise SecurityMasterSourceError(f"证券列表包含非对象记录(page={page})")
        return total_count, records

    def fetch_all(self) -> Sequence[Security]:
        first_total, first_page = self._fetch_page(1)
        if first_total <= 0 or not first_page:
            raise SecurityMasterSourceError("Eastmoney返回空证券列表，拒绝更新主数据")
        # 以第一页实际返回条数作为有效页容量，兼容上游对pz参数的静默限幅。
        effective_page_size = len(first_page)
        page_count = (first_total + effective_page_size - 1) // effective_page_size
        if page_count > self.max_pages:
            raise SecurityMasterSourceError(
                f"证券列表按实际每页{effective_page_size}条需要{page_count}页，"
                f"超过max_pages={self.max_pages}，拒绝不完整快照"
            )
        records: list[Mapping[str, object]] = list(first_page)
        for page in range(2, page_count + 1):
            total, page_records = self._fetch_page(page)
            if total != first_total:
                raise SecurityMasterSourceError("分页采集中上游总数变化，快照不一致")
            expected_count = min(effective_page_size, first_total - len(records))
            if len(page_records) != expected_count:
                raise SecurityMasterSourceError(
                    f"第{page}页返回数量异常：预期{expected_count}条，实际{len(page_records)}条"
                )
            records.extend(page_records)
        if len(records) < first_total:
            raise SecurityMasterSourceError(f"分页结果不完整：获取{len(records)}条，上游总数{first_total}")
        normalized: list[Security] = []
        for item in records:
            try:
                normalized.append(normalize_eastmoney_record(item))
            except ValueError as exc:
                raise SecurityMasterSourceError(f"证券字段规范化失败: {exc}") from exc
        return normalized


class SecurityMasterUpdater:
    """Validate a complete provider snapshot before one guarded database UPSERT."""

    def __init__(
        self,
        provider: SecurityMasterProvider,
        storage: object,
        source_name: str = "eastmoney_clist",
        min_expected_count: int = 1000,
        max_count_drop_ratio: float = 0.20,
    ):
        if min_expected_count < 1:
            raise ValueError("min_expected_count必须为正数")
        if not 0 < max_count_drop_ratio < 1:
            raise ValueError("max_count_drop_ratio必须在0和1之间")
        self.provider = provider
        self.storage = storage
        self.source_name = source_name
        self.min_expected_count = min_expected_count
        self.max_count_drop_ratio = max_count_drop_ratio

    @staticmethod
    def _normalize_snapshot(items: Iterable[Security]) -> list[Security]:
        normalized: list[Security] = []
        seen: set[str] = set()
        for item in items:
            code = normalize_security_code(item.code)
            if code in seen:
                raise SecurityMasterSourceError(f"证券主数据重复代码: {code}")
            seen.add(code)
            name = item.name.strip()
            if not name:
                raise SecurityMasterSourceError(f"证券名称为空: {code}")
            exchange = item.exchange or infer_exchange(code)
            if exchange not in {"SH", "SZ", "BJ"}:
                raise SecurityMasterSourceError(f"证券交易所不合法: {code}/{exchange}")
            status = item.listing_status
            if status not in {"active", "suspended", "delisted", "pending", "unknown"}:
                raise SecurityMasterSourceError(f"上市状态不合法: {code}/{status}")
            normalized.append(Security(
                code=code, name=name, exchange=exchange, listing_status=status,
                risk_flag=bool(item.risk_flag) or is_risk_warning_name(name),
                sectors=item.sectors,
            ))
        return normalized

    def _existing_count(self) -> int:
        if hasattr(self.storage, "count_securities"):
            return int(self.storage.count_securities())
        db_path = getattr(self.storage, "db_path", None)
        if db_path:
            with sqlite3.connect(str(db_path), timeout=10) as connection:
                row = connection.execute("SELECT COUNT(*) FROM security_master").fetchone()
                return int(row[0]) if row else 0
        return 0

    def update(self) -> SecurityMasterRefreshResult:
        raw = list(self.provider.fetch_all())
        fetched_count = len(raw)
        if fetched_count < self.min_expected_count:
            raise SecurityMasterSourceError(
                f"证券主数据数量异常({fetched_count}<{self.min_expected_count})，拒绝写库"
            )
        normalized = self._normalize_snapshot(raw)
        if len(normalized) != fetched_count:
            raise SecurityMasterSourceError("规范化数量与源数据数量不一致，拒绝写库")

        existing_count = self._existing_count()
        if existing_count and len(normalized) < existing_count * (1 - self.max_count_drop_ratio):
            raise SecurityMasterSourceError(
                f"证券数量较本地减少超过{self.max_count_drop_ratio:.0%}，拒绝写库"
            )

        updated_count = int(self.storage.upsert_securities(normalized))
        return SecurityMasterRefreshResult(
            source=self.source_name, fetched_count=fetched_count,
            normalized_count=len(normalized),
            risk_count=sum(1 for item in normalized if item.risk_flag),
            rejected_count=0, updated_count=updated_count,
        )


def eligible_securities(securities: Iterable[Security]) -> list[Security]:
    """Keep active, non-risk-warning SH/SZ securities; BJ is excluded from radar candidates."""
    return [
        item for item in securities
        if item.exchange in {"SH", "SZ"}
        and item.listing_status == "active"
        and not item.risk_flag
    ]
