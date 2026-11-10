# 本脚本只读获取沪深交易所现行A股名单与Eastmoney证券主数据，并输出内存交叉核验报告，不写入数据库。
"""Audit exchange listing snapshots against Eastmoney security master without database writes."""

from __future__ import annotations

import json
from collections import Counter

from trendradar.market_data.listing_status_provider import (
    SSEMainBoardAProvider,
    SZSEBoardAProvider,
)
from trendradar.market_data.security_master_provider import EastmoneySecurityMasterProvider


def _sample(values: set[str], limit: int = 100) -> list[str]:
    return sorted(values)[:limit]


def main() -> int:
    sse = SSEMainBoardAProvider().fetch_snapshot()
    szse_main, chinext = SZSEBoardAProvider().fetch_snapshots()
    securities = list(EastmoneySecurityMasterProvider().fetch_all())

    official = {
        "SH_main_board_a": set(sse.active_codes),
        "SZ_main_board_a": set(szse_main.active_codes),
        "SZ_chinext": set(chinext.active_codes),
    }
    master_by_code = {item.code: item for item in securities}
    if len(master_by_code) != len(securities):
        duplicates = [code for code, count in Counter(x.code for x in securities).items() if count > 1]
        raise RuntimeError(f"Eastmoney主数据存在重复代码: {duplicates[:20]}")

    master_scope = {
        "SH_main_board_a": {
            x.code for x in securities
            if x.exchange == "SH" and x.code.startswith("60")
        },
        "SZ_main_board_a": {
            x.code for x in securities
            if x.exchange == "SZ" and x.code.startswith(("000", "001", "002", "003"))
        },
        "SZ_chinext": {
            x.code for x in securities
            if x.exchange == "SZ" and x.code.startswith(("300", "301", "302"))
        },
    }

    report = {
        "status": "completed",
        "write_performed": False,
        "security_master_source": "eastmoney_clist",
        "security_master_count": len(securities),
        "official_counts": {key: len(value) for key, value in official.items()},
        "security_master_scope_counts": {key: len(value) for key, value in master_scope.items()},
        "scope_comparison": {},
        "cross_board_duplicates": sorted(
            (official["SH_main_board_a"] & official["SZ_main_board_a"])
            | (official["SH_main_board_a"] & official["SZ_chinext"])
            | (official["SZ_main_board_a"] & official["SZ_chinext"])
        ),
        "security_master_exchange_counts": dict(Counter(x.exchange for x in securities)),
        "security_master_board_counts": dict(Counter(
            "BJ" if x.exchange == "BJ"
            else "STAR" if x.exchange == "SH" and x.code.startswith(("688", "689"))
            else "SH_MAIN" if x.exchange == "SH"
            else "CHINEXT" if x.exchange == "SZ" and x.code.startswith(("300", "301", "302"))
            else "SZ_MAIN" if x.exchange == "SZ"
            else "UNKNOWN"
            for x in securities
        )),
        "sse_snapshot_fetched_at": sse.fetched_at,
        "szse_snapshot_fetched_at": szse_main.fetched_at,
    }

    for scope, official_codes in official.items():
        master_codes = master_scope[scope]
        official_not_in_master = official_codes - master_codes
        master_not_in_official = master_codes - official_codes
        name_mismatches = []
        for code in sorted(official_codes & master_codes):
            official_name = ""
            # The exchange snapshots currently expose codes only; name comparison is unavailable here.
            if official_name:
                master_name = master_by_code[code].name
                if official_name != master_name:
                    name_mismatches.append({"code": code, "master_name": master_name})
        report["scope_comparison"][scope] = {
            "official_count": len(official_codes),
            "master_count": len(master_codes),
            "matched_count": len(official_codes & master_codes),
            "official_not_in_master_count": len(official_not_in_master),
            "official_not_in_master_sample": _sample(official_not_in_master),
            "master_not_in_official_count": len(master_not_in_official),
            "master_not_in_official_sample": _sample(master_not_in_official),
            "name_mismatch_count": len(name_mismatches),
            "name_mismatch_sample": name_mismatches[:100],
        }

    # Known delisting examples help confirm that the current list is a positive-membership source.
    known_delisted = {"600991", "601028", "601059", "601198", "601299", "601989", "603056"}
    report["known_delisted_presence"] = {
        code: {
            "in_security_master": code in master_by_code,
            "in_official_current_lists": any(code in values for values in official.values()),
        }
        for code in sorted(known_delisted)
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
