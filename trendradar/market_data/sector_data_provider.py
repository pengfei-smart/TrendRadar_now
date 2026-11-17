"""Shenwan industry master and membership ingestion via optional AkShare.

The provider intentionally uses Level-1 and Level-2 industry constituents. This
keeps refresh volume bounded while giving the radar useful, hierarchical
industry coverage. Concept/theme boards are not mixed into this taxonomy.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
import time
from typing import Protocol, Sequence

from trendradar.market_data.security_master import normalize_security_code
from trendradar.market_data.security_master_provider import SecurityMasterSourceError

SW_SOURCE_URL = "https://akshare.akfamily.xyz/data/index/index.html"


@dataclass(frozen=True)
class SectorRecord:
    sector_code: str
    sector_name: str
    sector_type: str = "industry"
    provider: str = "akshare_sw"
    source_url: str = SW_SOURCE_URL
    source_updated_at: str = ""


@dataclass(frozen=True)
class SectorMembershipRecord:
    security_code: str
    sector_code: str
    valid_from: str = ""
    valid_to: str = ""
    source: str = "akshare_sw"
    source_url: str = SW_SOURCE_URL
    confidence: float = 1.0


@dataclass(frozen=True)
class SectorSnapshot:
    provider: str
    fetched_at: str
    sectors: tuple[SectorRecord, ...]
    memberships: tuple[SectorMembershipRecord, ...]


class SectorDataProvider(Protocol):
    def fetch_snapshot(self) -> SectorSnapshot: ...


def _text(value: object) -> str:
    if value is None:
        return ""
    # pandas NaN must not become the literal string "nan".
    raw = str(value).strip()
    return "" if raw.lower() in {"nan", "none", "nat"} else raw


def _industry_code(value: object) -> str:
    raw = _text(value)
    match = re.search(r"(\d{6})", raw)
    if not match:
        raise ValueError(f"申万行业代码不合法: {value!r}")
    return match.group(1)


def _industry_frame_rows(frame: object, *, code_column: str, name_column: str) -> list[dict]:
    if frame is None or not hasattr(frame, "iterrows") or not hasattr(frame, "columns"):
        raise SecurityMasterSourceError("AkShare行业分类响应不是DataFrame")
    columns = set(str(column) for column in frame.columns)
    if not {code_column, name_column}.issubset(columns):
        raise SecurityMasterSourceError(
            f"AkShare行业分类字段变化，缺少: {sorted({code_column, name_column} - columns)}"
        )
    rows = []
    for _, row in frame.iterrows():
        code = _industry_code(row[code_column])
        name = _text(row[name_column])
        if not name:
            raise SecurityMasterSourceError(f"申万行业名称为空: {code}")
        rows.append({"code": code, "name": name, "raw": row})
    if not rows:
        raise SecurityMasterSourceError("AkShare返回空行业分类")
    if len({item["code"] for item in rows}) != len(rows):
        raise SecurityMasterSourceError("申万行业分类包含重复行业代码")
    return rows


def _normalize_membership_date(value: object) -> str:
    raw = _text(value)
    if not raw:
        return ""
    iso_match = re.match(r"^(\\d{4})[-/.](\\d{2})[-/.](\\d{2})", raw)
    if iso_match:
        return "-".join(iso_match.groups())
    digits = re.sub(r"[^0-9]", "", raw)
    if len(digits) == 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:]}"
    # Do not invent a date if upstream changes its format.
    raise SecurityMasterSourceError(f"申万成分股纳入日期格式异常: {raw!r}")


class AkShareShenwanProvider:
    """Fetch SW Level-1/2 taxonomy and constituent memberships.

    AkShare is an optional dependency. Importing it lazily keeps the core radar
    and its offline tests usable without installing the large data package.
    """

    source_name = "akshare_sw"
    source_url = SW_SOURCE_URL

    def __init__(self, *, pause_seconds: float = 0.15, minimum_sectors: int = 20,
                 minimum_memberships: int = 1000):
        if pause_seconds < 0:
            raise ValueError("pause_seconds不能小于0")
        self.pause_seconds = pause_seconds
        self.minimum_sectors = minimum_sectors
        self.minimum_memberships = minimum_memberships

    def fetch_snapshot(self) -> SectorSnapshot:
        try:
            import akshare as ak
        except ImportError as exc:
            raise SecurityMasterSourceError(
                "缺少可选依赖AkShare，请执行: python -m pip install '.[market-data]'"
            ) from exc

        fetched_at = datetime.now(timezone.utc).isoformat()
        try:
            first = _industry_frame_rows(
                ak.sw_index_first_info(), code_column="行业代码", name_column="行业名称"
            )
            second_frame = ak.sw_index_second_info()
            second = _industry_frame_rows(
                second_frame, code_column="行业代码", name_column="行业名称"
            )
        except SecurityMasterSourceError:
            raise
        except Exception as exc:
            raise SecurityMasterSourceError(f"申万行业分类获取失败: {exc}") from exc

        sectors: dict[str, SectorRecord] = {}
        memberships: dict[tuple[str, str, str], SectorMembershipRecord] = {}
        for level, rows in ((1, first), (2, second)):
            for item in rows:
                key = f"SW{level}:{item['code']}"
                sectors[key] = SectorRecord(
                    sector_code=key, sector_name=item["name"],
                    source_updated_at=fetched_at,
                )

        # AkShare's sw_index_cons supports both Level-1 and Level-2 codes.
        # The Level-2 response carries parent name; we resolve it to the Level-1 code.
        first_by_name = {item["name"]: item["code"] for item in first}
        for level, rows in ((1, first), (2, second)):
            for industry in rows:
                symbol = industry["code"]
                try:
                    frame = ak.sw_index_cons(symbol=symbol)
                except Exception as exc:
                    raise SecurityMasterSourceError(
                        f"申万{level}级行业成分获取失败({symbol}): {exc}"
                    ) from exc
                required = {"stock_code"}
                if frame is None or not hasattr(frame, "columns") or not required.issubset(
                    set(str(column) for column in frame.columns)
                ):
                    raise SecurityMasterSourceError(
                        f"申万行业成分字段变化({symbol})，缺少stock_code"
                    )
                parent_code = ""
                if level == 2:
                    parent_name = _text(industry["raw"].get("上级行业", ""))
                    parent_code = first_by_name.get(parent_name, "")
                    if not parent_code:
                        raise SecurityMasterSourceError(
                            f"申万二级行业无法匹配一级行业: {industry['name']}/{parent_name}"
                        )
                sector_codes = [f"SW{level}:{symbol}"]
                if parent_code:
                    sector_codes.append(f"SW1:{parent_code}")
                for _, row in frame.iterrows():
                    try:
                        code = normalize_security_code(_text(row["stock_code"]))
                    except ValueError as exc:
                        raise SecurityMasterSourceError(
                            f"申万成分股证券代码非法({symbol}): {exc}"
                        ) from exc
                    start_date = _normalize_membership_date(row.get("start_date", ""))
                    for sector_code in sector_codes:
                        key = (code, sector_code, start_date)
                        memberships[key] = SectorMembershipRecord(
                            security_code=code, sector_code=sector_code,
                            valid_from=start_date,
                        )
                if self.pause_seconds:
                    time.sleep(self.pause_seconds)

        if len(sectors) < self.minimum_sectors:
            raise SecurityMasterSourceError(
                f"申万行业数量异常: {len(sectors)} < {self.minimum_sectors}"
            )
        unique_members = {item.security_code for item in memberships.values()}
        if len(memberships) < self.minimum_memberships or len(unique_members) < 500:
            raise SecurityMasterSourceError(
                "申万行业成分覆盖异常: "
                f"memberships={len(memberships)}, securities={len(unique_members)}"
            )
        return SectorSnapshot(
            provider=self.source_name, fetched_at=fetched_at,
            sectors=tuple(sectors.values()), memberships=tuple(memberships.values()),
        )


@dataclass(frozen=True)
class SectorRefreshResult:
    provider: str
    sector_count: int
    membership_count: int
    matched_security_count: int
    unmatched_security_count: int


class SectorDataUpdater:
    """Validate a complete sector snapshot and atomically replace one provider's data."""

    def __init__(self, provider: SectorDataProvider, storage: object,
                 minimum_sectors: int = 20, minimum_memberships: int = 1000,
                 maximum_unmatched_ratio: float = 0.05, minimum_matched_securities: int = 500):
        if minimum_sectors < 1 or minimum_memberships < 1:
            raise ValueError("行业和成员关系最小数量必须为正数")
        if not 0 <= maximum_unmatched_ratio < 1:
            raise ValueError("maximum_unmatched_ratio必须在[0,1)范围内")
        self.provider = provider
        self.storage = storage
        self.minimum_sectors = minimum_sectors
        self.minimum_memberships = minimum_memberships
        self.maximum_unmatched_ratio = maximum_unmatched_ratio
        self.minimum_matched_securities = minimum_matched_securities

    def update(self) -> SectorRefreshResult:
        snapshot = self.provider.fetch_snapshot()
        if not snapshot.sectors or len(snapshot.sectors) < self.minimum_sectors:
            raise SecurityMasterSourceError("行业快照为空或数量不足，拒绝写库")
        if not snapshot.memberships or len(snapshot.memberships) < self.minimum_memberships:
            raise SecurityMasterSourceError("行业成员快照为空或数量不足，拒绝写库")
        sector_codes = [item.sector_code for item in snapshot.sectors]
        if len(set(sector_codes)) != len(sector_codes):
            raise SecurityMasterSourceError("行业快照包含重复行业代码")
        if any(not item.sector_code or not item.sector_name.strip()
               or item.sector_type != "industry" for item in snapshot.sectors):
            raise SecurityMasterSourceError("行业快照包含非法行业记录")
        membership_keys = set()
        for item in snapshot.memberships:
            key = (item.security_code, item.sector_code, item.valid_from)
            if key in membership_keys:
                raise SecurityMasterSourceError(f"行业成员关系重复: {key}")
            membership_keys.add(key)
            if item.sector_code not in set(sector_codes):
                # Parent sector records are included in the same snapshot.
                raise SecurityMasterSourceError(f"成员关系引用未知行业: {item.sector_code}")
            if not re.fullmatch(r"\d{6}", item.security_code):
                raise SecurityMasterSourceError(f"行业成员证券代码非法: {item.security_code}")
            if not 0 <= item.confidence <= 1:
                raise SecurityMasterSourceError("行业成员置信度必须在0到1之间")

        with self.storage.connect() as connection:
            known = {
                row[0] for row in connection.execute(
                    "SELECT code FROM security_master WHERE exchange IN ('SH','SZ')"
                )
            }
            incoming_codes = {item.security_code for item in snapshot.memberships}
            matched = incoming_codes & known
            unmatched = incoming_codes - known
            unmatched_ratio = len(unmatched) / max(1, len(incoming_codes))
            if len(matched) < self.minimum_matched_securities or unmatched_ratio > self.maximum_unmatched_ratio:
                raise SecurityMasterSourceError(
                    "申万成员与本地证券主数据匹配异常: "
                    f"matched={len(matched)}, unmatched={len(unmatched)}, "
                    f"ratio={unmatched_ratio:.2%}"
                )

            # Only replace records owned by this provider. Other taxonomies survive.
            connection.execute(
                "DELETE FROM sector_membership WHERE source=?", (snapshot.provider,)
            )
            connection.execute(
                "DELETE FROM sector_master WHERE provider=?", (snapshot.provider,)
            )
            connection.executemany(
                """INSERT INTO sector_master
                (sector_code,sector_name,sector_type,provider,source_url,source_updated_at)
                VALUES(?,?,?,?,?,?)""",
                [(item.sector_code, item.sector_name, item.sector_type,
                  snapshot.provider, item.source_url, item.source_updated_at or snapshot.fetched_at)
                 for item in snapshot.sectors],
            )
            connection.executemany(
                """INSERT INTO sector_membership
                (security_code,sector_code,valid_from,valid_to,source,source_url,confidence)
                VALUES(?,?,?,'',?,?,?)""",
                [(item.security_code, item.sector_code, item.valid_from, snapshot.provider,
                  item.source_url, item.confidence)
                 for item in snapshot.memberships if item.security_code in known],
            )

        return SectorRefreshResult(
            provider=snapshot.provider, sector_count=len(snapshot.sectors),
            membership_count=sum(1 for item in snapshot.memberships if item.security_code in known),
            matched_security_count=len(matched), unmatched_security_count=len(unmatched),
        )
