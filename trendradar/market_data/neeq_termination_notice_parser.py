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
    elif any(term in compact for term in ("未能按照规定时间披露", "未按规定时间披露", "未披露")):
        category = "failure_to_disclose_periodic_report"
    elif any(term in compact for term in ("已注销公司登记", "公司登记已注销", "完成公司注销登记")):
        category = "company_registration_cancellation"
    else:
        category = "other_or_unclassified"
    registration_cancelled = bool(re.search(r"(?:已注销公司登记|公司登记已注销|完成公司注销登记)", compact))
    return category, legal_basis, registration_cancelled


def _extract_document_number(compact: str) -> str | None:
    match = _first(r"(?:股转系统公告)?[〔［\\[]?(\\d{4})[〕］\\]]?[-—–\\s]*(\\d{1,5})号", compact)
    if not match:
        match = _first(r"(?:^|[^0-9])(?:-\\d+-)?(20\\d{2})[-—–\\s]?(\\d{3,5})(?=[\\u4e00-\\u9fff])", compact)
    return f"{match.group(1)}-{match.group(2)}" if match else None


def _extract_identities(compact: str, original: str) -> list[dict[str, str]]:
    pattern = (
        r"(?P<name>[\\u4e00-\\u9fffA-Za-z0-9（）()·\\-]{2,100}?)"
        r"[（(]证券简称[：:](?P<short>[^；;，,）)]+)[；;，,]证券代码[：:](?P<code>\\d{6})"
    )
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    for match in re.finditer(pattern, compact):
        code = match.group("code")
        if code in seen:
            continue
        seen.add(code)
        found.append({
            "company_name": _clean(match.group("name")),
            "short_name": _clean(match.group("short")),
            "security_code": code,
            "identity_source": "structured_identity_text",
        })

    # HTML栏目页附表常被提取为“证券代码 / 证券简称 / 主办券商”逐行文本，
    # 不一定保留括号身份字段；仅接受6位代码行后紧邻的简称行。
    lines = [line.strip() for line in original.splitlines() if line.strip()]
    for index, line in enumerate(lines[:-1]):
        if not re.fullmatch(r"\\d{6}", line):
            continue
        code = line
        short = re.sub(r"\\s+", "", lines[index + 1])
        if code in seen or not re.fullmatch(r"[\\u4e00-\\u9fffA-Za-z0-9（）()·-]{2,20}", short):
            continue
        # 排除把下一行序号、券商字段或页码当作证券简称的情况。
        if re.fullmatch(r"\\d+", short) or any(word in short for word in ("证券", "公司", "公告")):
            continue
        seen.add(code)
        found.append({
            "company_name": None,
            "short_name": short,
            "security_code": code,
            "identity_source": "html_attachment_table",
        })
    return found


def _parse_shared_fields(block: str) -> dict[str, Any]:
    compact = re.sub(r"\\s+", "", block)
    number = _extract_document_number(compact)
    decision_match = _first(
        r"我司于\\s*" + _DATE + r"\\s*对[\\s\\S]{1,120}?作出终止其股票\\s*挂牌的决定",
        compact,
    )
    if not decision_match:
        # 批量公告可写作“我司决定自YYYY年M月D日对……作出终止决定”。
        decision_match = _first(
            r"我司决定自\\s*" + _DATE + r"\\s*对[\\s\\S]{1,180}?作出终止其股票\\s*挂牌的决定",
            compact,
        )
    planned_match = _first(r"将自\\s*" + _DATE + r"\\s*起终止其股票\\s*挂牌", compact)
    stated_match = _first(
        r"(?:决定自|决定于|自)\\s*" + _DATE + r"\\s*起?终止[其上述]?公司?股票?挂牌",
        compact,
    )
    # 兼容“12月26日起终止”这种沿用前文年份的历史写法。
    if not stated_match:
        short_match = _first(r"(\\d{1,2})月(\\d{1,2})日起终止[上述公司]*股票挂牌", compact)
        if short_match:
            year_match = _first(r"(20\\d{2})年", compact)
            if year_match:
                try:
                    stated_date = datetime(int(year_match.group(1)), int(short_match.group(1)), int(short_match.group(2))).date().isoformat()
                except ValueError:
                    stated_date = None
            else:
                stated_date = None
        else:
            stated_date = None
    else:
        stated_date = _date(stated_match)
    suspension_match = _first(r"(?:于|自)\\s*" + _DATE + r"\\s*对[\\s\\S]{0,100}?暂停转让", compact)
    announcement_match = _first(r"全国中小企业股份转让系统有限责任公司\\s*" + _DATE, compact)
    reason_match = _first(r"未能按照规定\\s*时间披露\\s*(.{2,100}?)\\s*。?\\s*根据", compact)
    reason = _clean(reason_match.group(1)) if reason_match else None
    category, legal_basis, deregistered = _classify_termination(compact, reason)
    conditional = bool(_first(
        r"(?:如|对于|对)[^。]{0,220}?(?:未在规定期限内提交复核申请|未提交复核申请|复核申请未被受理|未在规定期限内提出复核)",
        compact,
    ))
    if not conditional and planned_match and category == "failure_to_disclose_periodic_report":
        conditional = bool(_first(r"(?:逾期未|未按期).{0,30}复核申请", compact))
    return {
        "document_number": number,
        "decision_date": _date(decision_match),
        "planned_termination_date": _date(planned_match),
        "stated_termination_date": stated_date,
        "suspension_date": _date(suspension_match),
        "announcement_date": _date(announcement_match),
        "termination_reason": reason,
        "termination_reason_category": category,
        "termination_legal_basis": legal_basis,
        "conditional_termination": conditional,
        "conditional_termination_language": conditional,
        "notice_states_company_deregistered": deregistered,
    }


