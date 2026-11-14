# 本脚本只读采集NEEQ官方终止挂牌结果并将附件匹配结果写入待审核候选表，不写正式生命周期事实。
"""CLI for read-only NEEQ termination-result evidence ingestion."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trendradar.market_data.lifecycle_evidence_fetcher import LifecycleEvidenceFetcher
from trendradar.market_data.neeq_termination_result_pipeline import ingest_neeq_termination_result_pages
from trendradar.market_data.neeq_termination_result_provider import NEEQTerminationResultProvider
from trendradar.market_data.storage import MarketStorage


def main() -> int:
    parser = argparse.ArgumentParser(
        description="采集NEEQ官方终止挂牌结果及批量决定公告；只写入待审核候选表。"
    )
    parser.add_argument("--company-code", default="")
    parser.add_argument("--keyword", default="")
    parser.add_argument("--page", type=int, default=0)
    parser.add_argument("--max-pages", type=int, default=1)
    parser.add_argument("--max-records", type=int, default=100)
    parser.add_argument("--db", default="output/market/market.db")
    parser.add_argument("--timeout", type=float, default=25)
    args = parser.parse_args()
    if not 1 <= args.max_pages <= 100:
        parser.error("--max-pages 必须在1到100之间")
    if not 1 <= args.max_records <= 1000:
        parser.error("--max-records 必须在1到1000之间")
    provider = NEEQTerminationResultProvider(timeout_seconds=args.timeout)
    first = provider.fetch_page(page=args.page, company_code=args.company_code, keyword=args.keyword)
    pages = [first]
    for number in range(args.page + 1, min(first.total_pages, args.page + args.max_pages)):
        pages.append(provider.fetch_page(
            page=number, company_code=args.company_code, keyword=args.keyword,
        ))
    storage = MarketStorage(args.db)
    result = ingest_neeq_termination_result_pages(
        pages, storage=storage, fetcher=LifecycleEvidenceFetcher(timeout_seconds=args.timeout),
        max_records=args.max_records,
    )
    print(json.dumps({
        "database": str(storage.db_path),
        "page_summary": [{
            "page": p.page_number, "page_size": p.page_size,
            "total_elements": p.total_elements, "total_pages": p.total_pages,
            "response_sha256": p.response_sha256,
            "request_parameters": p.request_parameters,
            "request_body_sha256": p.request_body_sha256,
        } for p in pages],
        "ingestion": result,
        "safety": {
            "formal_issuer_master_writes": 0,
            "formal_lifecycle_writes": 0,
            "candidate_review_only": True,
        },
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
