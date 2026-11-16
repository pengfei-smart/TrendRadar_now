# 受控导入 NEEQ 终止挂牌候选及公告证据到独立台账；默认仅预览，不确认法律事实。
# -*- coding: utf-8 -*-
"""Import reviewed NEEQ candidate/audit JSON into the isolated evidence ledger."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from trendradar.market_data.storage import MarketStorage


def _canonical_json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


CHINA_TZ = timezone(timedelta(hours=8))


def _date_value(value: Any) -> str:
    if isinstance(value, str):
        return value[:10] if value else ""
    if isinstance(value, dict):
        epoch = value.get("time")
        if isinstance(epoch, (int, float)):
            return datetime.fromtimestamp(epoch / 1000, tz=timezone.utc).astimezone(
                CHINA_TZ
            ).date().isoformat()
    return ""


def _datetime_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        epoch = value.get("time")
        if isinstance(epoch, (int, float)):
            return datetime.fromtimestamp(epoch / 1000, tz=timezone.utc).astimezone(
                CHINA_TZ
            ).isoformat()
    return ""


def _manifest_records(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        records = payload
    elif isinstance(payload, dict):
        nested_manifest = payload.get("manifest")
        if isinstance(nested_manifest, dict) and isinstance(nested_manifest.get("candidates"), list):
            records = nested_manifest["candidates"]
        else:
            records = next(
                (payload[key] for key in ("records", "candidates", "items", "data")
                 if isinstance(payload.get(key), list)),
                None,
            )
        if records is None:
            raise ValueError("candidate manifest must contain records/candidates/items/data list")
    else:
        raise ValueError("candidate manifest root must be an object or list")
    normalized = []
    for entry in records:
        if not isinstance(entry, dict):
            raise ValueError("candidate manifest contains a non-object record")
        raw = entry.get("raw_record")
        if not isinstance(raw, dict):
            raw = entry
        code = str(entry.get("company_code") or entry.get("code") or raw.get("companyCode") or "").strip()
        record_id = str(entry.get("record_id") or entry.get("id") or raw.get("id") or "").strip()
        if len(code) != 6 or not code.isdigit() or not record_id:
            continue
        normalized.append({
            "record_id": record_id,
            "company_code": code,
            "company_name": str(entry.get("company_name") or entry.get("name") or raw.get("companyName") or ""),
            "raw_record": raw,
            "raw_record_sha256": str(entry.get("raw_record_sha256") or ""),
            "interface_result_date": str(entry.get("delisting_date") or entry.get("result_date") or entry.get("delistingDateISO") or _date_value(raw.get("delistingDate"))),
            "interface_create_at": str(
                entry.get("create_time") or entry.get("create_at")
                or _datetime_value(raw.get("createTime")) or ""
            ),
        })
    return normalized


def build_import_payload(
    manifest: Any, date_audit: dict, *, retrieved_at: str,
) -> dict[str, list[dict]]:
    """Build deterministic case/evidence/procedure/link rows; never set actual termination."""
    if not retrieved_at.strip():
        raise ValueError("retrieved_at is required")
    safety = date_audit.get("safety") or {}
    if safety.get("interface_date_promoted_to_actual_termination_date") is True:
        raise ValueError("unsafe audit report: interface date was promoted to actual termination date")
    audit_rows = date_audit.get("candidates")
    if not isinstance(audit_rows, list):
        raise ValueError("date audit must contain candidates list")
    by_key = {(str(row.get("record_id")), str(row.get("security_code"))): row for row in audit_rows}
    if len(by_key) != len(audit_rows):
        raise ValueError("date audit contains duplicate record_id/security_code keys")
    manifest_rows = _manifest_records(manifest)
    manifest_by_key = {(row["record_id"], row["company_code"]): row for row in manifest_rows}
    missing_keys = sorted(set(by_key) - set(manifest_by_key))
    if missing_keys:
        raise ValueError(f"date audit candidates missing from manifest: {missing_keys[:10]}")
    cases, evidences, procedures, links = [], {}, {}, {}
    for key, audit in sorted(by_key.items()):
        source = manifest_by_key[key]
        raw_json = _canonical_json(source["raw_record"])
        raw_hash = _sha256(raw_json)
        expected_hash = str(audit.get("raw_record_sha256") or source.get("raw_record_sha256") or "")
        if expected_hash and raw_hash != expected_hash:
            raise ValueError(
                f"raw record SHA256 mismatch for {key}: computed={raw_hash}, expected={expected_hash}"
            )
        case_id = f"neeq-result-{source['company_code']}-{source['record_id']}"
        cases.append({
            "case_id": case_id,
            "company_code": source["company_code"],
            "company_name_snapshot": source["company_name"],
            "source_record_id": source["record_id"],
            "source_api": "neeq_termination_result_api",
            "interface_result_date": source["interface_result_date"] or audit.get("interface_result_date") or "",
            "interface_create_at": source["interface_create_at"],
            "raw_record": source["raw_record"],
            "raw_record_sha256": raw_hash,
            "case_status": "pending_review",
        })
        for index, item in enumerate(audit.get("decision_evidence") or []):
            url = str(item.get("source_url") or "").strip()
            if not url.startswith("https://"):
                continue
            notice = str(item.get("notice_number") or "")
            evidence_key = _sha256(f"{url}|{notice}")
            evidence_id = f"neeq-evidence-{evidence_key[:24]}"
            kind = "batch_notice" if item.get("evidence_type") == "parsed_batch_membership" else "formal_decision"
            authority = "neeq_official" if "neeq.com.cn" in url.lower() else "unknown"
            evidences.setdefault(evidence_id, {
                "evidence_id": evidence_id,
                "evidence_kind": kind,
                "source_authority": authority,
                "notice_number": notice,
                "document_title": "",
                "source_url": url,
                "published_date": "",
                "retrieved_at": retrieved_at,
                "evidence_status": "crosschecked" if authority == "neeq_official" else "unverified",
                "metadata": {
                    "source": "neeq_datatype0_date_audit_v4",
                    "evidence_type": item.get("evidence_type"),
                    "membership_type": item.get("membership_type"),
                    "decision_date": item.get("decision_date"),
                    "conditional_termination_date": item.get("conditional_termination_date"),
                    "review_status": "pending_manual_review",
                    "confirmed_legal_fact": False,
                },
            })
            date = str(item.get("decision_date") or "")
            if date:
                proc_id = f"{case_id}-decision-{evidence_key[:12]}"
                procedures.setdefault(proc_id, {
                    "procedure_id": proc_id, "case_id": case_id,
                    "procedure_type": "decision_issued", "event_date": date,
                    "date_semantics": "decision_issued", "date_status": "pending",
                    "notes": "公告解析/交叉核对所得决定日期，待人工复核。",
                })
                link_id = f"{case_id}-link-{evidence_key[:12]}-decision"
                links.setdefault(link_id, {
                    "link_id": link_id, "case_id": case_id, "evidence_id": evidence_id,
                    "procedure_id": proc_id, "relation_type": "supports_procedure",
                    "notes": "待人工复核；仅支持程序节点，不证明实际终止挂牌日期。",
                })
            conditional_date = str(item.get("conditional_termination_date") or "")
            if conditional_date:
                proc_id = f"{case_id}-conditional-{evidence_key[:12]}"
                procedures.setdefault(proc_id, {
                    "procedure_id": proc_id, "case_id": case_id,
                    "procedure_type": "conditional_termination", "event_date": conditional_date,
                    "date_semantics": "conditional_termination", "date_status": "pending",
                    "notes": "公告所载条件终止日期，不等同实际终止挂牌日期。",
                })
                link_id = f"{case_id}-link-{evidence_key[:12]}-conditional"
                links.setdefault(link_id, {
                    "link_id": link_id, "case_id": case_id, "evidence_id": evidence_id,
                    "procedure_id": proc_id, "relation_type": "supports_procedure",
                    "notes": "待人工复核；不确认实际终止挂牌日期。",
                })
            if not date and not item.get("conditional_termination_date"):
                link_id = f"{case_id}-link-{evidence_key[:12]}-context"
                links.setdefault(link_id, {
                    "link_id": link_id, "case_id": case_id, "evidence_id": evidence_id,
                    "relation_type": "context_only",
                    "notes": "背景名单或未提供程序日期的证据，仅作上下文。",
                })
    return {
        "cases": cases,
        "evidence": list(evidences.values()),
        "procedures": list(procedures.values()),
        "links": list(links.values()),
    }


def apply_import_payload(storage: MarketStorage, payload: dict[str, list[dict]]) -> dict[str, int]:
    """Atomically write all ledger rows and verify every imported primary key."""
    expected_version = str(storage.SCHEMA_VERSION)
    with storage.connect() as connection:
        version_row = connection.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()
        if not version_row or str(version_row["value"]) != expected_version:
            raise RuntimeError(
                f"market DB schema version mismatch: expected {expected_version}, "
                f"found {version_row['value'] if version_row else 'missing'}"
            )
        storage.save_neeq_termination_cases(payload["cases"], connection=connection)
        storage.save_neeq_termination_evidence(payload["evidence"], connection=connection)
        storage.save_neeq_termination_procedures(payload["procedures"], connection=connection)
        storage.link_neeq_termination_evidence(payload["links"], connection=connection)

        checks = (
            ("readback_cases", "neeq_termination_case", "case_id", "cases", "case_id"),
            ("readback_evidence", "neeq_termination_evidence", "evidence_id", "evidence", "evidence_id"),
            ("readback_procedures", "neeq_termination_procedure", "procedure_id", "procedures", "procedure_id"),
            ("readback_links", "neeq_termination_evidence_link", "link_id", "links", "link_id"),
        )
        result = {}
        for label, table, id_column, payload_key, item_key in checks:
            expected_ids = {str(item[item_key]) for item in payload[payload_key]}
            if expected_ids:
                placeholders = ",".join("?" for _ in expected_ids)
                rows = connection.execute(
                    f"SELECT {id_column} FROM {table} WHERE {id_column} IN ({placeholders})",
                    tuple(sorted(expected_ids)),
                ).fetchall()
                actual_ids = {str(row[id_column]) for row in rows}
            else:
                actual_ids = set()
            if actual_ids != expected_ids:
                missing = sorted(expected_ids - actual_ids)
                raise RuntimeError(f"{label} primary-key readback mismatch; missing={missing[:10]}")
            result[label] = len(actual_ids)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-manifest", type=Path, required=True)
    parser.add_argument("--date-audit", type=Path, required=True)
    parser.add_argument("--db", type=Path, default=Path("output/market/market.db"))
    parser.add_argument("--retrieved-at", required=True, help="ISO 8601采集时间，含时区")
    parser.add_argument("--apply", action="store_true", help="实际写入独立NEEQ证据台账；默认仅预览")
    args = parser.parse_args()
    manifest = json.loads(args.candidate_manifest.read_text(encoding="utf-8"))
    audit = json.loads(args.date_audit.read_text(encoding="utf-8"))
    payload = build_import_payload(manifest, audit, retrieved_at=args.retrieved_at)
    summary = {key: len(value) for key, value in payload.items()}
    summary.update({"apply": args.apply, "actual_termination_facts_confirmed": 0})
    if args.apply:
        storage = MarketStorage(str(args.db))
        summary.update(apply_import_payload(storage, payload))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
