# 本脚本执行NEEQ公告元数据探测及限量生命周期候选采集，只写待审核候选，不修改正式发行人或生命周期主表。
"""Run a bounded NEEQ lifecycle evidence ingestion into the standalone market database."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 支持从仓库根目录直接运行：python scripts/market_data/ingest_neeq_lifecycle.py
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trendradar.market_data.lifecycle_evidence_fetcher import LifecycleEvidenceFetcher
from trendradar.market_data.neeq_announcement_probe import probe_neeq_announcement_list
from trendradar.market_data.neeq_lifecycle_pipeline import ingest_neeq_lifecycle_probe_result
from trendradar.market_data.storage import MarketStorage


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="探测NEEQ官方公告列表并将少量PDF解析为人工审核候选；不会写正式主表。"
    )
    parser.add_argument("--start-date", required=True, help="公告开始日期 YYYY-MM-DD")
    parser.add_argument("--end-date", required=True, help="公告结束日期 YYYY-MM-DD")
    parser.add_argument("--page", type=int, default=0, help="NEEQ零起始页码，默认0")
    parser.add_argument("--max-announcements", type=int, default=5,
                        help="本次最多下载的生命周期公告数，默认5，范围1-100")
    parser.add_argument("--keyword", default="", help="官方公告关键词，可留空")
    parser.add_argument("--company-code", default="", help="可选六位NEEQ公司代码")
    parser.add_argument("--market-layer", choices=("0", "1", "3"), default="3",
                        help="官方页面市场层级参数，默认3（全部）")
    parser.add_argument("--notice-type", default="",
                        help="公告类型筛选值；此前实测组合为5，默认不筛选")
    parser.add_argument("--disclosure-type", default="",
                        help="披露类型筛选值；此前实测组合为5，默认不筛选")
    parser.add_argument("--omit-date-filter", action="store_true",
                        help="实验选项：不向接口提交日期筛选")
    parser.add_argument("--db", default="output/market/market.db",
                        help="独立市场数据库路径")
    parser.add_argument("--timeout", type=float, default=20.0,
                        help="单份公告附件下载超时秒数")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not 1 <= args.max_announcements <= 100:
        raise SystemExit("--max-announcements 必须在1到100之间")

    probe = probe_neeq_announcement_list(
        start_date=args.start_date,
        end_date=args.end_date,
        keyword=args.keyword,
        market_layer=args.market_layer,
        page=args.page,
        company_code=args.company_code,
        notice_type=args.notice_type,
        disclosure_type=args.disclosure_type,
        omit_date_filter=args.omit_date_filter,
    )
    storage = MarketStorage(args.db)
    fetcher = LifecycleEvidenceFetcher(timeout_seconds=args.timeout)
    ingestion = ingest_neeq_lifecycle_probe_result(
        probe,
        storage=storage,
        fetcher=fetcher,
        max_announcements=args.max_announcements,
    )
    output = {
        "probe": {
            "source_id": probe["source_id"],
            "status_code": probe["status_code"],
            "metadata_source_url": probe["metadata_source_url"],
            "response_sha256": probe["response_sha256"],
            "page_number": probe["page_number"],
            "page_size": probe["page_size"],
            "returned_count": probe["returned_count"],
            "total_elements": probe["total_elements"],
            "lifecycle_candidate_count": probe["lifecycle_candidate_count"],
            "classification_filter_verified": probe["classification_filter_verified"],
            "warnings": probe["warnings"],
        },
        "ingestion": ingestion,
        "database": str(storage.db_path),
        "safety": {
            "formal_issuer_master_writes": 0,
            "formal_lifecycle_writes": 0,
            "writes_are_review_candidates_only": True,
        },
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if not any(item.get("status") == "failed" for item in ingestion["items"]) else 2


if __name__ == "__main__":
    raise SystemExit(main())
