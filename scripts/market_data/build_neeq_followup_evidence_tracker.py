# 本脚本根据NEEQ日期核验报告生成历史后续公告追踪清单；不联网下载、不写数据库。
"""CLI for building a NEEQ follow-up evidence tracking register."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

def main() -> int:
    parser = argparse.ArgumentParser(description="生成NEEQ后续公告证据追踪清单，不联网、不写数据库。")
    parser.add_argument("--reconciliation-report", required=True)
    parser.add_argument("--evidence-records", help="可选的人工整理证据元数据JSON数组")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    from trendradar.market_data.neeq_followup_evidence_tracker import build_neeq_followup_evidence_tracker
    reconciliation = json.loads(Path(args.reconciliation_report).read_text(encoding="utf-8-sig"))
    evidence = []
    if args.evidence_records:
        evidence = json.loads(Path(args.evidence_records).read_text(encoding="utf-8-sig"))
        if not isinstance(evidence, list):
            parser.error("--evidence-records必须是JSON数组")
    result = build_neeq_followup_evidence_tracker(reconciliation, evidence)
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\\n", encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
