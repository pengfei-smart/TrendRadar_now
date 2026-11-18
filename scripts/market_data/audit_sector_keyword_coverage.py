"""Audit reviewed semantic keyword coverage across the current SW2 catalog.

Run:
    python scripts/market_data/audit_sector_keyword_coverage.py
    python scripts/market_data/audit_sector_keyword_coverage.py --db path/to/market.db
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from trendradar.market_data.sector_keyword_mapper import (
    build_industry_keyword_coverage_report,
)
from trendradar.market_data.storage import MarketStorage


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit Shenwan level-two industry keyword-rule coverage"
    )
    parser.add_argument(
        "--db",
        default="output/market/market.db",
        help="Market SQLite database path",
    )
    parser.add_argument(
        "--provider",
        default="akshare_sw",
        help="Industry catalog provider (default: akshare_sw)",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional JSON report path; stdout is always printed",
    )
    args = parser.parse_args()

    storage = MarketStorage(args.db)
    sectors = storage.load_industry_sectors(provider=args.provider)
    report = build_industry_keyword_coverage_report(sectors)
    report["provider"] = args.provider
    report["database"] = str(Path(args.db))
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered + "\n", encoding="utf-8")
    return 0 if report["total_level_two_sectors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