def _parse_notice(block: str, index: int) -> dict[str, Any]:
    compact = re.sub(r"\\s+", "", block)
    shared = _parse_shared_fields(block)
    identities = _extract_identities(compact, block)
    if not identities:
        identities = [{"company_name": None, "short_name": None, "security_code": None, "identity_source": "unresolved"}]
    title_match = _first(r"([^\\n]{4,160}(?:公告|决定))", block)
    title = _clean(title_match.group(1)) if title_match else None
    results = []
    for identity in identities:
        record = {
            "notice_index": index,
            "document_number": shared["document_number"],
            "notice_title": title,
            **identity,
            **{key: value for key, value in shared.items() if key != "document_number"},
            "follow_up_evidence_required": True,
            "parse_warnings": [],
        }
        # 兼容v3调用方：planned_termination_date仍保留，但不再混同其他日期语义。
        required = ("security_code", "decision_date", "announcement_date")
        record["parse_warnings"] = [f"missing_{field}" for field in required if not record.get(field)]
        if not record.get("company_name") and not record.get("short_name"):
            record["parse_warnings"].append("missing_company_identity")
        if record.get("termination_reason_category") == "other_or_unclassified":
            record["parse_warnings"].append("unclassified_termination_reason")
        results.append(record)
    return results


def parse_neeq_termination_notice_text(text: str) -> dict:
    """Parse one or more notices and expand each notice into company-level records."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("公告文本为空")
    starts = [match.start() for match in re.finditer(r"股转系统公告", text)]
    blocks = [text[start:(starts[pos + 1] if pos + 1 < len(starts) else len(text))]
              for pos, start in enumerate(starts)] if starts else [text]
    notices = []
    for index, block in enumerate(blocks):
        notices.extend(_parse_notice(block, index))
    return {
        "parser": "neeq_termination_notice_parser_v4",
        "notice_count": len(notices),
        "source_document_block_count": len(blocks),
        "notices": notices,
    }


def _normalise_name(value: Any) -> str:
    return _clean(str(value or "")).replace("（", "(").replace("）", ")")


def crosscheck_neeq_notice_task(parsed: dict, task: dict, download_record: dict | None = None) -> dict:
    """Compare every company-level notice record with linked candidates; review only."""
    linked = task.get("linked_candidates") or []
    notices = parsed.get("notices") or []
    comparisons, unmatched_notices = [], []
    matched_codes: set[str] = set()
    for notice in notices:
        code = notice.get("security_code")
        candidates = [item for item in linked if str(item.get("company_code") or "") == str(code or "")]
        if not candidates:
            unmatched_notices.append({
                "security_code": code,
                "company_name": notice.get("company_name"),
                "short_name": notice.get("short_name"),
                "reason": "notice_code_not_in_linked_candidates",
            })
            continue
        matched_codes.add(str(code))
        for candidate in candidates:
            expected = _normalise_name(candidate.get("company_name"))
            actual = _normalise_name(notice.get("company_name"))
            expected_short = _normalise_name(candidate.get("company_short_name"))
            actual_short = _normalise_name(notice.get("short_name"))
            exact = bool(expected and actual and expected == actual)
            short_match = bool(actual_short and (actual_short == expected or (expected_short and actual_short == expected_short)))
            name_match = exact or short_match
            source_date = str(candidate.get("delisting_date") or "").strip() or None
            comparison_date = notice.get("planned_termination_date") or notice.get("stated_termination_date")
            date_match = (source_date == comparison_date) if source_date and comparison_date else None
            date_status = "matches" if date_match is True else ("mismatch_requires_follow_up" if date_match is False else "insufficient_date_evidence")
            issues = []
            if not name_match:
                issues.append("company_name_mismatch")
            if date_match is False:
                issues.append("result_date_vs_notice_date_mismatch")
            issues.extend(notice.get("parse_warnings") or [])
            comparisons.append({
                "record_id": candidate.get("record_id"),
                "security_code": code,
                "candidate_company_name": candidate.get("company_name"),
                "notice_company_name": notice.get("company_name"),
                "notice_short_name": notice.get("short_name"),
                "company_code_match": True,
                "company_name_match": name_match,
                "company_full_name_exact_match": exact,
                "company_short_name_match": short_match,
                "result_list_date": source_date,
                "notice_planned_termination_date": notice.get("planned_termination_date"),
                "notice_stated_termination_date": notice.get("stated_termination_date"),
                "result_list_date_matches_notice_date": date_match,
                "result_list_date_matches_planned_date": date_match,
                "date_consistency_status": date_status,
                "termination_reason_category": notice.get("termination_reason_category"),
                "termination_legal_basis": notice.get("termination_legal_basis"),
                "conditional_termination": notice.get("conditional_termination"),
                "notice_states_company_deregistered": notice.get("notice_states_company_deregistered"),
                "decision_date": notice.get("decision_date"),
                "announcement_date": notice.get("announcement_date"),
                "suspension_date": notice.get("suspension_date"),
                "follow_up_evidence_required": True,
                "notice_document_number": notice.get("document_number"),
                "issues": sorted(set(issues)),
                "review_status": "pending_manual_review",
                "confirmed_legal_fact": False,
                "formal_lifecycle_write_allowed": False,
            })
    missing_codes = sorted({str(item.get("company_code") or "") for item in linked
                            if str(item.get("company_code") or "") not in matched_codes})
    requested_url = (download_record or {}).get("requested_url")
    url_match = requested_url == task.get("url") if requested_url else None
    download_ok = bool(download_record and download_record.get("download_status") == "downloaded"
                       and download_record.get("text_extraction_status") == "extracted" and url_match is True)
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


def build_neeq_notice_review_report(manifest: dict, download_state: dict, text_loader, *, generated_at: str | None = None) -> dict:
    """Build a read-only report for downloaded tasks."""
    results = download_state.get("results") or {}
    items = []
    for task in manifest.get("tasks") or []:
        attachment_id = str(task.get("attachment_id") or "")
        record = results.get(attachment_id) or {}
        if record.get("download_status") != "downloaded":
            continue
        if record.get("text_extraction_status") != "extracted" or not record.get("text_file"):
            items.append({
                "attachment_id": attachment_id, "url": task.get("url"),
                "download_status": record.get("download_status"),
                "text_extraction_status": record.get("text_extraction_status"),
                "error": record.get("text_extraction_error"),
                "review_required": True, "confirmed_legal_fact": False,
                "formal_lifecycle_write_allowed": False,
            })
            continue
        parsed = parse_neeq_termination_notice_text(text_loader(record["text_file"]))
        item = crosscheck_neeq_notice_task(parsed, task, record)
        item["notices"] = parsed["notices"]
        item["source_document_block_count"] = parsed["source_document_block_count"]
        items.append(item)
    comparisons = [c for item in items for c in item.get("comparisons", [])]
    parsed_notices = [n for item in items for n in item.get("notices", [])]

    def counts(records):
        output = {}
        for record in records:
            category = str(record.get("termination_reason_category") or "unknown")
            output[category] = output.get(category, 0) + 1
        return output

    return {
        "schema_version": 3,
        "parser_version": "neeq_termination_notice_parser_v4",
        "source": "neeq_official_termination_result_list_and_notice_attachments",
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "attachment_count": len(items),
        "source_document_block_count": sum(item.get("source_document_block_count", 0) for item in items),
        "parsed_notice_count": len(parsed_notices),
        "comparison_count": len(comparisons),
        "termination_reason_category_counts": counts(parsed_notices),
        "parsed_notice_reason_category_counts": counts(parsed_notices),
        "matched_comparison_reason_category_counts": counts(comparisons),
        "unmatched_notice_count": sum(len(item.get("unmatched_notices", [])) for item in items),
        "conditional_termination_count": sum(bool(n.get("conditional_termination")) for n in parsed_notices),
        "matched_comparison_conditional_termination_count": sum(bool(c.get("conditional_termination")) for c in comparisons),
        "notice_registration_cancellation_count": sum(bool(n.get("notice_states_company_deregistered")) for n in parsed_notices),
        "matched_comparison_registration_cancellation_count": sum(bool(c.get("notice_states_company_deregistered")) for c in comparisons),
        "date_mismatch_count": sum(c.get("date_consistency_status") == "mismatch_requires_follow_up" for c in comparisons),
        "follow_up_evidence_required_count": sum(bool(n.get("follow_up_evidence_required")) for n in parsed_notices),
        "matched_comparison_follow_up_evidence_required_count": sum(bool(c.get("follow_up_evidence_required")) for c in comparisons),
        "items": items,
        "safety": {
            "network_downloads": 0, "database_writes": 0, "formal_lifecycle_writes": 0,
            "legal_fact_confirmations": 0, "manual_review_required": True, "read_only": True,
        },
    }
