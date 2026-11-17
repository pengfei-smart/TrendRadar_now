"""Runnable entry point for one complete market-opportunity radar scan.

Run with:
    python -m trendradar.market_data.runner
    python -m trendradar.market_data.runner --refresh-universe
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Sequence

from trendradar.market_data.macro_event_fetcher import MacroEventFetcher
from trendradar.market_data.market_radar import MarketRadar
from trendradar.market_data.models import MarketEvent
from trendradar.market_data.quote_fetcher import EastmoneyBatchQuoteProvider, QuoteFetcher
from trendradar.market_data.relation_store import CrossMarketRelationStore
from trendradar.market_data.result_store import RadarResultStore
from trendradar.market_data.security_master_provider import (
    EastmoneySecurityMasterProvider,
    SecurityMasterUpdater,
)
from trendradar.market_data.storage import MarketStorage
from trendradar.market_data.us_event_fetcher import USEventFetcher
from trendradar.market_data.listing_status_provider import (
    ExchangeListingStatusUpdater,
)


def run_once(
    *,
    db_path: str = "output/market/market.db",
    events: Sequence[MarketEvent],
    quote_fetcher: object,
    run_type: str = "manual",
    max_candidates: int = 100,
    push_limit: int = 10,
) -> dict:
    """Run and persist one scan; dependencies are injectable for offline tests."""
    storage = MarketStorage(db_path)
    event_list = list(events)
    storage.save_events(event_list)
    securities = {item.code: item for item in storage.load_securities()}
    result_store = RadarResultStore(db_path)
    relation_store = CrossMarketRelationStore(db_path)
    radar = MarketRadar(
        quote_fetcher=quote_fetcher,
        max_candidates=max_candidates,
        push_limit=push_limit,
        relation_store=relation_store,
        result_store=result_store,
    )
    selected = radar.scan_and_persist(
        event_list,
        securities,
        run_type=run_type,
        source_status=f"events:loaded:{len(event_list)}",
        notes=f"events={len(event_list)}; securities={len(securities)}",
    )
    with result_store.connect() as connection:
        row = connection.execute(
            "SELECT run_id FROM radar_runs ORDER BY run_id DESC LIMIT 1"
        ).fetchone()
    if row is None:
        raise RuntimeError("Radar completed without persisting a run record")
    persisted = result_store.get_run(int(row["run_id"]))
    return {
        "status": persisted["run"]["status"],
        "run": persisted["run"],
        "event_count": len(event_list),
        "security_count": len(securities),
        "selected_count": len(selected),
        "candidates": persisted["candidates"],
    }


def _collect_events(limit_per_feed: int) -> list[MarketEvent]:
    events = [
        *USEventFetcher().fetch(limit_per_feed=limit_per_feed),
        *MacroEventFetcher().fetch(limit_per_feed=limit_per_feed),
    ]
    # Stable event IDs make this deterministic and avoid duplicate feed entries.
    unique: dict[str, MarketEvent] = {}
    for event in events:
        unique[event.event_id] = event
    return list(unique.values())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one TrendRadar market-opportunity scan")
    parser.add_argument("--db", default="output/market/market.db", help="Market SQLite path")
    parser.add_argument("--report", default="output/market/latest_radar_report.json",
                        help="JSON report output path")
    parser.add_argument("--events-limit", type=int, default=30)
    parser.add_argument("--max-candidates", type=int, default=100)
    parser.add_argument("--push-limit", type=int, default=10)
    parser.add_argument("--refresh-universe", action="store_true",
                        help="Refresh Eastmoney security master and official SH/SZ listing snapshots")
    args = parser.parse_args(argv)

    try:
        if args.refresh_universe:
            storage = MarketStorage(args.db)
            SecurityMasterUpdater(EastmoneySecurityMasterProvider(), storage).update()
            ExchangeListingStatusUpdater(storage).update()
        events = _collect_events(args.events_limit)
        quote_fetcher = QuoteFetcher([EastmoneyBatchQuoteProvider()])
        report = run_once(
            db_path=args.db,
            events=events,
            quote_fetcher=quote_fetcher,
            run_type="manual",
            max_candidates=args.max_candidates,
            push_limit=args.push_limit,
        )
        report["generated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({
            "status": report["status"],
            "run_id": report["run"]["run_id"],
            "event_count": report["event_count"],
            "security_count": report["security_count"],
            "candidate_count": report["run"]["candidate_count"],
            "report": str(report_path),
        }, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({
            "status": "failed",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
