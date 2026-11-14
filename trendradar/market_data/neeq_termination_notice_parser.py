# 本文件解析NEEQ终止挂牌公告文本并与官方结果候选交叉核验，只输出待审核报告，不写数据库。
"""Parse NEEQ termination notices and cross-check them against result-list candidates."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any


_DATE = r"(\\d{4})\\s*年\\s*(\\d{1,2})\\s*月\\s*(\\d{1,2})\\s*日"


def _date(match: re.Match | None) -> str | None:
    if not match:
        return None
    try:
        return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3))).date().isoformat()
    except ValueError:
        return None


def _clean(value: str) -> str:
    return re.sub(r"\\s+", "", str(value or "")).strip("；;，,。 ")


def _first(pattern: str, text: str, flags: int = re.I) -> re.Match | None:
    return re.search(pattern, text, flags)


def _classify_termination(compact: str, reason: str | None) -> tuple[str, str | None, bool]:
    legal_match = _first(
        r"第\\s*4\\s*[.．]\\s*5\\s*[.．]\\s*1\\s*条\\s*第\\s*[（(]?\\s*[一二三四五六七八九十0-9]+\\s*[）)]?\\s*项",
        compact,
    )
    legal_basis = _clean(legal_match.group(0)) if legal_match else None
    if "破产清算" in compact:
        category = "bankruptcy_liquidation"
    elif "主动解散清算" in compact or ("解散清算" in compact and "破产" not in compact):
        category = "voluntary_dissolution_liquidation"
    elif "未能按照规定时间披露" in compact or "未按规定时间披露" in compact:
        category = "failure_to_disclose_periodic_report"
    elif "已注销公司登记" in compact or "公司登记已注销" in compact:
        category = "company_registration_cancellation"
    else:
        category = "other_or_unclassified"
    registration_cancelled = bool(
        re.search(r"(?:已注销公司登记|公司登记已注销|完成公司注销登记)", compact)
    )
    return category, legal_basis, registration_cancelled


def _parse_notice(block: str, index: int) -> dict[str, Any]:
    # PDF文本层可能在汉字之间插入空格或换行；匹配时压缩空白，原文仍由上层保留。
    compact = re.sub(r"\\s+", "", block)
    number_match = _first(r"股转系统公告(\\d{4})(\\d{1,5})号", compact)
    identity_source = compact[number_match.end():] if number_match else compact
    identity_match = _first(
        r"(?P<name>[\\u4e00-\\u9fffA-Za-z0-9（）()·\\-]{2,100}?)[（(]证券简称[：:](?P<short>[^；;）)]+)[；;]证券代码[：:](?P<code>\\d{6})",
        identity_source,
    )
    decision_match = _first(
        r"我司于\\s*" + _DATE + r"\\s*对[\\s\\S]{1,100}?作出终止其股票\\s*挂牌的决定",
        compact,
    )
    planned_match = _first(
        r"将自\\s*" + _DATE + r"\\s*起终止其股票\\s*挂牌",
        compact,
    )
    announcement_match = _first(
        r"全国中小企业股份转让系统有限责任公司\\s*" + _DATE,
        compact,
    )
    reason_match = _first(
        r"未能按照规定\\s*时间披露\\s*(.{2,100}?)\\s*。?\\s*根据",
        compact,
    )
    reason = _clean(reason_match.group(1)) if reason_match else None
    reason_category, legal_basis, registration_cancelled = _classify_termination(compact, reason)
    conditional = bool(
        _first(r"如.{0,160}?(?:未在规定期限内提交复核申请|未提交复核申请|复核申请未被受理)", compact)
    )
    if not conditional and planned_match and reason_category == "failure_to_disclose_periodic_report":
        # 某些公告使用“逾期未申请复核”等等价条件表达。
        conditional = bool(_first(r"(?:逾期未|未按期).{0,30}复核申请", compact))
    return {
        "notice_index": index,
        "document_number": (
            f"{number_match.group(1)}-{number_match.group(2)}" if number_match else None
        ),
        "company_name": _clean(identity_match.group("name")) if identity_match else None,
        "short_name": _clean(identity_match.group("short")) if identity_match else None,
        "security_code": identity_match.group("code") if identity_match else None,
        "decision_date": _date(decision_match),
        "planned_termination_date": _date(planned_match),
        "announcement_date": _date(announcement_match),
        "termination_reason": reason,
        "termination_reason_category": reason_category,
        "termination_legal_basis": legal_basis,
        "conditional_termination": conditional,
        "conditional_termination_language": conditional,
        "notice_states_company_deregistered": registration_cancelled,
        "follow_up_evidence_required": bool(conditional or not registration_cancelled),
        "parse_warnings": [],
    }


def parse_neeq_termination_notice_text(text: str) -> dict:
    """Parse one or more notices from extracted text; absent fields remain null."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("公告文本为空")
    starts = [match.start() for match in re.finditer(r"股转系统公告", text)]
    if not starts:
        starts = [0]
    blocks = [
        text[start:(starts[pos + 1] if pos + 1 < len(starts) else len(text))]
        for pos, start in enumerate(starts)
    ]
    notices = [_parse_notice(block, index) for index, block in enumerate(blocks)]
    for notice in notices:
        required = ("security_code", "company_name", "decision_date", "planned_termination_date")
        notice["parse_warnings"] = [f"missing_{field}" for field in required if not notice.get(field)]
    return {
        "parser": "neeq_termination_notice_parser_v2",
        "notice_count": len(notices),
        "notices": notices,
    }


