"""Atomic persistence for AI event-review board execution history.

A review run and all of its evidence, assessments, adjudication and quality
result are committed in one SQLite transaction. The v12 tables are append-only:
this repository writes only terminal snapshots and never updates prior runs.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import uuid

from .event_chief_reviewer_agent import CHIEF_REVIEWER_PROMPT_VERSION
from .event_impact_rubric import EVENT_IMPACT_RUBRIC_VERSION
from .event_review_board_runner import EventReviewBoardExecutionResult
from .event_review_models import IMPACT_DIMENSIONS, REVIEWER_ROLES
from .event_review_quality_gate import (
    EventReviewQualityGateResult,
    evaluate_event_review_quality,
)
from .event_review_temporal import parse_aware_datetime
from .event_reviewer_agent import REVIEWER_PROMPT_VERSION
from .storage import MarketStorage


@dataclass(frozen=True)
class PersistedEventReviewRun:
    """Identifiers and deterministic quality outcome for a committed run."""

    review_run_id: str
    event_id: str
    run_status: str
    board_status: str
    quality_result: EventReviewQualityGateResult


def _json(value: object) -> str:
    """Canonical UTF-8-friendly JSON for stable audit records."""
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    )


def _dimension_scores(pairs: tuple[tuple[str, float | None], ...]) -> dict:
    return {name: score for name, score in pairs}


def _validate_timestamp(value: str, field: str) -> datetime:
    parsed = parse_aware_datetime(value)
    if parsed is None:
        raise ValueError(f"{field} must be an ISO-8601 timestamp with timezone")
    return parsed


def _execution_errors(execution: EventReviewBoardExecutionResult) -> tuple[str, str]:
    codes: list[str] = []
    messages: list[str] = []
    for result in execution.reviewer_results:
        if result.error_code:
            codes.append(result.error_code)
        if result.error_message:
            messages.append(result.error_message)
    if execution.chief_result is not None:
        if execution.chief_result.error_code:
            codes.append(execution.chief_result.error_code)
        if execution.chief_result.error_message:
            messages.append(execution.chief_result.error_message)
    if execution.status == "ready_for_adjudication":
        codes.append("chair_not_run")
        messages.append("四位审查员已完成，但尚未形成首席审查官裁决")
    return ";".join(dict.fromkeys(codes)), " | ".join(dict.fromkeys(messages))[:4000]


class EventReviewRepository:
    """Persist one immutable review-board execution snapshot atomically."""

    def __init__(self, storage: MarketStorage):
        if not isinstance(storage, MarketStorage):
            raise TypeError("storage must be a MarketStorage instance")
        self.storage = storage

    def save_run(
        self,
        *,
        execution: EventReviewBoardExecutionResult,
        event_payload: dict,
        started_at: str,
        completed_at: str,
        review_run_id: str | None = None,
    ) -> PersistedEventReviewRun:
        if not isinstance(execution, EventReviewBoardExecutionResult):
            raise TypeError("execution must be an EventReviewBoardExecutionResult")
        if not isinstance(event_payload, dict):
            raise TypeError("event_payload must be a dictionary")
        started = _validate_timestamp(started_at, "started_at")
        completed = _validate_timestamp(completed_at, "completed_at")
        if completed < started:
            raise ValueError("completed_at must not be earlier than started_at")

        run_id = review_run_id or str(uuid.uuid4())
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("review_run_id must be a non-empty string")
        run_id = run_id.strip()

        board = execution.board
        quality = evaluate_event_review_quality(board, event_payload=event_payload)
        status_map = {
            "incomplete": "incomplete",
            "chief_error": "chief_error",
            "adjudicated": "adjudicated",
            "ready_for_adjudication": "failed",
        }
        try:
            run_status = status_map[execution.status]
        except KeyError as exc:
            raise ValueError(f"unsupported review execution status: {execution.status}") from exc

        reviewer_models = sorted({
            item.assessment.model_name
            for item in execution.reviewer_results
            if item.assessment.model_name
        })
        chief_model = (
            board.adjudication.chair_model_name
            if board.adjudication is not None else ""
        )
        error_code, error_message = _execution_errors(execution)

        # Parent first, then all children. MarketStorage.connect commits once
        # after the block and rolls the entire batch back on any exception.
        with self.storage.connect() as connection:
            connection.execute(
                """INSERT INTO event_review_runs (
                    review_run_id,event_id,run_status,board_status,
                    rubric_version,reviewer_prompt_version,chief_prompt_version,
                    reviewer_model,chief_model,started_at,completed_at,
                    error_code,error_message
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    run_id, board.event_id, run_status, board.status,
                    EVENT_IMPACT_RUBRIC_VERSION, REVIEWER_PROMPT_VERSION,
                    CHIEF_REVIEWER_PROMPT_VERSION, ";".join(reviewer_models),
                    chief_model, started_at, completed_at, error_code, error_message,
                ),
            )

            connection.executemany(
                """INSERT INTO event_review_evidence (
                    review_run_id,evidence_id,event_id,source,source_url,excerpt,
                    evidence_kind,published_at,captured_at,supports_dimensions_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        run_id, item.evidence_id, item.event_id, item.source,
                        item.source_url, item.excerpt, item.evidence_kind,
                        item.published_at, item.captured_at,
                        _json(list(item.supports_dimensions)),
                    )
                    for item in board.evidence
                ],
            )

            connection.executemany(
                """INSERT INTO event_reviewer_assessments (
                    review_run_id,reviewer_role,event_id,recommendation,
                    completion_status,dimension_scores_json,dimension_rationales_json,
                    evidence_ids_json,confidence,summary,concerns_json,
                    model_provider,model_name,model_version,prompt_version
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        run_id, item.reviewer_role, item.event_id,
                        item.recommendation, item.completion_status,
                        _json(_dimension_scores(item.dimension_scores)),
                        _json(dict(item.dimension_rationales)),
                        _json(list(item.evidence_ids)), item.confidence,
                        item.summary, _json(list(item.concerns)),
                        item.model_provider, item.model_name, item.model_version,
                        item.prompt_version,
                    )
                    for item in board.reviewer_assessments
                ],
            )

            if board.adjudication is not None:
                item = board.adjudication
                connection.execute(
                    """INSERT INTO event_review_adjudications (
                        review_run_id,event_id,status,final_dimension_scores_json,
                        rationale,reviewer_roles_considered_json,dissenting_views_json,
                        unresolved_issues_json,required_followups_json,
                        chair_model_provider,chair_model_name,chair_model_version,prompt_version
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        run_id, item.event_id, item.status,
                        _json(_dimension_scores(item.final_dimension_scores)),
                        item.rationale, _json(list(item.reviewer_roles_considered)),
                        _json(list(item.dissenting_views)),
                        _json(list(item.unresolved_issues)),
                        _json(list(item.required_followups)),
                        item.chair_model_provider, item.chair_model_name,
                        item.chair_model_version, item.prompt_version,
                    ),
                )

            connection.execute(
                """INSERT INTO event_review_quality_results (
                    review_run_id,event_id,status,eligible_for_scoring,
                    reasons_json,evaluated_at
                ) VALUES (?,?,?,?,?,?)""",
                (
                    run_id, board.event_id, quality.status,
                    int(quality.eligible_for_scoring), _json(list(quality.reasons)),
                    completed_at,
                ),
            )

        return PersistedEventReviewRun(
            review_run_id=run_id,
            event_id=board.event_id,
            run_status=run_status,
            board_status=board.status,
            quality_result=quality,
        )
