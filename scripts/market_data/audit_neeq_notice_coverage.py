# 本脚本读取v7公告审核报告和附件候选清单，生成只读关联覆盖审计。
"""CLI for read-only NEEQ notice-to-candidate coverage audit."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="审计NEEQ公告身份与候选附件关联覆盖，不写数据库。")
    parser.add_argument("--review-report", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    from trendradar.market_data.neeq_notice_coverage_audit import build_neeq_notice_coverage_audit

    report = json.loads(Path(args.review_report).read_text(encoding="utf-8-sig"))
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8-sig"))
    result = build_neeq_notice_coverage_audit(report, manifest)
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
