# 本脚本定向查询NEEQ官方终止挂牌结果列表，保存原始JSONP响应和审计报告；不下载附件、不写数据库。
"""CLI for a targeted, read-only NEEQ termination-result audit."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="定向核验NEEQ官方终止挂牌结果；只保存原始接口响应和候选报告。")
    parser.add_argument("--security-codes", default="834502,834606,834904")
    parser.add_argument("--raw-response-dir", default="output/market/neeq_result_raw")
    parser.add_argument("--output", default="output/market/neeq_targeted_termination_result_audit_v1.json")
    parser.add_argument("--max-pages-per-security", type=int, default=20)
    args = parser.parse_args()

    from trendradar.market_data.neeq_targeted_result_audit import audit_targeted_neeq_termination_results
    from trendradar.market_data.neeq_termination_result_provider import NEEQTerminationResultProvider

    codes = [value.strip() for value in args.security_codes.split(",") if value.strip()]
    result = audit_targeted_neeq_termination_results(
        NEEQTerminationResultProvider(),
        security_codes=codes,
        raw_response_dir=args.raw_response_dir,
        max_pages_per_security=args.max_pages_per_security,
    )
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    print(json.dumps(result["safety"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
