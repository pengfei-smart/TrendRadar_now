"""Atomic persistence for AI event-review board execution history.

A review run and all of its evidence, assessments, adjudication and quality
result are committed in one SQLite transaction. The v12/v13 audit tables are append-only:
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
        operational_error_code: str = "",
        operational_error_message: str = "",
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
        if operational_error_code:
            error_code = ";".join(filter(None, (error_code, operational_error_code)))
        if operational_error_message:
            error_message = " | ".join(filter(None, (
                error_message, operational_error_message
            )))[:4000]

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

            usage_records = [
                (
                    run_id, board.event_id, item.assessment.reviewer_role,
                    "success" if item.request_succeeded else "error",
                    item.assessment.model_provider, item.assessment.model_name,
                    item.request_id, item.attempts,
                    item.prompt_tokens, item.completion_tokens, item.total_tokens,
                    int(
                        item.prompt_tokens is not None
                        and item.completion_tokens is not None
                        and item.total_tokens is not None
                    ),
                    item.call_started_at or started_at,
                    item.call_completed_at or completed_at,
                    item.error_code,
                )
                for item in execution.reviewer_results
            ]
            if execution.chief_result is not None:
                item = execution.chief_result
                usage_records.append((
                    run_id, board.event_id, "chief_reviewer",
                    "success" if item.request_succeeded else "error",
                    item.provider, item.model, item.request_id, item.attempts,
                    item.prompt_tokens, item.completion_tokens, item.total_tokens,
                    int(
                        item.prompt_tokens is not None
                        and item.completion_tokens is not None
                        and item.total_tokens is not None
                    ),
                    item.call_started_at or started_at,
                    item.call_completed_at or completed_at,
                    item.error_code,
                ))
            connection.executemany(
                """INSERT INTO event_review_model_usage (
                    review_run_id,event_id,reviewer_role,request_status,
                    provider,model,request_id,attempts,prompt_tokens,
                    completion_tokens,total_tokens,usage_complete,
                    call_started_at,call_completed_at,error_code
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                usage_records,
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


    def list_runs(
        self,
        *,
        event_id: str,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple["EventReviewRunSummary", ...]:
        """List an event's immutable review history, newest first."""
        if not isinstance(event_id, str) or not event_id.strip():
            raise ValueError("event_id must be a non-empty string")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit must be an integer between 1 and 500")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("offset must be a non-negative integer")

        with self.storage.connect() as connection:
            rows = connection.execute(
                """SELECT r.review_run_id,r.event_id,r.run_status,r.board_status,
                          r.started_at,r.completed_at,r.rubric_version,
                          r.reviewer_model,r.chief_model,r.error_code,
                          q.status AS quality_status,
                          COALESCE(q.eligible_for_scoring,0) AS eligible_for_scoring
                   FROM event_review_runs AS r
                   LEFT JOIN event_review_quality_results AS q
                     ON q.review_run_id=r.review_run_id
                   WHERE r.event_id=?
                   ORDER BY julianday(r.started_at) DESC,r.created_at DESC,
                            r.review_run_id DESC
                   LIMIT ? OFFSET ?""",
                (event_id.strip(), limit, offset),
            ).fetchall()

        return tuple(
            EventReviewRunSummary(
                review_run_id=row["review_run_id"],
                event_id=row["event_id"],
                run_status=row["run_status"],
                board_status=row["board_status"],
                started_at=row["started_at"],
                completed_at=row["completed_at"],
                rubric_version=row["rubric_version"],
                reviewer_model=row["reviewer_model"],
                chief_model=row["chief_model"],
                quality_status=row["quality_status"],
                eligible_for_scoring=bool(row["eligible_for_scoring"]),
                error_code=row["error_code"],
            )
            for row in rows
        )

    def get_run(self, *, review_run_id: str) -> "EventReviewAuditRecord | None":
        """Load one complete, read-only audit record with decoded JSON fields."""
        if not isinstance(review_run_id, str) or not review_run_id.strip():
            raise ValueError("review_run_id must be a non-empty string")
        run_id = review_run_id.strip()
        with self.storage.connect() as connection:
            run = connection.execute(
                "SELECT * FROM event_review_runs WHERE review_run_id=?", (run_id,)
            ).fetchone()
            if run is None:
                return None
            evidence_rows = connection.execute(
                """SELECT * FROM event_review_evidence WHERE review_run_id=?
                   ORDER BY published_at,evidence_id""", (run_id,)
            ).fetchall()
            assessment_rows = connection.execute(
                """SELECT * FROM event_reviewer_assessments WHERE review_run_id=?
                   ORDER BY reviewer_role""", (run_id,)
            ).fetchall()
            adjudication_row = connection.execute(
                "SELECT * FROM event_review_adjudications WHERE review_run_id=?",
                (run_id,),
            ).fetchone()
            quality_row = connection.execute(
                "SELECT * FROM event_review_quality_results WHERE review_run_id=?",
                (run_id,),
            ).fetchone()
            usage_rows = connection.execute(
                """SELECT * FROM event_review_model_usage WHERE review_run_id=?
                   ORDER BY CASE reviewer_role
                       WHEN 'fact_checker' THEN 1
                       WHEN 'macro_policy_analyst' THEN 2
                       WHEN 'industry_chain_analyst' THEN 3
                       WHEN 'market_expectations_analyst' THEN 4
                       ELSE 5 END""",
                (run_id,),
            ).fetchall()

        if quality_row is None:
            raise RuntimeError(f"review run {run_id!r} has no quality result")
        return EventReviewAuditRecord(
            run=_row_dict(run),
            evidence=tuple(_decode_evidence(row) for row in evidence_rows),
            reviewer_assessments=tuple(
                _decode_assessment(row) for row in assessment_rows
            ),
            adjudication=_decode_adjudication(adjudication_row),
            quality_result=_decode_quality(quality_row),
            model_usage=tuple(_row_dict(row) for row in usage_rows),
        )


@dataclass(frozen=True)
class EventReviewRunSummary:
    """Compact immutable index entry for an event's review history."""

    review_run_id: str
    event_id: str
    run_status: str
    board_status: str
    started_at: str
    completed_at: str
    rubric_version: str
    reviewer_model: str
    chief_model: str
    quality_status: str | None
    eligible_for_scoring: bool
    error_code: str


@dataclass(frozen=True)
class EventReviewAuditRecord:
    """Read-only reconstruction of one persisted review run and its provenance."""

    run: dict
    evidence: tuple[dict, ...]
    reviewer_assessments: tuple[dict, ...]
    adjudication: dict | None
    quality_result: dict | None
    model_usage: tuple[dict, ...] = ()


def _decode_json_field(row: dict, field: str):
    try:
        return json.loads(row[field])
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"corrupt event-review audit JSON field: {field}"
        ) from exc


def _row_dict(row):
    return dict(row) if row is not None else None


def _decode_evidence(row):
    item = dict(row)
    item["supports_dimensions"] = _decode_json_field(item, "supports_dimensions_json")
    del item["supports_dimensions_json"]
    return item


def _decode_assessment(row):
    item = dict(row)
    for source, target in (
        ("dimension_scores_json", "dimension_scores"),
        ("dimension_rationales_json", "dimension_rationales"),
        ("evidence_ids_json", "evidence_ids"),
        ("concerns_json", "concerns"),
    ):
        item[target] = _decode_json_field(item, source)
        del item[source]
    return item


def _decode_adjudication(row):
    if row is None:
        return None
    item = dict(row)
    for source, target in (
        ("final_dimension_scores_json", "final_dimension_scores"),
        ("reviewer_roles_considered_json", "reviewer_roles_considered"),
        ("dissenting_views_json", "dissenting_views"),
        ("unresolved_issues_json", "unresolved_issues"),
        ("required_followups_json", "required_followups"),
    ):
        item[target] = _decode_json_field(item, source)
        del item[source]
    return item


def _decode_quality(row):
    if row is None:
        return None
    item = dict(row)
    item["reasons"] = _decode_json_field(item, "reasons_json")
    del item["reasons_json"]
    item["eligible_for_scoring"] = bool(item["eligible_for_scoring"])
    return item


# Read methods are attached to the repository class below to keep the public
# API discoverable beside save_run while preserving the append-only contract.

def _decode_json_field(row, field: str):
    try:
        return json.loads(row[field])
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"corrupt event-review audit JSON field: {field}"
        ) from exc


