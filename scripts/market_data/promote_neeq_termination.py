# 本脚本仅在明确提供 --execute、双候选ID、审核人和法律依据时晋升NEEQ正式终止挂牌事实。
"""CLI for explicit human-approved NEEQ termination promotion."""

import argparse
import json

from trendradar.market_data.storage import MarketStorage
from trendradar.market_data.neeq_termination_promotion import promote_neeq_termination


def build_parser():
    parser = argparse.ArgumentParser(
        description="显式晋升NEEQ终止挂牌正式事实；默认只检查参数，不执行写入。"
    )
    parser.add_argument("--db", default="output/market/market.db")
    parser.add_argument("--identity-candidate-id", required=True)
    parser.add_argument("--lifecycle-candidate-id", required=True)
    parser.add_argument("--effective-date", required=True)
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--legal-basis", required=True)
    parser.add_argument("--notes", default="")
    parser.add_argument("--execute", action="store_true",
                        help="明确允许写入正式NEEQ发行人主档和生命周期事件")
    return parser


def main():
    args = build_parser().parse_args()
    if not args.execute:
        print(json.dumps({
            "status": "dry_run",
            "message": "未提供 --execute，不会写入正式表；请先确认候选、日期和法律依据。",
            "formal_issuer_master_writes": 0,
            "formal_lifecycle_event_writes": 0,
        }, ensure_ascii=False, indent=2))
        return 0
    storage = MarketStorage(args.db)
    result = promote_neeq_termination(
        storage,
        identity_candidate_id=args.identity_candidate_id,
        lifecycle_candidate_id=args.lifecycle_candidate_id,
        effective_date=args.effective_date,
        reviewer=args.reviewer,
        legal_basis=args.legal_basis,
        notes=args.notes,
    )
    print(json.dumps({
        "database": str(storage.db_path),
        "promotion": result,
        "safety": {"explicit_execute": True, "automatic_promotion": False},
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