def _normalise_name(value: Any) -> str:
    return _clean(str(value or "")).replace("（", "(").replace("）", ")")


def crosscheck_neeq_notice_task(
    parsed: dict, task: dict, download_record: dict | None = None,
) -> dict:
    """Compare parsed notice identities/dates with all candidate links on an attachment task."""
    linked = task.get("linked_candidates") or []
    notices = parsed.get("notices") or []
    comparisons = []
    unmatched_notices = []
    matched_codes = set()
    for notice in notices:
        code = notice.get("security_code")
        candidates = [item for item in linked if str(item.get("company_code") or "") == str(code or "")]
        if not candidates:
            unmatched_notices.append({
                "security_code": code,
                "company_name": notice.get("company_name"),
                "reason": "notice_code_not_in_linked_candidates",
            })
            continue
        matched_codes.add(str(code))
        for candidate in candidates:
            expected_name = _normalise_name(candidate.get("company_name"))
            actual_name = _normalise_name(notice.get("company_name"))
            expected_short_name = _normalise_name(candidate.get("company_short_name"))
            notice_short_name = _normalise_name(notice.get("short_name"))
            exact_full_name_match = bool(expected_name and actual_name and expected_name == actual_name)
            short_name_match = bool(
                notice_short_name and (
                    notice_short_name == expected_name
                    or (expected_short_name and notice_short_name == expected_short_name)
                )
            )
            name_match = exact_full_name_match or short_name_match
            source_date = str(candidate.get("delisting_date") or "").strip() or None
            planned_date = notice.get("planned_termination_date")
            date_match = (source_date == planned_date) if source_date and planned_date else None
            if date_match is True:
                date_status = "matches"
            elif date_match is False:
                date_status = "mismatch_requires_follow_up"
            else:
                date_status = "insufficient_date_evidence"
            issues = []
            if not name_match:
                issues.append("company_name_mismatch")
            if date_match is False:
                issues.append("result_date_vs_planned_date_mismatch")
            if notice.get("parse_warnings"):
                issues.extend(notice["parse_warnings"])
            follow_up = bool(
                notice.get("follow_up_evidence_required")
                or date_status == "mismatch_requires_follow_up"
                or not name_match
            )
            comparisons.append({
                "record_id": candidate.get("record_id"),
                "security_code": code,
                "candidate_company_name": candidate.get("company_name"),
                "notice_company_name": notice.get("company_name"),
                "company_code_match": True,
                "company_name_match": name_match,
                "company_full_name_exact_match": exact_full_name_match,
                "company_short_name_match": short_name_match,
                "result_list_date": source_date,
                "notice_planned_termination_date": planned_date,
                "result_list_date_matches_planned_date": date_match,
                "date_consistency_status": date_status,
                "termination_reason_category": notice.get("termination_reason_category"),
                "termination_legal_basis": notice.get("termination_legal_basis"),
                "conditional_termination": notice.get("conditional_termination"),
                "notice_states_company_deregistered": notice.get("notice_states_company_deregistered"),
                "follow_up_evidence_required": follow_up,
                "notice_document_number": notice.get("document_number"),
                "decision_date": notice.get("decision_date"),
                "announcement_date": notice.get("announcement_date"),
                "issues": sorted(set(issues)),
                "review_status": "pending_manual_review",
                "confirmed_legal_fact": False,
                "formal_lifecycle_write_allowed": False,
            })
    missing_codes = sorted({
        str(item.get("company_code") or "") for item in linked
        if str(item.get("company_code") or "") not in matched_codes
    })
    requested_url = (download_record or {}).get("requested_url")
    url_match = (requested_url == task.get("url")) if requested_url else None
    download_ok = bool(
        download_record
        and download_record.get("download_status") == "downloaded"
        and download_record.get("text_extraction_status") == "extracted"
        and url_match is True
    )
    return {
        "attachment_id": task.get("attachment_id"),
        "url": task.get("url"),
        "download_status": (download_record or {}).get("download_status", "missing"),
        "text_extraction_status": (download_record or {}).get("text_extraction_status", "missing"),
        "download_and_extraction_verified": download_ok,
        "requested_url": requested_url,
        "download_url_matches_manifest": url_match,
        "document_sha256": (download_record or {}).get("content_sha256"),
        "parsed_notice_count": len(notices),
        "comparisons": comparisons,
        "unmatched_notices": unmatched_notices,
        "linked_candidate_codes_without_notice": missing_codes,
        "review_required": True,
        "confirmed_legal_fact": False,
        "formal_lifecycle_write_allowed": False,
    }


