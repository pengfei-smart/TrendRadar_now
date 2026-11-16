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
                """SELECT e.* FROM neeq_termination_evidence e
                WHERE EXISTS (
                    SELECT 1 FROM neeq_termination_evidence_link l
                    WHERE l.evidence_id=e.evidence_id AND l.case_id=?
                )
                ORDER BY CASE WHEN e.published_date='' THEN e.retrieved_at
                              ELSE e.published_date END,
                         e.created_at,e.evidence_id""",
                (normalized_id,),
            ).fetchall()
        ]
        searches = [
            dict(row) for row in connection.execute(
                """SELECT * FROM neeq_termination_evidence_search
                WHERE case_id=? ORDER BY searched_at,search_id""",
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
        item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
        timeline.append({
            "timeline_type": "evidence",
            "event_date": item["published_date"] or item["retrieved_at"],
            "date_semantics": "publication_or_retrieval",
            "date_status": item["evidence_status"],
            "reference_id": item["evidence_id"],
            "description": item["document_title"] or item["source_url"],
        })
    for item in searches:
        item["source_urls"] = json.loads(item.pop("source_urls_json") or "[]")
        timeline.append({
            "timeline_type": "evidence_search",
            "event_date": item["searched_at"],
            "date_semantics": "evidence_investigation",
            "date_status": item["search_status"],
            "reference_id": item["search_id"],
            "description": item["search_notes"] or item["search_scope"],
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
        "evidence_searches": searches,
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
        parsed_reviewed_at = datetime.fromisoformat(reviewed_at.replace("Z", "+00:00"))
        if parsed_reviewed_at.tzinfo is None:
            raise ValueError("missing timezone")
    except ValueError as exc:
        raise ValueError("reviewed_at必须是包含时区偏移的ISO 8601日期时间") from exc

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

def list_neeq_termination_review_queue(
    storage: MarketStorage, *, limit: int = 100,
) -> list[dict[str, Any]]:
    """Build a prioritized, read-only review queue from recorded case/evidence/procedure facts."""
    if not 1 <= limit <= 1000:
        raise ValueError("limit必须在1到1000之间")

    with storage.connect() as connection:
        cases = connection.execute(
            """SELECT * FROM neeq_termination_case
            WHERE case_status NOT IN ('closed','invalid')
            ORDER BY interface_result_date,company_code,case_id"""
        ).fetchall()
        queue: list[dict[str, Any]] = []
        for case_row in cases:
            case = dict(case_row)
            case_id = case["case_id"]
            procedures = [
                dict(row) for row in connection.execute(
                    """SELECT * FROM neeq_termination_procedure
                    WHERE case_id=? ORDER BY event_date,procedure_id""",
                    (case_id,),
                ).fetchall()
            ]
            evidence = [
                dict(row) for row in connection.execute(
                    """SELECT DISTINCT e.evidence_id,e.evidence_kind,e.source_authority,
                              e.evidence_status,e.source_url,e.metadata_json,
                              l.relation_type
                    FROM neeq_termination_evidence e
                    JOIN neeq_termination_evidence_link l ON l.evidence_id=e.evidence_id
                    WHERE l.case_id=?""",
                    (case_id,),
                ).fetchall()
            ]
            search_rows = [
                dict(row) for row in connection.execute(
                    """SELECT s.* FROM neeq_termination_evidence_search s
                    WHERE s.case_id=? AND NOT EXISTS (
                        SELECT 1 FROM neeq_termination_evidence_search newer
                        WHERE newer.case_id=s.case_id
                          AND newer.evidence_kind=s.evidence_kind
                          AND (
                              newer.searched_at>s.searched_at
                              OR (newer.searched_at=s.searched_at AND newer.search_id>s.search_id)
                          )
                    )""",
                    (case_id,),
                ).fetchall()
            ]
            latest_review = connection.execute(
                """SELECT review_id,review_status,conclusion_code,reviewer,reviewed_at,review_notes
                FROM neeq_termination_review WHERE case_id=?
                ORDER BY reviewed_at DESC,review_id DESC LIMIT 1""",
                (case_id,),
            ).fetchone()

            for item in evidence:
                item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
            evidence_kinds = {item["evidence_kind"] for item in evidence}
            latest_search_status = {
                item["evidence_kind"]: item["search_status"] for item in search_rows
            }
            evidence_search_status = {}
            for kind in sorted(evidence_kinds | set(latest_search_status)):
                if kind in evidence_kinds and latest_search_status.get(kind) != "manually_verified":
                    evidence_search_status[kind] = "registered"
                else:
                    evidence_search_status[kind] = latest_search_status.get(kind, "not_searched")
            has_formal_decision = any(
                item["evidence_kind"] in {"formal_decision", "supplementary_decision"}
                or (
                    item["evidence_kind"] == "batch_notice"
                    and (
                        item["relation_type"] == "supports_procedure"
                        or item["metadata"].get("membership_type") == "formal_decision_company"
                    )
                )
                for item in evidence
            )
            conditional_dates = {
                item["event_date"] for item in procedures
                if item["procedure_type"] == "conditional_termination" and item["event_date"]
            }
            mismatch_dates = sorted(
                date for date in conditional_dates
                if case["interface_result_date"] and date != case["interface_result_date"]
            )
            has_final_notice = "final_termination_notice" in evidence_kinds
            has_review_decision = "review_decision" in evidence_kinds

            missing: list[str] = []
            next_actions: list[str] = []
            if not has_formal_decision:
                missing.append("formal_decision")
                next_actions.append("查找并核验全国股转公司正式终止挂牌决定公告")
            if mismatch_dates:
                if not has_review_decision:
                    missing.append("review_decision")
                    next_actions.append("查找复核申请受理、复核决定或补充决定，解释接口日期与条件日期差异")
                if not has_final_notice:
                    missing.append("final_termination_notice")
                    next_actions.append("查找最终终止挂牌公告或官方摘牌结果记录")
            elif not has_final_notice:
                missing.append("final_termination_notice")
                next_actions.append("补充最终终止挂牌公告或官方摘牌结果记录")
            if latest_review is None:
                next_actions.append("进行首次人工证据审阅并记录结论")
            elif latest_review["review_status"] == "needs_more_evidence":
                next_actions.append("按上次复核意见补齐缺失材料后再次复核")
            elif latest_review["review_status"] == "pending":
                next_actions.append("完成待处理的人工复核")
            elif latest_review["review_status"] == "rejected":
                next_actions.append("复核被拒绝的原因并决定是否关闭案件或补充新证据")

            for kind in missing:
                evidence_search_status.setdefault(kind, "not_searched")

            priority = 0 if mismatch_dates else (1 if not has_final_notice else 2)
            queue.append({
                "case_id": case_id,
                "company_code": case["company_code"],
                "company_name": case["company_name_snapshot"],
                "case_status": case["case_status"],
                "interface_result_date": case["interface_result_date"],
                "conditional_termination_dates": sorted(conditional_dates),
                "date_mismatch": bool(mismatch_dates),
                "mismatch_dates": mismatch_dates,
                "evidence_kinds": sorted(evidence_kinds),
                "evidence_search_status": evidence_search_status,
                "missing_evidence_kinds": missing,
                "evidence_coverage_scope": "current_ledger_only",
                "latest_review": dict(latest_review) if latest_review else None,
                "priority": priority,
                "next_actions": next_actions,
                "actual_termination_confirmed": False,
            })

    queue.sort(key=lambda item: (
        item["priority"],
        0 if item["latest_review"] is None else 1,
        item["interface_result_date"] or "",
        item["company_code"],
    ))
    return queue[:limit]

