# 本脚本用于手动抽样验证东方财富公开批量行情接口，不参与日常新闻采集或自动推送。
"""Manually smoke-test the Eastmoney quote endpoint with a small A-share sample."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from trendradar.market_data.models import Security
from trendradar.market_data.quote_fetcher import EastmoneyBatchQuoteProvider, QuoteSourceError


def parse_codes(raw: str) -> list[str]:
    codes = list(dict.fromkeys(item.strip() for item in raw.split(",") if item.strip()))
    invalid = [code for code in codes if len(code) != 6 or not code.isdigit()]
    if invalid:
        raise ValueError(f"证券代码必须是六位数字: {', '.join(invalid)}")
    if not codes:
        raise ValueError("至少提供一个证券代码")
    return codes


def main() -> int:
    parser = argparse.ArgumentParser(description="东方财富批量行情真实接口抽样检查")
    parser.add_argument(
        "--codes",
        default="600000,000001,600519,000858,300750",
        help="逗号分隔的六位A股证券代码，默认覆盖沪深主板及创业板",
    )
    args = parser.parse_args()

    try:
        codes = parse_codes(args.codes)
    except ValueError as exc:
        parser.error(str(exc))

    securities = [
        Security(
            code=code,
            name=code,
            exchange="SH" if code.startswith("6") else ("BJ" if code.startswith(("4", "8", "9")) else "SZ"),
        )
        for code in codes
    ]

    started = time.monotonic()
    collected_at = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
    try:
        quotes = EastmoneyBatchQuoteProvider().fetch_quotes(securities)
    except (QuoteSourceError, Exception) as exc:
        print(json.dumps({
            "status": "failed",
            "source": "eastmoney_ulist",
            "requested_codes": codes,
            "collected_at": collected_at,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "error": str(exc),
        }, ensure_ascii=False, indent=2))
        return 1

    by_code = {quote.code: quote for quote in quotes}
    missing = [code for code in codes if code not in by_code]
    rows = [
        {
            "code": code,
            "price": by_code[code].price,
            "change_pct": by_code[code].change_pct,
            "turnover": by_code[code].turnover,
            "volume_ratio": by_code[code].volume_ratio,
            "provider_timestamp": by_code[code].timestamp,
            "delayed_or_unverified": by_code[code].delayed,
        }
        for code in codes if code in by_code
    ]
    result = {
        "status": "passed" if not missing else "partial",
        "source": "eastmoney_ulist",
        "requested_count": len(codes),
        "returned_count": len(rows),
        "coverage": round(len(rows) / len(codes), 4),
        "missing_codes": missing,
        "collected_at": collected_at,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "quotes": rows,
        "note": "接口为非官方公开接口；delayed_or_unverified=true，不代表交易所逐笔实时行情。",
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if rows else 1


if __name__ == "__main__":
    sys.exit(main())
