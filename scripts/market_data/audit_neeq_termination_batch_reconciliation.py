# 本脚本读取NEEQ公告解析报告、名单覆盖审计和官方结果候选清单，生成只读批次交叉审计报告。
"""Build a read-only NEEQ termination batch reconciliation report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trendradar.market_data.neeq_termination_batch_reconciliation import (
    build_neeq_termination_batch_reconciliation,
)


def _load_json(path: str) -> dict:
    with Path(path).open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"JSON根节点必须是对象: {path}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(
        description="只读交叉审计NEEQ批次公告名单与官方终止结果候选清单，不写数据库。"
    )
    parser.add_argument(
        "--review-report", required=True,
        help="neeq_termination_notice_review_v8.json",
    )
    parser.add_argument(
        "--coverage-audit", required=True,
        help="neeq_notice_coverage_audit_v3.json",
    )
    parser.add_argument(
        "--candidate-manifest", required=True,
        help="neeq_termination_candidate_manifest_v2.json（支持导出器完整外层结构）",
    )
    parser.add_argument(
        "--output", default="output/market/neeq_termination_batch_reconciliation_v1.json",
    )
    args = parser.parse_args()

    result = build_neeq_termination_batch_reconciliation(
        _load_json(args.review_report),
        _load_json(args.coverage_audit),
        _load_json(args.candidate_manifest),
    )
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "status": "complete",
        "output": str(destination),
        "summary": result["summary"],
        "safety": result["safety"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
