# 本脚本只读审计NEEQ官方终止挂牌结果名录分页覆盖和重复记录，不下载PDF、不写数据库。
"""CLI for bounded pagination coverage audit of NEEQ termination results."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trendradar.market_data.neeq_termination_result_audit import audit_neeq_termination_pages
from trendradar.market_data.neeq_termination_result_provider import NEEQTerminationResultProvider


def main() -> int:
    parser = argparse.ArgumentParser(description="只读审计NEEQ官方终止挂牌结果名录分页，不写数据库。")
    parser.add_argument("--company-code", default="")
    parser.add_argument("--keyword", default="")
    parser.add_argument("--start-page", type=int, default=0)
    parser.add_argument("--max-pages", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=25)
    args = parser.parse_args()
    if args.start_page < 0 or args.start_page > 100000:
        parser.error("--start-page 必须在0到100000之间")
    if not 1 <= args.max_pages <= 500:
        parser.error("--max-pages 必须在1到500之间")
    provider = NEEQTerminationResultProvider(timeout_seconds=args.timeout)
    first = provider.fetch_page(
        page=args.start_page, company_code=args.company_code, keyword=args.keyword,
        tolerate_invalid_records=True,
    )
    pages = [first]
    stop_page = min(first.total_pages, args.start_page + args.max_pages)
    for number in range(args.start_page + 1, stop_page):
        pages.append(provider.fetch_page(
            page=number, company_code=args.company_code, keyword=args.keyword,
            tolerate_invalid_records=True,
        ))
    audit = audit_neeq_termination_pages(pages, start_page=args.start_page)
    print(json.dumps({
        "source": "neeq_official_termination_result_list",
        "query": {"company_code": args.company_code, "keyword": args.keyword},
        "page_summaries": [{
            "page": page.page_number, "page_size": page.page_size,
            "total_elements": page.total_elements, "total_pages": page.total_pages,
            "response_sha256": page.response_sha256,
            "rejected_record_count": page.rejected_record_count,
            "record_validation_errors": list(page.record_validation_errors),
            "rejected_record_details": list(page.rejected_record_details),
        } for page in pages],
        "audit": audit,
        "safety": {"database_writes": 0, "pdf_downloads": 0, "read_only": True},
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
