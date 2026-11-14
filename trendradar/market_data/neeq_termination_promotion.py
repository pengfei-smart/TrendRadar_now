# 本文件执行NEEQ正式终止挂牌事实的显式人工晋升；必须双候选审核通过并提供法律依据，不由采集或审核动作自动触发。
"""Explicit, fail-closed promotion of reviewed NEEQ termination evidence."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime

from trendradar.market_data.neeq_termination_evidence import assess_neeq_termination_evidence


def _json_object(value, field: str) -> dict:
    if isinstance(value, dict):
        return value
    try:
        result = json.loads(value or "{}")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} 不是有效JSON") from exc
    if not isinstance(result, dict):
        raise ValueError(f"{field} 必须是JSON对象")
    return result


def promote_neeq_termination(
    storage, *, identity_candidate_id: str, lifecycle_candidate_id: str,
    effective_date: str, reviewer: str, legal_basis: str, notes: str = "",
    promoted_at: str | None = None,
) -> dict:
    """在单一事务中写入NEEQ发行人主档和终止挂牌事件；不接受未核验身份或日期。"""
    if not identity_candidate_id.strip() or not lifecycle_candidate_id.strip():
        raise ValueError("必须提供身份候选ID和生命周期候选ID")
    if not reviewer.strip() or not legal_basis.strip():
        raise ValueError("必须提供审核人和明确的法律依据")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", effective_date or ""):
        raise ValueError("effective_date 必须为 YYYY-MM-DD")
    try:
        datetime.strptime(effective_date, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("effective_date 不是有效日历日期") from exc
    timestamp = promoted_at or datetime.now().astimezone().isoformat(timespec="seconds")

    with storage.connect() as connection:
        identity = connection.execute(
            "SELECT * FROM neeq_issuer_identity_candidates WHERE identity_candidate_id=?",
            (identity_candidate_id,),
        ).fetchone()
        lifecycle = connection.execute(
            "SELECT * FROM lifecycle_evidence_candidates WHERE candidate_id=?",
            (lifecycle_candidate_id,),
        ).fetchone()
        if identity is None or lifecycle is None:
            raise ValueError("身份候选或生命周期候选不存在")
        identity, lifecycle = dict(identity), dict(lifecycle)
        if identity["review_status"] != "verified" or lifecycle["review_status"] != "verified":
            raise ValueError("身份候选和生命周期候选必须分别人工审核为 verified")
        if lifecycle["exchange"] != "NEEQ":
            raise ValueError("生命周期候选不是NEEQ来源")
        code = identity["company_code"]
        if lifecycle["security_code"] != code:
            raise ValueError("生命周期候选证券代码与身份候选不一致；禁止仅凭元数据路径晋升")
        if not re.fullmatch(r"\d{6}", code or ""):
            raise ValueError("NEEQ company_code 非法")
        payload = _json_object(lifecycle["payload_json"], "payload_json")
        hint = payload.get("issuer_identity_hint") or {}
        if hint.get("issuer_code") != code:
            raise ValueError("生命周期候选的官方元数据身份代码不匹配")
        if hint.get("attachment_url") != identity["attachment_url"]:
            raise ValueError("身份候选附件URL与生命周期候选附件URL不一致")
        if hint.get("metadata_response_sha256") != identity["metadata_response_sha256"]:
            raise ValueError("身份候选与生命周期候选的元数据响应哈希不一致")

        assessment = assess_neeq_termination_evidence(lifecycle)
        if assessment["outcome"] != "candidate_for_human_legal_review":
            raise ValueError(
                "终止证据未达到正式人工法律复核候选条件: " + assessment["outcome"]
            )
        if effective_date not in {
            item["date"] for item in assessment["delisting_date_evidence"]
        }:
            raise ValueError("指定终止日期不在该公告的明确终止挂牌日期证据中")
        if not lifecycle["source_url"].startswith("https://"):
            raise ValueError("正式事件来源必须为HTTPS")
        pdf_hash = str(lifecycle["content_sha256"] or "").lower()
        if not re.fullmatch(r"[0-9a-f]{64}", pdf_hash):
            raise ValueError("生命周期公告正文SHA-256非法")

        current_name = identity["company_name"].strip()
        if not current_name:
            raise ValueError("发行人名称为空")
        event_seed = f"NEEQ|{code}|delisting_effective|{effective_date}"
        event_id = "neeq-delisting-" + hashlib.sha256(event_seed.encode("utf-8")).hexdigest()[:24]
        promotion_notes = (
            f"人工晋升审核人={reviewer.strip()};法律依据={legal_basis.strip()};"
            f"身份候选={identity_candidate_id};生命周期候选={lifecycle_candidate_id};"
            f"公告正文SHA256={pdf_hash};晋升时间={timestamp};补充说明={notes.strip()}"
        )

        existing_event = connection.execute(
            "SELECT * FROM security_lifecycle_events WHERE event_id=?", (event_id,)
        ).fetchone()
        if existing_event:
            existing_event = dict(existing_event)
            if (existing_event["security_code"] != code
                    or existing_event["event_type"] != "delisting_effective"
                    or existing_event["event_date"] != effective_date
                    or existing_event["source_url"] != lifecycle["source_url"]):
                raise ValueError("相同事件ID已存在但事实内容冲突，拒绝覆盖")
            issuer = connection.execute(
                "SELECT company_code FROM neeq_issuer_master WHERE company_code=?", (code,)
            ).fetchone()
            return {
                "status": "already_promoted" if issuer else "inconsistent_existing_event",
                "company_code": code, "event_id": event_id,
                "formal_issuer_master_writes": 0, "formal_lifecycle_event_writes": 0,
            }

        connection.execute(
            """INSERT INTO neeq_issuer_master
            (company_code,current_name,full_name,market_layer,issuer_status,identity_status,
             source_name,source_url,source_sha256,source_retrieved_at,notes)
            VALUES(?,?,?,?,?,'verified',?,?,?,?,?)
            ON CONFLICT(company_code) DO UPDATE SET
              current_name=excluded.current_name,
              issuer_status='terminated',
              identity_status='verified',
              source_name=excluded.source_name,
              source_url=excluded.source_url,
              source_sha256=excluded.source_sha256,
              source_retrieved_at=excluded.source_retrieved_at,
              notes=excluded.notes,
              updated_at=CURRENT_TIMESTAMP""",
            (code, current_name, "", "unknown", "terminated",
             "NEEQ official announcement metadata", identity["metadata_source_url"],
             identity["metadata_response_sha256"], identity["retrieved_at"], promotion_notes),
        )
        connection.execute(
            """INSERT INTO security_lifecycle_events
            (event_id,security_code,exchange,event_type,event_date,delisting_effective_date,
             date_status,evidence_grade,source_title,source_url,notes)
            VALUES(?,?,'NEEQ','delisting_effective',?,?,'confirmed','official',?,?,?)""",
            (event_id, code, effective_date, effective_date, lifecycle["source_title"],
             lifecycle["source_url"], promotion_notes),
        )
    return {
        "status": "promoted", "company_code": code, "company_name": current_name,
        "effective_date": effective_date, "event_id": event_id,
        "formal_issuer_master_writes": 1, "formal_lifecycle_event_writes": 1,
        "reviewer": reviewer.strip(), "legal_basis": legal_basis.strip(),
    }