def _row_dict(row):
    return dict(row) if row is not None else None


def _decode_evidence(row):
    item = dict(row)
    item["supports_dimensions"] = _decode_json_field(
        item, "supports_dimensions_json"
    )
    del item["supports_dimensions_json"]
    return item


def _decode_assessment(row):
    item = dict(row)
    for source, target in (
        ("dimension_scores_json", "dimension_scores"),
        ("dimension_rationales_json", "dimension_rationales"),
        ("evidence_ids_json", "evidence_ids"),
        ("concerns_json", "concerns"),
    ):
        item[target] = _decode_json_field(item, source)
        del item[source]
    return item


def _decode_adjudication(row):
    if row is None:
        return None
    item = dict(row)
    for source, target in (
        ("final_dimension_scores_json", "final_dimension_scores"),
        ("reviewer_roles_considered_json", "reviewer_roles_considered"),
        ("dissenting_views_json", "dissenting_views"),
        ("unresolved_issues_json", "unresolved_issues"),
        ("required_followups_json", "required_followups"),
    ):
        item[target] = _decode_json_field(item, source)
        del item[source]
    return item


def _decode_quality(row):
    if row is None:
        return None
    item = dict(row)
    item["reasons"] = _decode_json_field(item, "reasons_json")
    del item["reasons_json"]
    item["eligible_for_scoring"] = bool(item["eligible_for_scoring"])
    return item

