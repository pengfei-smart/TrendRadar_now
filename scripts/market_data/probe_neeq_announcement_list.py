# 本脚本对 NEEQ 公告列表接口执行单次只读探测，仅保存元数据和候选标题，不下载公告附件。
"""Probe the NEEQ announcement list once and save a bounded metadata report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from trendradar.market_data.neeq_announcement_probe import probe_neeq_announcement_list


def main() -> int:
    parser = argparse.ArgumentParser(description="单次只读探测 NEEQ 公告列表接口")
    parser.add_argument("--start-date", required=True, help="开始日期 YYYY-MM-DD")
    parser.add_argument("--end-date", required=True, help="结束日期 YYYY-MM-DD")
    parser.add_argument("--keyword", default="", help="可选关键词")
    parser.add_argument("--page", type=int, default=0, help="零起始页码，默认 0")
    parser.add_argument("--market-layer", choices=("0", "1", "3"), default="3")
    parser.add_argument("--omit-date-filter", action="store_true", help="对照实验：不向接口发送 startTime/endTime")
    parser.add_argument("--notice-type", default="", help="公告类型筛选值；仅使用已抓包核验的值")
    parser.add_argument("--disclosure-type", default="", help="披露类型筛选值；仅使用已抓包核验的值")
    args = parser.parse_args()

    try:
        result = probe_neeq_announcement_list(
            start_date=args.start_date,
            end_date=args.end_date,
            keyword=args.keyword,
            page=args.page,
            market_layer=args.market_layer,
            omit_date_filter=args.omit_date_filter,
            notice_type=args.notice_type,
            disclosure_type=args.disclosure_type,
        )
    except Exception as exc:
        print(json.dumps({
            "status": "failed",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }, ensure_ascii=False, indent=2))
        return 1

    report = {"status": "passed", **result}
    project_root = Path(__file__).resolve().parents[2]
    output_path = project_root / "output" / "market" / "neeq_announcement_list_probe.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({
        "status": report["status"],
        "http_method": result["http_method"],
        "status_code": result["status_code"],
        "content_type": result["content_type"],
        "request_count": result["request_count"],
        "request_page": result["request_page"],
        "date_filter_omitted": result["date_filter_omitted"],
        "notice_type": args.notice_type,
        "disclosure_type": args.disclosure_type,
        "page_number": result["page_number"],
        "page_size": result["page_size"],
        "returned_count": result["returned_count"],
        "total_elements": result["total_elements"],
        "lifecycle_candidate_count": result["lifecycle_candidate_count"],
        "classification_filter_verified": result["classification_filter_verified"],
        "response_sha256": result["response_sha256"],
        "output_file": str(output_path),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