def build_neeq_notice_review_report(
    manifest: dict, download_state: dict, text_loader, *, generated_at: str | None = None,
) -> dict:
    """Build a report for downloaded tasks; text_loader receives the state text filename."""
    results = download_state.get("results") or {}
    items = []
    for task in manifest.get("tasks") or []:
        attachment_id = str(task.get("attachment_id") or "")
        record = results.get(attachment_id) or {}
        if record.get("download_status") != "downloaded":
            continue
        if record.get("text_extraction_status") != "extracted" or not record.get("text_file"):
            items.append({
                "attachment_id": attachment_id,
                "url": task.get("url"),
                "download_status": record.get("download_status"),
                "text_extraction_status": record.get("text_extraction_status"),
                "error": record.get("text_extraction_error"),
                "review_required": True,
                "confirmed_legal_fact": False,
                "formal_lifecycle_write_allowed": False,
            })
            continue
        text = text_loader(record["text_file"])
        parsed = parse_neeq_termination_notice_text(text)
        item = crosscheck_neeq_notice_task(parsed, task, record)
        item["notices"] = parsed["notices"]
        items.append(item)
    return {
        "schema_version": 2,
        "source": "neeq_official_termination_result_list_and_notice_attachments",
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "attachment_count": len(items),
        "parsed_notice_count": sum(item.get("parsed_notice_count", 0) for item in items),
        "comparison_count": sum(len(item.get("comparisons", [])) for item in items),
        "items": items,
        "safety": {
            "network_downloads": 0,
            "database_writes": 0,
            "formal_lifecycle_writes": 0,
            "legal_fact_confirmations": 0,
            "manual_review_required": True,
            "read_only": True,
        },
    }
