# 本脚本只读抓取NEEQ官方终止挂牌名录并导出审计基线及候选清单，不下载附件、不写数据库。
"""Fetch NEEQ termination result pages and export a review-only candidate manifest."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trendradar.market_data.neeq_termination_candidate_manifest import (
    build_neeq_termination_candidate_manifest,
)
from trendradar.market_data.neeq_termination_result_audit import audit_neeq_termination_pages
from trendradar.market_data.neeq_termination_result_provider import NEEQTerminationResultProvider


def main() -> int:
    parser = argparse.ArgumentParser(
        description="只读导出NEEQ终止挂牌候选清单；不下载公告附件、不写数据库。"
    )
    parser.add_argument("--start-page", type=int, default=0)
    parser.add_argument("--max-pages", type=int, default=500)
    parser.add_argument("--timeout", type=float, default=25)
    parser.add_argument("--output", default="", help="输出JSON文件；留空时打印到标准输出")
    args = parser.parse_args()
    if args.start_page != 0:
        parser.error("候选清单必须从第0页开始，才能建立完整审计基线")
    if not 1 <= args.max_pages <= 500:
        parser.error("--max-pages 必须在1到500之间")

    provider = NEEQTerminationResultProvider(timeout_seconds=args.timeout)
    first = provider.fetch_page(page=0, tolerate_invalid_records=True)
    pages = [first]
    stop_page = min(first.total_pages, args.max_pages)
    for number in range(1, stop_page):
        pages.append(provider.fetch_page(page=number, tolerate_invalid_records=True))

    audit = audit_neeq_termination_pages(pages, start_page=0)
    manifest = build_neeq_termination_candidate_manifest(pages, audit)
    output = {
        "source": "neeq_official_termination_result_list",
        "audit": audit,
        "manifest": manifest,
        "page_summaries": [{
            "page": page.page_number,
            "response_sha256": page.response_sha256,
            "record_count": len(page.records),
            "rejected_record_count": page.rejected_record_count,
            "record_validation_errors": list(page.record_validation_errors),
        } for page in pages],
        "safety": {
            "database_writes": 0,
            "attachment_downloads": 0,
            "formal_lifecycle_writes": 0,
            "read_only": True,
        },
    }
    rendered = json.dumps(output, ensure_ascii=False, indent=2)
    if args.output:
        destination = Path(args.output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
