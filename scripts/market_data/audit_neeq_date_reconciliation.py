# 本脚本生成NEEQ日期差异与补充候选证据只读核验报告，不写入数据库。
"""CLI for NEEQ date reconciliation and supplemental candidate audit."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="生成NEEQ日期差异与补充候选证据核验报告，不写数据库。")
    parser.add_argument("--review-report", required=True)
    parser.add_argument("--coverage-audit", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    from trendradar.market_data.neeq_date_reconciliation_audit import build_neeq_date_reconciliation_audit

    review = json.loads(Path(args.review_report).read_text(encoding="utf-8-sig"))
    coverage = json.loads(Path(args.coverage_audit).read_text(encoding="utf-8-sig"))
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8-sig"))
    result = build_neeq_date_reconciliation_audit(review, coverage, manifest)
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
