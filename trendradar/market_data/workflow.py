"""Independent orchestration entrypoint for the market opportunity radar."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any, Callable, Iterable, Sequence

from trendradar.market_data.database_lifecycle import MarketDatabaseLifecycle
from trendradar.market_data.event_review_pipeline import (
    review_persisted_market_event_with_runtime,
)
from trendradar.market_data.event_review_repository import EventReviewRepository
from trendradar.market_data.macro_event_fetcher import MacroEventFetcher
from trendradar.market_data.market_radar import MarketRadar
from trendradar.market_data.models import MarketEvent
from trendradar.market_data.quote_fetcher import (
    EastmoneyBatchQuoteProvider,
    QuoteFetcher,
)
from trendradar.market_data.relation_store import CrossMarketRelationStore
from trendradar.market_data.result_store import RadarResultStore
from trendradar.market_data.us_event_fetcher import USEventFetcher
from trendradar.market_data.workflow_context import MarketWorkflowContext
from trendradar.market_data.workflow_report import (
    MarketWorkflowReport,
    WorkflowStageResult,
)

logger = logging.getLogger(__name__)


_SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def _now() -> str:
    return datetime.now(_SHANGHAI_TZ).isoformat(timespec="seconds")


def _stage(
    report: MarketWorkflowReport,
    name: str,
    status: str,
    *,
    started_at: str,
    message: str = "",
    metrics: dict[str, Any] | None = None,
    error: Exception | None = None,
) -> None:
    report.add_stage(
        WorkflowStageResult(
            name=name,
            status=status,
            started_at=started_at,
            completed_at=_now(),
            message=message,
            metrics=metrics or {},
            error_type=type(error).__name__ if error else "",
            error_message=str(error) if error else "",
        )
    )


def _collect_events(
    us_fetcher: Any,
    macro_fetcher: Any,
    *,
    limit_per_feed: int,
) -> tuple[list[MarketEvent], list[dict[str, str]]]:
    """Collect and deduplicate events while isolating source failures."""
    collected: dict[str, MarketEvent] = {}
    errors: list[dict[str, str]] = []

    for source_name, fetcher in (
        ("us_events", us_fetcher),
        ("macro_events", macro_fetcher),
    ):
        try:
            events = fetcher.fetch(limit_per_feed=limit_per_feed)

            for event in events:
                if not isinstance(event, MarketEvent):
                    raise TypeError(
                        f"{source_name} returned a non-MarketEvent record"
                    )
                collected.setdefault(event.event_id, event)

        except Exception as exc:
            logger.exception("Market event collection failed: %s", source_name)
            errors.append({
                "source": source_name,
                "error_type": type(exc).__name__,
                "error_message": str(exc),
            })

    return list(collected.values()), errors


def _review_events(
    *,
    storage: Any,
    repository: EventReviewRepository,
    context: MarketWorkflowContext,
    events: Sequence[MarketEvent],
) -> dict[str, Any]:
    """Run guarded Event Review without blocking downstream market stages."""
    result = {
        "status": "success",
        "reviewed": 0,
        "skipped_adjudicated": 0,
        "failed": 0,
        "guard_denied": False,
        "errors": [],
    }

    if not events:
        result["status"] = "skipped"
        result["message"] = "No events to review"
        return result

    try:
        guard = context.get_ai_request_guard()
        if not guard.is_allowed():
            result.update(
                status="skipped",
                guard_denied=True,
                message="AI request is not authorized in the current time window",
            )
            return result
    except Exception as exc:
        result.update(
            status="skipped",
            guard_denied=True,
            message="AI authorization check failed; Event Review was skipped",
        )
        result["errors"].append({
            "error_type": type(exc).__name__,
            "error_message": str(exc),
        })
        return result

    # Runtime creation is deliberately delayed until AI authorization succeeds.
    try:
        runtime = context.get_event_review_runtime()
    except Exception as exc:
        result.update(
            status="partial",
            message="Event Review runtime could not be initialized",
        )
        result["failed"] = len(events)
        result["errors"].append({
            "error_type": type(exc).__name__,
            "error_message": str(exc),
        })
        return result

    for event in events:
        # Recheck authorization before every event. A window boundary may
        # be crossed while a batch is running.
        try:
            if not context.get_ai_request_guard().is_allowed():
                result["guard_denied"] = True
                result["message"] = (
                    "AI authorization ended during the review batch"
                )
                break
        except Exception as exc:
            result["guard_denied"] = True
            result["errors"].append({
                "event_id": event.event_id,
                "error_type": type(exc).__name__,
                "error_message": str(exc),
            })
            break

        try:
            history = repository.list_runs(event_id=event.event_id, limit=1)
            if history and getattr(history[0], "run_status", "") == "adjudicated":
                result["skipped_adjudicated"] += 1
                continue

            review_persisted_market_event_with_runtime(
                storage=storage,
                repository=repository,
                runtime=runtime,
                event_id=event.event_id,
            )
            result["reviewed"] += 1

        except Exception as exc:
            logger.exception(
                "Event Review failed for event %s",
                event.event_id,
            )
            result["failed"] += 1
            result["errors"].append({
                "event_id": event.event_id,
                "error_type": type(exc).__name__,
                "error_message": str(exc),
            })

    if result["failed"]:
        result["status"] = "partial"
    elif result["guard_denied"] and result["reviewed"]:
        result["status"] = "partial"
    elif result["guard_denied"]:
        result["status"] = "skipped"

    return result


def run_market_workflow(
    *,
    context: MarketWorkflowContext,
    us_fetcher: Any | None = None,
    macro_fetcher: Any | None = None,
    quote_fetcher: Any | None = None,
    limit_per_feed: int = 40,
    max_candidates: int = 100,
    push_limit: int = 10,
    run_type: str = "scheduled",
) -> dict[str, Any]:
    """Execute one independent market workflow run.

    Dependencies may be injected for offline tests. No AI API or R2 request
    is made unless the supplied context and runtime explicitly authorize it.
    """
    if not isinstance(context, MarketWorkflowContext):
        raise TypeError("context must be a MarketWorkflowContext")

    report = MarketWorkflowReport(
        run_id=context.run_id,
        run_type=run_type,
        started_at=_now(),
    )

    lifecycle: MarketDatabaseLifecycle | None = None
    lifecycle_started = False
    quote_fetcher_owned = quote_fetcher is None

    try:
        # Strictly validate remote mode. Never silently downgrade remote to local.
        context.validate_storage_backend()

        configured_mode = context.storage_backend_mode
        if configured_mode == "auto":
            storage_mode = (
                "remote"
                if context.remote_database_backend is not None
                else "local"
            )
        else:
            storage_mode = configured_mode

        lifecycle = MarketDatabaseLifecycle(
            context.db_path,
            backend=context.remote_database_backend,
            storage_mode=storage_mode,
            run_id=context.run_id,
            expected_schema_version=14,
        )

        database_started_at = _now()
        start_result = lifecycle.start()
        lifecycle_started = True
        storage = lifecycle.storage

        if storage is None:
            raise RuntimeError("Database lifecycle did not provide MarketStorage")

        report.database["start"] = start_result
        _stage(
            report,
            "database_start",
            "success",
            started_at=database_started_at,
            metrics={"storage_mode": storage_mode},
        )

        # Stage 1: collect external events.
        collection_started_at = _now()
        us_fetcher = us_fetcher or USEventFetcher()
        macro_fetcher = macro_fetcher or MacroEventFetcher()

        events, collection_errors = _collect_events(
            us_fetcher,
            macro_fetcher,
            limit_per_feed=limit_per_feed,
        )

        if collection_errors:
            report.errors.extend(
                {
                    "stage": "event_collection",
                    **error,
                }
                for error in collection_errors
            )

        collection_status = (
            "partial" if collection_errors else "success"
        )
        if collection_errors and not events:
            collection_status = "failed"

        _stage(
            report,
            "event_collection",
            collection_status,
            started_at=collection_started_at,
            metrics={
                "event_count": len(events),
                "source_error_count": len(collection_errors),
            },
        )

        # Stage 2: persist collected events.
        persistence_started_at = _now()
        saved_count = storage.save_events(events)

        _stage(
            report,
            "event_persistence",
            "success",
            started_at=persistence_started_at,
            metrics={
                "collected_count": len(events),
                "saved_count": saved_count,
            },
        )

        # Stage 3: optional AI review.
        review_started_at = _now()
        repository = EventReviewRepository(storage)

        review_result = _review_events(
            storage=storage,
            repository=repository,
            context=context,
            events=events,
        )

        report.metadata["event_review"] = review_result

        _stage(
            report,
            "event_review",
            review_result["status"],
            started_at=review_started_at,
            message=review_result.get("message", ""),
            metrics={
                "reviewed": review_result["reviewed"],
                "skipped_adjudicated": review_result["skipped_adjudicated"],
                "failed": review_result["failed"],
                "guard_denied": review_result["guard_denied"],
            },
        )

        if review_result["errors"]:
            report.errors.extend(
                {
                    "stage": "event_review",
                    **error,
                }
                for error in review_result["errors"]
            )

        # Stage 4: load the persisted security universe and scan.
        security_started_at = _now()
        securities = {
            item.code: item
            for item in storage.load_securities(eligible_only=True)
        }
        _stage(
            report,
            "security_universe",
            "success" if securities else "partial",
            started_at=security_started_at,
            metrics={"security_count": len(securities)},
            message=(
                "" if securities
                else "No persisted securities are available for scanning"
            ),
        )

        if quote_fetcher is None:
            quote_fetcher = QuoteFetcher(
                [EastmoneyBatchQuoteProvider()]
            )

        radar_started_at = _now()
        result_store = RadarResultStore(str(context.db_path))
        relation_store = CrossMarketRelationStore(str(context.db_path))

        radar = MarketRadar(
            quote_fetcher=quote_fetcher,
            max_candidates=max_candidates,
            push_limit=push_limit,
            relation_store=relation_store,
            result_store=result_store,
        )

        try:
            selected, radar_run_id = radar.scan_and_persist_with_run(
                events,
                securities,
                run_at=_now(),
                run_type=run_type,
            )

            persisted_run = result_store.get_run(radar_run_id)
            run_record = (
                persisted_run.get("run")
                if isinstance(persisted_run, dict)
                else None
            )

            radar_status = (
                run_record.get("status", "")
                if isinstance(run_record, dict)
                else ""
            )

            message = ""
            if radar_status not in {"success", "partial", "failed"}:
                radar_status = "partial"
                message = (
                    "Radar scan returned without a valid persisted run record"
                )

            metrics = {
                "selected_count": len(selected),
            }

            if isinstance(run_record, dict):
                metrics.update({
                    "run_id": run_record.get("run_id"),
                    "candidate_count": run_record.get("candidate_count", 0),
                    "pushed_count": run_record.get("pushed_count", 0),
                })

            _stage(
                report,
                "radar_scan",
                radar_status,
                started_at=radar_started_at,
                message=message,
                metrics=metrics,
            )

        except Exception as exc:
            logger.exception("Market radar scan failed")
            report.add_error(stage="radar_scan", error=exc)

            _stage(
                report,
                "radar_scan",
                "failed",
                started_at=radar_started_at,
                message="Quote collection or radar scan failed",
                error=exc,
            )

        # Stage 5: finish database persistence.
        database_finish_started_at = _now()

        try:
            finish_result = lifecycle.finish()
            lifecycle_started = False
            report.database["finish"] = finish_result

            _stage(
                report,
                "database_finish",
                "success",
                started_at=database_finish_started_at,
                metrics={
                    "persistence": finish_result.get("persistence", ""),
                },
            )

        except Exception as exc:
            # Record the precise failure stage before propagating.
            # The outer handler remains responsible for workflow failure
            # and lifecycle cleanup.
            _stage(
                report,
                "database_finish",
                "failed",
                started_at=database_finish_started_at,
                message="Database persistence could not be completed",
                error=exc,
            )
            raise

    except Exception as exc:
        logger.exception("Market workflow failed")
        report.add_error(stage="workflow", error=exc)

        _stage(
            report,
            "workflow",
            "failed",
            started_at=_now(),
            message="Market workflow terminated unexpectedly",
            error=exc,
        )

        if lifecycle is not None and lifecycle_started:
            try:
                lifecycle.abort()
            except Exception as abort_error:
                report.add_error(
                    stage="database_abort",
                    error=abort_error,
                )
            finally:
                lifecycle_started = False

    finally:
        if quote_fetcher_owned and quote_fetcher is not None:
            try:
                quote_fetcher.close()
            except Exception:
                logger.exception(
                    "Failed to close workflow-owned QuoteFetcher"
                )

        if lifecycle is not None and lifecycle_started:
            try:
                lifecycle.abort()
            except Exception as abort_error:
                report.add_error(
                    stage="database_abort",
                    error=abort_error,
                )

    report.finalize(completed_at=_now())
    return report.to_dict()


def main() -> int:
    """CLI entrypoint."""
    from trendradar.market_data.workflow_context import MarketWorkflowContext

    context = MarketWorkflowContext.from_config()
    report = run_market_workflow(context=context)

    print(json.dumps(report, ensure_ascii=False, indent=2))

    return 0 if report["status"] in {"success", "partial", "skipped"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
