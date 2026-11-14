# 本脚本提供NEEQ身份及生命周期证据候选的人工审核入口，只更新候选审核字段，不晋升正式主档或生命周期事实。
"""Review NEEQ identity and lifecycle evidence candidates without promoting formal records."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trendradar.market_data.storage import MarketStorage
from trendradar.market_data.neeq_notice_classifier import classify_neeq_notice
from trendradar.market_data.neeq_termination_evidence import assess_neeq_termination_evidence
from trendradar.market_data.neeq_lifecycle_event_aggregator import build_neeq_lifecycle_event_groups


REVIEW_STATUSES = ("pending", "verified", "rejected", "needs_more_evidence")


def list_candidates(storage: MarketStorage, *, target: str = "all",
                    review_status: str | None = None, company_code: str | None = None,
                    limit: int = 100) -> dict:
    """读取候选队列；生命周期候选只显示NEEQ记录。"""
    result = {"identity_candidates": [], "lifecycle_candidates": []}
    if target in {"all", "identity"}:
        identities = storage.list_neeq_issuer_identity_candidates(
            review_status=review_status, company_code=company_code, limit=limit,
        )
        result["identity_candidates"] = [{
            "candidate_id": row["identity_candidate_id"],
            "company_code": row["company_code"],
            "company_name": row["company_name"],
            "title": row["disclosure_title"],
            "publish_date": row["publish_date"],
            "attachment_url": row["attachment_url"],
            "metadata_source_url": row["metadata_source_url"],
            "metadata_response_sha256": row["metadata_response_sha256"],
            "review_status": row["review_status"],
            "reviewer": row["reviewer"],
            "reviewed_at": row["reviewed_at"],
            "review_notes": row["review_notes"],
            "raw_record": row["raw_record"],
            "notice_classification": classify_neeq_notice(row["disclosure_title"]),
        } for row in identities]
    if target in {"all", "lifecycle"}:
        lifecycle = storage.list_lifecycle_evidence_candidates(
            review_status=review_status, limit=limit,
        )
        for row in lifecycle:
            if row.get("exchange") != "NEEQ":
                continue
            if company_code and row.get("security_code") != company_code:
                try:
                    payload = json.loads(row.get("payload_json") or "{}")
                except (TypeError, ValueError):
                    payload = {}
                hint = payload.get("issuer_identity_hint") or {}
                if hint.get("issuer_code") != company_code:
                    continue
            try:
                payload = json.loads(row.get("payload_json") or "{}")
            except (TypeError, ValueError):
                payload = {}
            result["lifecycle_candidates"].append({
                "candidate_id": row["candidate_id"],
                "candidate_kind": row["candidate_kind"],
                "security_code": row["security_code"],
                "event_type": row["event_type"],
                "event_date": row["event_date"],
                "date_status": row["date_status"],
                "source_title": row["source_title"],
                "source_url": row["source_url"],
                "source_published_at": row["source_published_at"],
                "evidence_excerpt": row["evidence_excerpt"],
                "evidence_grade": row["evidence_grade"],
                "review_status": row["review_status"],
                "reviewer": row["reviewer"],
                "reviewed_at": row["reviewed_at"],
                "review_notes": row["review_notes"],
                "issuer_identity_hint": payload.get("issuer_identity_hint", {}),
                "date_evidence": (payload.get("parsed_evidence") or {}).get("date_evidence", []),
                "notice_classification": payload.get("notice_classification") or classify_neeq_notice(row["source_title"]),
                "termination_evidence_assessment": assess_neeq_termination_evidence(row),
            })
    result["event_groups"] = build_neeq_lifecycle_event_groups(result["lifecycle_candidates"])
    return result


def review_candidate(storage: MarketStorage, *, target: str, candidate_id: str,
                     review_status: str, reviewer: str, review_notes: str = "",
                     reviewed_at: str | None = None) -> dict:
    """审核一个候选；verified只表示候选证据通过审核，不代表正式事实。"""
    if target not in {"identity", "lifecycle"}:
        raise ValueError("target 必须是 identity 或 lifecycle")
    if review_status not in REVIEW_STATUSES:
        raise ValueError("不支持的审核状态")
    if not candidate_id.strip() or not reviewer.strip():
        raise ValueError("candidate_id 和 reviewer 不能为空")
    timestamp = reviewed_at or datetime.now().astimezone().isoformat(timespec="seconds")
    if target == "identity":
        existing = storage.list_neeq_issuer_identity_candidates(limit=5000)
        if not any(row.get("identity_candidate_id") == candidate_id for row in existing):
            raise ValueError(f"NEEQ身份候选不存在: {candidate_id}")
        storage.review_neeq_issuer_identity_candidate(
            candidate_id, review_status=review_status, reviewer=reviewer,
            reviewed_at=timestamp, review_notes=review_notes,
        )
    else:
        existing = storage.list_lifecycle_evidence_candidates(limit=5000)
        if not any(row.get("candidate_id") == candidate_id and row.get("exchange") == "NEEQ"
                   for row in existing):
            raise ValueError(f"NEEQ生命周期候选不存在: {candidate_id}")
        storage.review_lifecycle_evidence_candidate(
            candidate_id, review_status=review_status, reviewer=reviewer,
            reviewed_at=timestamp, review_notes=review_notes,
        )
    return {
        "target": target, "candidate_id": candidate_id,
        "review_status": review_status, "reviewer": reviewer,
        "reviewed_at": timestamp, "formal_master_writes": 0,
        "formal_lifecycle_writes": 0,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="查看或审核NEEQ身份/生命周期候选；不会写入正式发行人主档或生命周期事实表。"
    )
    parser.add_argument("--db", default="output/market/market.db", help="独立市场数据库路径")
    sub = parser.add_subparsers(dest="command", required=True)
    listing = sub.add_parser("list", help="列出待审核候选")
    listing.add_argument("--target", choices=("all", "identity", "lifecycle"), default="all")
    listing.add_argument("--status", choices=REVIEW_STATUSES, default=None)
    listing.add_argument("--company-code", default="")
    listing.add_argument("--limit", type=int, default=100)

    review = sub.add_parser("review", help="审核单条候选")
    review.add_argument("--target", choices=("identity", "lifecycle"), required=True)
    review.add_argument("--candidate-id", required=True)
    review.add_argument("--status", choices=REVIEW_STATUSES, required=True)
    review.add_argument("--reviewer", required=True)
    review.add_argument("--notes", default="")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    storage = MarketStorage(args.db)
    if args.command == "list":
        if not 1 <= args.limit <= 5000:
            raise SystemExit("--limit 必须在1到5000之间")
        output = {
            "database": str(storage.db_path),
            **list_candidates(
                storage, target=args.target, review_status=args.status,
                company_code=args.company_code or None, limit=args.limit,
            ),
            "safety": {
                "formal_issuer_master_writes": 0,
                "formal_lifecycle_writes": 0,
                "read_only": True,
            },
        }
    else:
        output = {
            "database": str(storage.db_path),
            "review": review_candidate(
                storage, target=args.target, candidate_id=args.candidate_id,
                review_status=args.status, reviewer=args.reviewer,
                review_notes=args.notes,
            ),
            "safety": {
                "formal_issuer_master_writes": 0,
                "formal_lifecycle_writes": 0,
                "candidate_review_only": True,
            },
        }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
