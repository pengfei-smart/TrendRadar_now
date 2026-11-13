# 本脚本按日期分页扫描NEEQ公告，并在本地按证券代码/名称匹配；仅写人工审核候选，不确认正式生命周期事实。
"""Bounded NEEQ lifecycle announcement scan with local issuer identity matching."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trendradar.market_data.lifecycle_evidence_fetcher import LifecycleEvidenceFetcher
from trendradar.market_data.neeq_announcement_probe import probe_neeq_announcement_list
from trendradar.market_data.neeq_lifecycle_pipeline import ingest_neeq_lifecycle_probe_result
from trendradar.market_data.neeq_lifecycle_scanner import (
    lifecycle_records,
    match_neeq_announcement_records,
    page_coverage,
)
from trendradar.market_data.storage import MarketStorage


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="按日期分页读取NEEQ公告，在本地匹配证券身份；不会写正式主表。"
    )
    parser.add_argument("--start-date", required=True, help="公告开始日期 YYYY-MM-DD")
    parser.add_argument("--end-date", required=True, help="公告结束日期 YYYY-MM-DD")
    parser.add_argument("--target-code", action="append", default=[],
                        help="目标六位证券代码，可重复指定")
    parser.add_argument("--target-name", action="append", default=[],
                        help="目标公司名称或历史名称，可重复指定；仅精确规范化匹配")
    parser.add_argument("--max-pages", type=int, default=300,
                        help="最多扫描页数，默认300，范围1-500")
    parser.add_argument("--pause-seconds", type=float, default=0.3,
                        help="分页请求间隔秒数，默认0.3，范围0-5")
    parser.add_argument("--max-announcements", type=int, default=20,
                        help="最多下载并解析的匹配生命周期公告数，默认20，范围1-100")
    parser.add_argument("--db", default="output/market/market.db",
                        help="独立市场数据库路径")
    parser.add_argument("--timeout", type=float, default=20.0,
                        help="单份公告附件下载超时秒数")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not args.target_code and not args.target_name:
        raise SystemExit("至少提供一个 --target-code 或 --target-name")
    if any(len(code) != 6 or not code.isdigit() for code in args.target_code):
        raise SystemExit("--target-code 必须是六位数字")
    if not 1 <= args.max_pages <= 500:
        raise SystemExit("--max-pages 必须在1到500之间")
    if not 0 <= args.pause_seconds <= 5:
        raise SystemExit("--pause-seconds 必须在0到5之间")
    if not 1 <= args.max_announcements <= 100:
        raise SystemExit("--max-announcements 必须在1到100之间")

    storage = MarketStorage(args.db)
    fetcher = LifecycleEvidenceFetcher(timeout_seconds=args.timeout)
    page_audits = []
    aggregate = {
        "matched_announcement_records": 0,
        "matched_lifecycle_announcements": 0,
        "processed_count": 0,
        "identity_candidates_saved": 0,
        "lifecycle_candidates_saved": 0,
        "formal_master_writes": 0,
        "formal_lifecycle_writes": 0,
        "items": [],
    }
    total_elements = 0
    page_size = 0
    stop_reason = "max_pages_reached"

    for page in range(args.max_pages):
        probe = probe_neeq_announcement_list(
            start_date=args.start_date,
            end_date=args.end_date,
            page=page,
            # Deliberately omit companyCd and keyword: neither passed a positive control.
        )
        total_elements = int(probe["total_elements"])
        page_size = int(probe["page_size"] or 0)
        records = probe.get("records", [])
        matched = match_neeq_announcement_records(
            records,
            target_codes=args.target_code,
            target_names=args.target_name,
        )
        lifecycle = lifecycle_records(matched)
        remaining = args.max_announcements - aggregate["processed_count"]
        page_audit = {
            "page": page,
            "returned_count": probe["returned_count"],
            "total_elements": total_elements,
            "page_size": page_size,
            "response_sha256": probe["response_sha256"],
            "matched_company_records": len(matched),
            "matched_lifecycle_announcements": len(lifecycle),
            "matched_codes": sorted({str(row.get("companyCd") or "") for row in matched}),
            "matched_names": sorted({str(row.get("companyName") or "") for row in matched}),
        }
        if lifecycle and remaining > 0:
            selected = lifecycle[:remaining]
            page_probe = dict(probe)
            page_probe["lifecycle_candidates"] = selected
            ingested = ingest_neeq_lifecycle_probe_result(
                page_probe,
                storage=storage,
                fetcher=fetcher,
                max_announcements=len(selected),
            )
            page_audit["ingestion"] = ingested
            for key in (
                "processed_count", "identity_candidates_saved",
                "lifecycle_candidates_saved", "formal_master_writes",
                "formal_lifecycle_writes",
            ):
                aggregate[key] += int(ingested.get(key, 0))
            aggregate["items"].extend(ingested.get("items", []))
        page_audits.append(page_audit)
        aggregate["matched_announcement_records"] += len(matched)
        aggregate["matched_lifecycle_announcements"] += len(lifecycle)

        if aggregate["processed_count"] >= args.max_announcements:
            stop_reason = "max_announcements_reached"
            break
        if probe["returned_count"] == 0:
            stop_reason = "empty_page"
            break
        if page_size and (page + 1) * page_size >= total_elements:
            stop_reason = "all_pages_scanned"
            break
        if page_size and probe["returned_count"] < page_size:
            stop_reason = "short_page"
            break
        if args.pause_seconds:
            time.sleep(args.pause_seconds)

    coverage = page_coverage(total_elements, page_size, len(page_audits))
    if stop_reason not in {"all_pages_scanned", "empty_page", "short_page"} and coverage["coverage_status"] != "complete":
        coverage["coverage_status"] = "partial"
    output = {
        "source_id": "neeq",
        "scan": {
            "date_range": {"start": args.start_date, "end": args.end_date},
            "pause_seconds": args.pause_seconds,
            "target_codes": args.target_code,
            "target_names": args.target_name,
            "server_side_company_filters_used": False,
            "pages": page_audits,
            "page_count_scanned": len(page_audits),
            "stop_reason": stop_reason,
            "coverage": coverage,
        },
        "matching": {
            "method": "exact_security_code_or_exact_normalized_company_name",
            "fuzzy_matching": False,
            "matched_announcement_records": aggregate["matched_announcement_records"],
            "matched_lifecycle_announcements": aggregate["matched_lifecycle_announcements"],
        },
        "ingestion": aggregate,
        "database": str(storage.db_path),
        "safety": {
            "formal_issuer_master_writes": 0,
            "formal_lifecycle_writes": 0,
            "writes_are_review_candidates_only": True,
        },
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if not any(item.get("status") == "failed" for item in aggregate["items"]) else 2


if __name__ == "__main__":
    raise SystemExit(main())
