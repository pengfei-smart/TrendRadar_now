# 本脚本从NEEQ历史证据登记册生成证券生命周期复核报告；不联网、不写数据库、不确认法律事实。
"""CLI for a read-only NEEQ lifecycle evidence review report."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="汇总NEEQ证券生命周期证据及缺口；只读，不确认法律事实。")
    parser.add_argument("--evidence-register", required=True)
    parser.add_argument("--security-codes", default="834502,834606,834904")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    from trendradar.market_data.neeq_lifecycle_case_review import build_neeq_lifecycle_case_review

    register = json.loads(Path(args.evidence_register).read_text(encoding="utf-8-sig"))
    codes = [value.strip() for value in args.security_codes.split(",") if value.strip()]
    result = build_neeq_lifecycle_case_review(register, security_codes=codes)
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    print(json.dumps(result["safety"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
