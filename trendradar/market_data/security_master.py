# 本文件提供A股证券主数据规范化、更新校验和风险/停牌过滤，属于逐文件开发清单第八项。
"""A-share security master utilities."""

from typing import Iterable

from trendradar.market_data.models import Security


def normalize_security_code(code: str) -> str:
    """Normalize numeric A-share codes to six digits; reject malformed codes."""
    value = str(code).strip()
    if value.endswith((".SH", ".SZ", ".BJ")):
        value = value[:-3]
    if not value.isdigit() or len(value) > 6:
        raise ValueError(f"证券代码格式不合法：{code!r}")
    return value.zfill(6)


def validate_security_master(securities: Iterable[Security]) -> list[Security]:
    """Validate unique normalized codes and filter non-active/risk securities."""
    result: list[Security] = []
    seen: set[str] = set()
    for security in securities:
        code = normalize_security_code(security.code)
        if code in seen:
            raise ValueError(f"证券主数据出现重复代码：{code}")
        seen.add(code)
        if (
            security.listing_status == "active"
            and not security.risk_flag
            and security.exchange in {"SH", "SZ"}
            and not (security.exchange == "SH" and code.startswith(("688", "689")))
        ):
            result.append(Security(
                code=code,
                name=security.name.strip(),
                exchange=security.exchange,
                listing_status=security.listing_status,
                risk_flag=security.risk_flag,
                sectors=security.sectors,
            ))
    return result
