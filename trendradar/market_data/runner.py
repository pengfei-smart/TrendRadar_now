"""Runnable entry point for one complete market-opportunity radar scan.

Run with:
    python -m trendradar.market_data.runner
    python -m trendradar.market_data.runner --refresh-universe
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Sequence
_SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")

from trendradar.market_data.macro_event_fetcher import MacroEventFetcher
from trendradar.market_data.market_radar import MarketRadar
from trendradar.market_data.models import MarketEvent
from trendradar.market_data.quote_fetcher import EastmoneyBatchQuoteProvider, QuoteFetcher
from trendradar.market_data.relation_store import CrossMarketRelationStore
from trendradar.market_data.result_store import RadarResultStore
from trendradar.market_data.security_master_provider import (
    DataSourceStatusRecordingError,
    EastmoneySecurityMasterProvider,
    SecurityMasterUpdater,
)
from trendradar.market_data.storage import MarketStorage
from trendradar.market_data.database_lifecycle import (
    MarketDatabaseLifecycle,
)
from trendradar.market_data.us_event_fetcher import USEventFetcher
from trendradar.market_data.listing_status_provider import (
    ExchangeListingStatusUpdater,
)
from trendradar.market_data.sector_data_provider import (
    AkShareShenwanProvider, SectorDataUpdater,
)



def run_once(
    *,
    db_path: str = "output/market/market.db",
    events: Sequence[MarketEvent],
    quote_fetcher: object,
    run_type: str = "manual",
    max_candidates: int = 100,
    push_limit: int = 10,
    storage: MarketStorage | None = None,
) -> dict:
    """Run and persist one scan; dependencies are injectable for offline tests."""
    if storage is None:
        storage = MarketStorage(db_path)
    else:
        expected_path = Path(db_path).resolve()
        actual_path = Path(storage.db_path).resolve()

        if expected_path != actual_path:
            raise ValueError(
                "db_path must match the injected MarketStorage database path"
            )
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
    selected, radar_run_id = radar.scan_and_persist_with_run(
        event_list,
        securities,
        run_type=run_type,
        source_status=f"events:loaded:{len(event_list)}",
        notes=f"events={len(event_list)}; securities={len(securities)}",
    )

    persisted = result_store.get_run(radar_run_id)
    if persisted is None:
        raise RuntimeError(
            f"Radar run {radar_run_id} completed without a persisted record"
        )
    return {
        "status": persisted["run"]["status"],
        "run": persisted["run"],
        "event_count": len(event_list),
        "security_count": len(securities),
        "selected_count": len(selected),
        "candidates": persisted["candidates"],
    }




def _close_resource_safely(resource: object, label: str) -> None:
    """Best-effort cleanup: resource cleanup must not override business results."""
    close = getattr(resource, "close", None)
    if not callable(close):
        return

    try:
        close()
    except Exception as exc:
        print(
            f"[Market Cleanup] {label} 关闭失败: "
            f"{type(exc).__name__}: {exc}"
        )


def _refresh_security_master(storage: MarketStorage) -> object:
    """Refresh security master and always close the locally owned Provider."""
    provider = EastmoneySecurityMasterProvider()
    try:
        return SecurityMasterUpdater(provider, storage).update()
    finally:
        _close_resource_safely(provider, "SecurityMasterProvider")


def _refresh_listing_status(storage: MarketStorage) -> object:
    """Refresh listing status and always close the locally owned Updater."""
    updater = ExchangeListingStatusUpdater(storage)
    try:
        return updater.update()
    finally:
        _close_resource_safely(updater, "ExchangeListingStatusUpdater")


def _refresh_universe_if_due(storage: MarketStorage) -> dict:
    """Refresh enabled security sources independently when due."""
    results = {}

    refresh_tasks = (
        (
            "security_master",
            "eastmoney_security_master",
            lambda: _refresh_security_master(storage),
        ),
        (
            "listing_status",
            "exchange_listing_status",
            lambda: _refresh_listing_status(storage),
        ),
    )

    for task_name, source_id, refresh in refresh_tasks:
        try:
            source = storage.get_data_source(source_id)

            # A registered but disabled source must not be refreshed.
            # An unregistered source remains eligible for its first refresh.
            if source is not None and not bool(source.get("enabled", True)):
                results[task_name] = {
                    "status": "skipped",
                    "reason": "disabled",
                }
                continue

            if not storage.is_data_source_refresh_due(
                source_id,
                interval_hours=24,
            ):
                results[task_name] = {
                    "status": "skipped",
                    "reason": "not_due",
                }
                continue

            refresh()
            results[task_name] = {"status": "success"}

        except DataSourceStatusRecordingError as exc:
            # 业务数据已经成功写入，只是数据源成功状态记录失败。
            # 不应将已完成的业务刷新误报为失败。
            results[task_name] = {
                "status": "partial",
                "business_data_status": "success",
                "status_recording_status": "failed",
                "error_type": type(exc).__name__,
                "error": str(exc),
            }

        except Exception as exc:
            results[task_name] = {
                "status": "failed",
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
    return results


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
    """CLI entrypoint with exclusive market database lifecycle locking."""
    parser = argparse.ArgumentParser(
        description="Run one TrendRadar market-opportunity scan"
    )
    parser.add_argument(
        "--db",
        default="output/market/market.db",
        help="Market SQLite path",
    )
    parser.add_argument(
        "--report",
        default="output/market/latest_radar_report.json",
        help="JSON report output path",
    )
    parser.add_argument("--events-limit", type=int, default=30)
    parser.add_argument("--max-candidates", type=int, default=100)
    parser.add_argument("--push-limit", type=int, default=10)
    parser.add_argument(
        "--refresh-universe",
        action="store_true",
        help="Refresh security master, SH/SZ listing snapshots, and Shenwan industries",
    )
    parser.add_argument(
        "--refresh-sectors",
        action="store_true",
        help="Refresh Shenwan Level-1/2 industry master and memberships only",
    )

    args = parser.parse_args(argv)

    lifecycle: MarketDatabaseLifecycle | None = None
    lifecycle_started = False
    quote_fetcher: QuoteFetcher | None = None

    try:
        # All Runner modes share the same database lock as the market workflow.
        lifecycle = MarketDatabaseLifecycle(
            args.db,
            storage_mode="local",
        )

        start_result = lifecycle.start()
        lifecycle_started = True

        storage = lifecycle.storage
        if storage is None:
            raise RuntimeError(
                "Database lifecycle did not provide MarketStorage"
            )

        refresh_results = None

        if args.refresh_universe or args.refresh_sectors:
            if args.refresh_universe:
                # Explicit refresh remains forced and fails fast.
                _refresh_security_master(storage)
                _refresh_listing_status(storage)

            sector_result = SectorDataUpdater(
                AkShareShenwanProvider(),
                storage,
            ).update()

            if args.refresh_sectors and not args.refresh_universe:
                # Sector-only mode does not run the radar scan.
                finish_result = lifecycle.finish()
                lifecycle_started = False

                print(json.dumps({
                    "status": "success",
                    "provider": sector_result.provider,
                    "sector_count": sector_result.sector_count,
                    "membership_count": sector_result.membership_count,
                    "matched_security_count": sector_result.matched_security_count,
                    "unmatched_security_count": sector_result.unmatched_security_count,
                    "excluded_security_count": sector_result.excluded_security_count,
                    "database": finish_result,
                }, ensure_ascii=False, indent=2))

                return 0

        else:
            # Normal runs refresh each universe source independently when due.
            refresh_results = _refresh_universe_if_due(storage)

            print(json.dumps({
                "universe_refresh": refresh_results,
            }, ensure_ascii=False, indent=2))

        # Event collection and radar execution remain inside the lock.
        events = _collect_events(args.events_limit)
        quote_fetcher = QuoteFetcher(
            [EastmoneyBatchQuoteProvider()]
        )

        report = run_once(
            db_path=args.db,
            events=events,
            quote_fetcher=quote_fetcher,
            run_type="manual",
            max_candidates=args.max_candidates,
            push_limit=args.push_limit,
            storage=storage,
        )

        report["generated_at"] = datetime.now(
            _SHANGHAI_TZ
        ).isoformat(timespec="seconds")

        if refresh_results is not None:
            report["universe_refresh"] = refresh_results

        # Persist all database changes before releasing exclusive ownership.
        finish_result = lifecycle.finish()
        lifecycle_started = False

        report["database"] = {
            "start": start_result,
            "finish": finish_result,
        }

        report_path = Path(args.report)

        try:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            print(json.dumps({
                "status": "partial",
                "database_status": "success",
                "report_status": "failed",
                "report": str(report_path),
                "error_type": type(exc).__name__,
                "error": str(exc),
            }, ensure_ascii=False, indent=2))
            return 1

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

    finally:
        # QuoteFetcher owns the Provider created for this runner invocation.
        if quote_fetcher is not None:
            _close_resource_safely(
                quote_fetcher,
                "QuoteFetcher",
            )

        # Covers failures after acquiring the lock, including refresh,
        # event collection, quote fetching and radar persistence failures.
        if lifecycle is not None and lifecycle_started:
            try:
                lifecycle.abort()
            except Exception as abort_error:
                print(json.dumps({
                    "status": "failed",
                    "stage": "database_abort",
                    "error_type": type(abort_error).__name__,
                    "error": str(abort_error),
                }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())