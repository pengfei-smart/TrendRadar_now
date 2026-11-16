# 本文件提供NEEQ终止挂牌案件证据时间线与追加式人工复核服务。
"""Review and timeline services for the isolated NEEQ termination evidence ledger."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from trendradar.market_data.storage import MarketStorage


_REVIEW_STATUSES = {"pending", "verified", "rejected", "needs_more_evidence"}


def get_neeq_termination_case_timeline(
    storage: MarketStorage, case_id: str,
) -> dict[str, Any]:
    """Return one case with a chronological, read-only view of its procedures, evidence and reviews."""
    normalized_id = str(case_id or "").strip()
    if not normalized_id:
        raise ValueError("case_id不能为空")

    with storage.connect() as connection:
        case_row = connection.execute(
            "SELECT * FROM neeq_termination_case WHERE case_id=?", (normalized_id,),
        ).fetchone()
        if case_row is None:
            raise ValueError(f"NEEQ案件不存在: {normalized_id}")

        procedures = [
            dict(row) for row in connection.execute(
                """SELECT * FROM neeq_termination_procedure
                WHERE case_id=? ORDER BY event_date,created_at,procedure_id""",
                (normalized_id,),
            ).fetchall()
        ]
        evidence = [
            dict(row) for row in connection.execute(
                """SELECT DISTINCT e.* FROM neeq_termination_evidence e
                JOIN neeq_termination_evidence_link l ON l.evidence_id=e.evidence_id
                WHERE l.case_id=?
                ORDER BY CASE WHEN e.published_date='' THEN e.retrieved_at
                              ELSE e.published_date END,
                         e.created_at,e.evidence_id""",
                (normalized_id,),
            ).fetchall()
        ]
        links = [
            dict(row) for row in connection.execute(
                """SELECT * FROM neeq_termination_evidence_link
                WHERE case_id=? ORDER BY created_at,link_id""",
                (normalized_id,),
            ).fetchall()
        ]
        reviews = [
            dict(row) for row in connection.execute(
                """SELECT * FROM neeq_termination_review
                WHERE case_id=? ORDER BY reviewed_at,review_id""",
                (normalized_id,),
            ).fetchall()
        ]

    case = dict(case_row)
    case["raw_record"] = json.loads(case.pop("raw_record_json"))
    timeline: list[dict[str, Any]] = []
    if case.get("interface_result_date"):
        timeline.append({
            "timeline_type": "interface_result_date",
            "event_date": case["interface_result_date"],
            "date_semantics": "official_result_api_candidate",
            "date_status": "unverified",
            "reference_id": normalized_id,
            "description": "NEEQ结果接口候选日期；不代表已确认的实际终止挂牌日期。",
        })
    for item in procedures:
        timeline.append({
            "timeline_type": "procedure",
            "event_date": item["event_date"] or "",
            "date_semantics": item["date_semantics"],
            "date_status": item["date_status"],
            "reference_id": item["procedure_id"],
            "description": item["notes"],
        })
    for item in evidence:
        timeline.append({
            "timeline_type": "evidence",
            "event_date": item["published_date"] or item["retrieved_at"],
            "date_semantics": "publication_or_retrieval",
            "date_status": item["evidence_status"],
            "reference_id": item["evidence_id"],
            "description": item["document_title"] or item["source_url"],
        })
    for item in reviews:
        timeline.append({
            "timeline_type": "review",
            "event_date": item["reviewed_at"],
            "date_semantics": "manual_review",
            "date_status": item["review_status"],
            "reference_id": str(item["review_id"]),
            "description": item["review_notes"],
        })
    timeline.sort(key=lambda item: (
        item["event_date"] or "",
        {"interface_result_date": 0, "evidence": 1, "procedure": 2, "review": 3}.get(
            item["timeline_type"], 9
        ),
        item["reference_id"],
    ))
    return {
        "case": case,
        "timeline": timeline,
        "procedures": procedures,
        "evidence": evidence,
        "evidence_links": links,
        "reviews": reviews,
    }


def append_neeq_termination_case_review(
    storage: MarketStorage, review: dict[str, Any],
) -> int:
    """Append a manual review after verifying its case/evidence/procedure references."""
    case_id = str(review.get("case_id") or "").strip()
    reviewer = str(review.get("reviewer") or "").strip()
    reviewed_at = str(review.get("reviewed_at") or "").strip()
    status = str(review.get("review_status") or "pending").strip()
    conclusion = str(review.get("conclusion_code") or "").strip()
    evidence_id = str(review.get("evidence_id") or "").strip() or None
    procedure_id = str(review.get("procedure_id") or "").strip() or None
    notes = str(review.get("review_notes") or "")

    if not case_id or not reviewer or not reviewed_at:
        raise ValueError("复核必须提供case_id、reviewer和reviewed_at")
    if status not in _REVIEW_STATUSES:
        raise ValueError(f"非法review_status: {status}")
    if conclusion == "actual_termination_confirmed":
        raise ValueError("该复核服务不允许确认正式实际终止挂牌事实")
    try:
        datetime.fromisoformat(reviewed_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("reviewed_at必须是ISO 8601日期时间") from exc

    with storage.connect() as connection:
        if not connection.execute(
            "SELECT 1 FROM neeq_termination_case WHERE case_id=?", (case_id,),
        ).fetchone():
            raise ValueError(f"NEEQ案件不存在: {case_id}")
        if evidence_id and not connection.execute(
            """SELECT 1 FROM neeq_termination_evidence_link
            WHERE case_id=? AND evidence_id=?""",
            (case_id, evidence_id),
        ).fetchone():
            raise ValueError("复核引用的证据未关联到该案件")
        if procedure_id and not connection.execute(
            """SELECT 1 FROM neeq_termination_procedure
            WHERE case_id=? AND procedure_id=?""",
            (case_id, procedure_id),
        ).fetchone():
            raise ValueError("复核引用的程序节点不属于该案件")
        cursor = connection.execute(
            """INSERT INTO neeq_termination_review
            (case_id,evidence_id,procedure_id,review_status,conclusion_code,
             reviewer,reviewed_at,review_notes)
            VALUES(?,?,?,?,?,?,?,?)""",
            (case_id, evidence_id, procedure_id, status, conclusion,
             reviewer, reviewed_at, notes),
        )
        review_id = int(cursor.lastrowid)
        row = connection.execute(
            """SELECT case_id,review_status FROM neeq_termination_review WHERE review_id=?""",
            (review_id,),
        ).fetchone()
        if row is None or row["case_id"] != case_id or row["review_status"] != status:
            raise RuntimeError("复核记录写入后回读校验失败")
        return review_id
