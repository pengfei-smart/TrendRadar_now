# 本脚本根据人工整理的NEEQ历史证据元数据及本地原件生成SHA256核验登记表；不联网、不写数据库。
"""CLI for the NEEQ historical evidence register."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="登记NEEQ历史证据原件、SHA256及日期主张；只读，不确认法律事实。")
    parser.add_argument("--tracker-report", required=True)
    parser.add_argument("--evidence-records", required=True, help="人工整理的证据元数据JSON数组")
    parser.add_argument("--evidence-root", required=True, help="证据原件所在根目录")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    from trendradar.market_data.neeq_historical_evidence_register import build_neeq_historical_evidence_register

    tracker = json.loads(Path(args.tracker_report).read_text(encoding="utf-8-sig"))
    records = json.loads(Path(args.evidence_records).read_text(encoding="utf-8-sig"))
    if not isinstance(records, list):
        parser.error("--evidence-records必须是JSON数组")
    result = build_neeq_historical_evidence_register(
        tracker, records, evidence_root=args.evidence_root
    )
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    print(json.dumps(result["safety"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
