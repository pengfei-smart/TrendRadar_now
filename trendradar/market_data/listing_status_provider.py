# 本文件负责从沪深交易所官方证券列表获取目标板块的现行上市名单，并严格校验响应完整性。
"""Official exchange listing-universe adapters.

These adapters establish positive evidence for currently listed securities only.
Absence from a current list is never interpreted as delisting.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from typing import Mapping, Sequence

import requests
from openpyxl import load_workbook

from trendradar.market_data.security_master_provider import SecurityMasterSourceError


SSE_LIST_URL = "https://query.sse.com.cn/sseQuery/commonQuery.do"
SZSE_LIST_URL = "https://www.szse.cn/api/report/ShowReport"
SSE_REFERER = "https://www.sse.com.cn/assortment/stock/list/share/"
SZSE_REFERER = "https://www.szse.cn/market/product/stock/list/index.html"


@dataclass(frozen=True)
class ListingStatusSnapshot:
    """A validated positive-membership snapshot from one official exchange board."""

    source_id: str
    source_url: str
    exchange: str
    board_scope: str
    fetched_at: str
    source_as_of: str
    active_codes: tuple[str, ...]


def _normalize_code(value: object) -> str:
    raw = str(value or "").strip()
    if raw.endswith(".0"):
        raw = raw[:-2]
    code = raw.zfill(6)
    if len(code) != 6 or not code.isdigit():
        raise ValueError(f"非法证券代码: {value!r}")
    return code


def _unique_codes(values: Sequence[object], *, minimum_count: int) -> tuple[str, ...]:
    codes = tuple(_normalize_code(value) for value in values)
    if len(codes) < minimum_count:
        raise SecurityMasterSourceError(
            f"交易所名单数量异常: {len(codes)} < {minimum_count}"
        )
    if len(set(codes)) != len(codes):
        raise SecurityMasterSourceError("交易所名单包含重复证券代码")
    return tuple(sorted(codes))


class SSEMainBoardAProvider:
    """Fetch the SSE official Main Board A-share list (positive active membership)."""

    source_id = "sse_main_board_a"
    board_scope = "main_board_a"

    def __init__(self, session: requests.Session | None = None,
                 timeout: tuple[float, float] = (5.0, 25.0),
                 minimum_count: int = 800):
        self.session = session or requests.Session()
        self.timeout = timeout
        self.minimum_count = minimum_count

    def fetch_snapshot(self) -> ListingStatusSnapshot:
        params = {
            "STOCK_TYPE": "1",
            "REG_PROVINCE": "",
            "CSRC_CODE": "",
            "STOCK_CODE": "",
            "sqlId": "COMMON_SSE_CP_GPJCTPZ_GPLB_GP_L",
            "COMPANY_STATUS": "2",
            "type": "inParams",
            "isPagination": "true",
            "pageHelp.cacheSize": "1",
            "pageHelp.beginPage": "1",
            "pageHelp.pageSize": "10000",
            "pageHelp.pageNo": "1",
            "pageHelp.endPage": "1",
        }
        try:
            response = self.session.get(
                SSE_LIST_URL, params=params, timeout=self.timeout,
                headers={"User-Agent": "TrendRadar-market-data/1.0",
                         "Referer": SSE_REFERER},
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise SecurityMasterSourceError(f"上交所主板A股名单请求失败: {exc}") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("result"), list):
            raise SecurityMasterSourceError("上交所名单响应结构异常")
        rows = payload["result"]
        page_help = payload.get("pageHelp")
        if not isinstance(page_help, dict):
            raise SecurityMasterSourceError("上交所响应缺少分页完整性信息")
        try:
            reported_total = int(page_help.get("total"))
        except (TypeError, ValueError) as exc:
            raise SecurityMasterSourceError("上交所响应缺少有效总记录数") from exc
        if reported_total != len(rows):
            raise SecurityMasterSourceError(
                f"上交所名单不完整：声明{reported_total}条，实际{len(rows)}条"
            )
        if not rows or not all(isinstance(row, Mapping) for row in rows):
            raise SecurityMasterSourceError("上交所名单为空或记录结构异常")
        codes: list[str] = []
        for row in rows:
            code = row.get("SECURITY_CODE") or row.get("COMPANY_CODE")
            if not code:
                raise SecurityMasterSourceError("上交所名单记录缺少证券代码")
            normalized = _normalize_code(code)
            if not normalized.startswith("60"):
                raise SecurityMasterSourceError(f"上交所主板名单出现非主板代码: {normalized}")
            codes.append(normalized)
        active_codes = _unique_codes(codes, minimum_count=self.minimum_count)
        return ListingStatusSnapshot(
            source_id=self.source_id, source_url=SSE_LIST_URL, exchange="SH",
            board_scope=self.board_scope,
            fetched_at=datetime.now(timezone.utc).isoformat(),
            source_as_of="", active_codes=active_codes,
        )


class SZSEBoardAProvider:
    """Fetch SZSE official A-share XLSX and split Main Board / ChiNext by board column."""

    source_id = "szse_a_share_list"

    def __init__(self, session: requests.Session | None = None,
                 timeout: tuple[float, float] = (5.0, 30.0),
                 minimum_main_count: int = 300,
                 minimum_chinext_count: int = 500):
        self.session = session or requests.Session()
        self.timeout = timeout
        self.minimum_main_count = minimum_main_count
        self.minimum_chinext_count = minimum_chinext_count

    def fetch_snapshots(self) -> tuple[ListingStatusSnapshot, ListingStatusSnapshot]:
        params = {"SHOWTYPE": "xlsx", "CATALOGID": "1110", "TABKEY": "tab1",
                  "random": "0.6935816432433362"}
        try:
            response = self.session.get(
                SZSE_LIST_URL, params=params, timeout=self.timeout,
                headers={"User-Agent": "TrendRadar-market-data/1.0",
                         "Referer": SZSE_REFERER},
            )
            response.raise_for_status()
            raw = response.content
            workbook = load_workbook(BytesIO(raw), read_only=True, data_only=True)
        except (requests.RequestException, OSError, ValueError, KeyError) as exc:
            raise SecurityMasterSourceError(f"深交所A股名单下载或解析失败: {exc}") from exc
        try:
            sheet = workbook.active
            # 深交所官方 XLSX 的工作表尺寸元数据可能错误地标为 A1:A1。
            # 只读模式会因此只暴露第一列，必须先重置尺寸再流式读取。
            sheet.reset_dimensions()
            rows = sheet.iter_rows(values_only=True)
            header = next(rows, None)
            if not header:
                raise SecurityMasterSourceError("深交所A股名单缺少表头")
            columns = {str(value).strip(): index for index, value in enumerate(header)
                       if value is not None}
            required = {"板块", "A股代码", "A股简称"}
            if not required.issubset(columns):
                raise SecurityMasterSourceError(
                    f"深交所A股名单字段变化，缺少: {sorted(required - set(columns))}"
                )
            main_codes: list[str] = []
            chinext_codes: list[str] = []
            for row in rows:
                code_value = row[columns["A股代码"]] if columns["A股代码"] < len(row) else None
                name_value = row[columns["A股简称"]] if columns["A股简称"] < len(row) else None
                board_value = row[columns["板块"]] if columns["板块"] < len(row) else None
                if code_value in (None, ""):
                    continue
                code = _normalize_code(code_value)
                board = str(board_value or "").strip()
                if board in {"主板", "中小板"}:
                    if not code.startswith(("000", "001", "002", "003")):
                        raise SecurityMasterSourceError(f"深交所主板出现异常代码: {code}")
                    main_codes.append(code)
                elif board == "创业板":
                    # 创业板常规号段为300/301；302132是深交所批准的代码变更特例，仍属创业板。
                    if not code.startswith(("300", "301", "302")):
                        raise SecurityMasterSourceError(f"创业板名单出现异常代码: {code}")
                    chinext_codes.append(code)
                else:
                    raise SecurityMasterSourceError(f"深交所A股名单出现未知板块: {board!r}")
                if not str(name_value or "").strip():
                    raise SecurityMasterSourceError(f"深交所名单证券简称缺失: {code}")
            main = _unique_codes(main_codes, minimum_count=self.minimum_main_count)
            chinext = _unique_codes(chinext_codes, minimum_count=self.minimum_chinext_count)
        finally:
            workbook.close()
        fetched_at = datetime.now(timezone.utc).isoformat()
        return (
            ListingStatusSnapshot(self.source_id, SZSE_LIST_URL, "SZ", "main_board_a",
                                  fetched_at, "", main),
            ListingStatusSnapshot(self.source_id, SZSE_LIST_URL, "SZ", "chinext",
                                  fetched_at, "", chinext),
        )


class ExchangeListingStatusUpdater:
    """Refresh verified active membership from all three exchange-board sources."""

    def __init__(self, storage: object, sse_provider: SSEMainBoardAProvider | None = None,
                 szse_provider: SZSEBoardAProvider | None = None):
        self.storage = storage
        self.sse_provider = sse_provider or SSEMainBoardAProvider()
        self.szse_provider = szse_provider or SZSEBoardAProvider()

    def update(self) -> dict[str, int]:
        # Fetch and validate every required board before writing any of them.
        snapshots = (
            self.sse_provider.fetch_snapshot(),
            *self.szse_provider.fetch_snapshots(),
        )
        expected = {("SH", "main_board_a"), ("SZ", "main_board_a"), ("SZ", "chinext")}
        actual = {(item.exchange, item.board_scope) for item in snapshots}
        if actual != expected:
            raise SecurityMasterSourceError("交易所上市名单板块覆盖不完整")
        batch = [{
            "source_id": snapshot.source_id,
            "exchange": snapshot.exchange,
            "board_scope": snapshot.board_scope,
            "fetched_at": snapshot.fetched_at,
            "source_as_of": snapshot.source_as_of,
            "statuses": {code: "active" for code in snapshot.active_codes},
            "source_url": snapshot.source_url,
        } for snapshot in snapshots]
        return self.storage.apply_listing_status_snapshots(batch)
