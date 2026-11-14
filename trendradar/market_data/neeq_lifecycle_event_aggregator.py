# 本文件将同一发行人的多份NEEQ公告证据归并为事件组视图，不修改候选记录或正式生命周期事实。
"""Build conservative event groups from review-only NEEQ lifecycle candidates."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Iterable


def _payload(row: dict) -> dict:
    value = row.get("payload_json") or {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return {}
    return value if isinstance(value, dict) else {}


def _date_evidence(payload: dict, row: dict | None = None) -> list[dict]:
    parsed = payload.get("parsed_evidence") or {}
    values = parsed.get("date_evidence") if isinstance(parsed, dict) else []
    if not isinstance(values, list):
        values = payload.get("date_evidence") or ((row or {}).get("date_evidence", []))
    return [item for item in values if isinstance(item, dict)] if isinstance(values, list) else []


def _decision_date(payload: dict, row: dict | None = None) -> str:
    dates = {
        str(item.get("value") or item.get("date") or "").strip()
        for item in _date_evidence(payload, row)
        if item.get("date_type") == "decision_date"
        and str(item.get("value") or item.get("date") or "").strip()
    }
    # Multiple conflicting decision dates are not safe grouping anchors.
    return next(iter(dates)) if len(dates) == 1 else ""


def build_neeq_lifecycle_event_groups(candidates: Iterable[dict]) -> list[dict]:
    """Group evidence by issuer, event type and an explicit common decision date.

    Rows without a metadata-linked issuer code or a unique decision_date remain
    ungrouped. This deliberately prefers missed merges over false merges.
    Grouping is a read-only view: it never changes candidate review status or
    writes formal lifecycle facts.
    """
    groups: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    ungrouped: list[dict] = []

    for row in candidates:
        if not isinstance(row, dict):
            continue
        payload = _payload(row)
        hint = payload.get("issuer_identity_hint") or row.get("issuer_identity_hint") or {}
        if not isinstance(hint, dict):
            hint = {}
        issuer_code = str(
            hint.get("issuer_code") or row.get("security_code") or ""
        ).strip()
        event_type = str(row.get("event_type") or "").strip()
        decision_date = _decision_date(payload, row)
        if (
            str(row.get("exchange") or "").upper() != "NEEQ"
            or len(issuer_code) != 6
            or not issuer_code.isdigit()
            or not event_type
            or not decision_date
        ):
            ungrouped.append(row)
            continue
        groups[(issuer_code, event_type, decision_date)].append(row)

    output = []
    for (issuer_code, event_type, decision_date), rows in sorted(groups.items()):
        identity_names = {
            str(((_payload(row).get("issuer_identity_hint") or row.get("issuer_identity_hint") or {}).get("issuer_name")) or "").strip()
            for row in rows
        } - {""}
        group_seed = "|".join(("NEEQ", issuer_code, event_type, decision_date))
        group_id = "neeq-event-" + hashlib.sha256(group_seed.encode("utf-8")).hexdigest()[:24]
        evidence = []
        observed_dates = set()
        for row in sorted(rows, key=lambda item: (
            str(item.get("source_published_at") or ""),
            str(item.get("source_url") or ""),
            str(item.get("candidate_id") or ""),
        )):
            payload = _payload(row)
            for item in _date_evidence(payload, row):
                value = str(item.get("value") or item.get("date") or "").strip()
                date_type = str(item.get("date_type") or "unspecified").strip()
                if value:
                    observed_dates.add((date_type, value))
            evidence.append({
                "candidate_id": str(row.get("candidate_id") or ""),
                "source_title": str(row.get("source_title") or ""),
                "source_url": str(row.get("source_url") or ""),
                "source_published_at": str(row.get("source_published_at") or ""),
                "review_status": str(row.get("review_status") or "pending"),
                "notice_classification": payload.get("notice_classification") or row.get("notice_classification", {}),
            })
        output.append({
            "event_group_id": group_id,
            "exchange": "NEEQ",
            "issuer_code": issuer_code,
            "issuer_names": sorted(identity_names),
            "event_type": event_type,
            "decision_date_anchor": decision_date,
            "evidence_count": len(evidence),
            "evidence": evidence,
            "date_evidence": [
                {"date_type": kind, "value": value}
                for kind, value in sorted(observed_dates)
            ],
            "review_status": "pending",
            "confirmed_legal_fact": False,
            "formal_lifecycle_write_allowed": False,
        })

    for row in ungrouped:
        payload = _payload(row)
        hint = payload.get("issuer_identity_hint") or row.get("issuer_identity_hint") or {}
        output.append({
            "event_group_id": "",
            "exchange": str(row.get("exchange") or ""),
            "issuer_code": str(hint.get("issuer_code") or row.get("security_code") or ""),
            "event_type": str(row.get("event_type") or ""),
            "decision_date_anchor": "",
            "evidence_count": 1,
            "evidence": [{
                "candidate_id": str(row.get("candidate_id") or ""),
                "source_title": str(row.get("source_title") or ""),
                "source_url": str(row.get("source_url") or ""),
                "source_published_at": str(row.get("source_published_at") or ""),
                "review_status": str(row.get("review_status") or "pending"),
                "notice_classification": payload.get("notice_classification") or row.get("notice_classification", {}),
            }],
            "date_evidence": [],
            "review_status": "pending",
            "confirmed_legal_fact": False,
            "formal_lifecycle_write_allowed": False,
            "grouping_status": "insufficient_identity_or_decision_date",
        })
    return output
