# 本文件解析NEEQ终止挂牌公告文本并与官方结果候选交叉核验，只输出待审核报告，不写数据库。
"""Parse NEEQ termination notices and cross-check them against result-list candidates."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any


_DATE = r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"


def _date(match: re.Match | None) -> str | None:
    if not match:
        return None
    try:
        return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3))).date().isoformat()
    except ValueError:
        return None


def _clean(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip("；;，,。 ")


def _first(pattern: str, text: str, flags: int = re.I) -> re.Match | None:
    return re.search(pattern, text, flags)


def _classify_termination(compact: str, reason: str | None) -> tuple[str, str | None, bool]:
    legal_match = _first(
        r"第\s*4\s*[.．]\s*5\s*[.．]\s*1\s*条\s*第\s*[（(]?\s*[一二三四五六七八九十0-9]+\s*[）)]?\s*项",
        compact,
    )
    legal_basis = _clean(legal_match.group(0)) if legal_match else None
    if "破产清算" in compact:
        category = "bankruptcy_liquidation"
    elif "主动解散清算" in compact or ("解散清算" in compact and "破产" not in compact):
        category = "voluntary_dissolution_liquidation"
    elif any(term in compact for term in ("不符合整改要求", "不符合整改条件", "不符合自查整改条件", "不符合自查整改", "未完成整改")):
        category = "failure_to_meet_rectification_requirements"
    elif any(term in compact for term in ("未能按照规定时间披露", "未按规定时间披露", "未披露")):
        category = "failure_to_disclose_periodic_report"
    elif any(term in compact for term in ("已注销公司登记", "公司登记已注销", "完成公司注销登记")):
        category = "company_registration_cancellation"
    else:
        category = "other_or_unclassified"
    registration_cancelled = bool(re.search(r"(?:已注销公司登记|公司登记已注销|完成公司注销登记)", compact))
    return category, legal_basis, registration_cancelled


def _extract_document_number(document_text: str) -> str | None:
    """Extract a notice number from the opening header, never from body citations."""
    lines = [re.sub(r"\s+", "", line).strip() for line in document_text.splitlines()]
    lines = [line for line in lines if line]
    # Keep document-number discovery close to the opening header. A sponsor
    # notice may cite older 股转系统公告 numbers much later in its body.
    header_lines = lines[:8]

    if header_lines and "股转系统公告" in header_lines[0]:
        for index, line in enumerate(header_lines[:-1]):
            if not re.fullmatch(r"20\d{2}", line):
                continue
            serial_match = re.fullmatch(r"(\d{3,5})(?:号)?", header_lines[index + 1])
            if serial_match:
                return f"{line}-{serial_match.group(1)}"

    for line in header_lines:
        # A bracketed year/serial is only a document number when explicitly
        # labelled as an announcement number, not a cited regulatory circular.
        if "公告编号" in line:
            bracketed = re.search(r"[〔［\[]?(20\d{2})[〕］\]](\d{1,5})(?:号)?", line)
            if bracketed:
                return f"{bracketed.group(1)}-{bracketed.group(2)}"

        conventional = re.search(r"(?:^|[^0-9])(?:-\d+-)?(20\d{2})[-—–](\d{1,5})(?:号)?", line)
        if conventional and ("公告编号" in line or "股转系统公告" in line):
            return f"{conventional.group(1)}-{conventional.group(2)}"

        labelled = re.search(r"股转系统公告[〔［\[]?(20\d{2})[〕］\]]?(\d{1,5})(?:号)?", line)
        if labelled:
            return f"{labelled.group(1)}-{labelled.group(2)}"

    compact_header = "".join(header_lines[:4])
    # Keep v9's bounded compact-PDF pattern; do not join arbitrary body lines.
    compact_match = re.search(r"-\d+-(20\d{2})(\d{3,5})(?=[\u4e00-\u9fff])", compact_header)
    if compact_match:
        return f"{compact_match.group(1)}-{compact_match.group(2)}"
    return None


def _extract_html_table_identities(source: str) -> list[dict[str, str]]:
    """Extract code/name pairs from one HTML table text section."""
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    lines = [re.sub(r"\s+", "", line.strip()) for line in source.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        code_match = re.fullmatch(r"(\d{6})", line)
        if not code_match:
            continue
        code = code_match.group(1)
        if code in seen:
            continue
        short = None
        if index + 1 < len(lines):
            next_line = lines[index + 1]
            if re.fullmatch(r"[\u4e00-\u9fffA-Za-z0-9（）()·-]{2,20}", next_line) and len(re.findall(r"[\u4e00-\u9fff]", next_line)) >= 2 and not re.search(r"\d{4}", next_line) and next_line not in ("主办券商", "证券简称"):
                short = next_line
        if short is None:
            continue
        seen.add(code)
        found.append({
            "company_name": None,
            "short_name": short,
            "security_code": code,
            "identity_source": "html_attachment_table",
        })
    return found


def _extract_identities(compact: str, original: str, *, table_source: str | None = None) -> list[dict[str, str | None]]:
    """Extract identities from standard and issuer-style NEEQ announcement layouts."""
    found: list[dict[str, str | None]] = []
    seen: set[str] = set()
    identity_pattern = re.compile(
        r"[（(]证券简称[：:](?P<short>[^；;，,）)]+)[；;，,]证券代码[：:](?P<code>\d{6})"
    )
    for match in identity_pattern.finditer(compact):
        code = match.group("code")
        if code in seen:
            continue
        prefix = compact[:match.start()]
        boundaries = [prefix.rfind(token) + len(token) for token in ("号", "公告", "决定", "终止")]
        boundary = max(boundaries)
        name_part = prefix[boundary:]
        name_part = re.sub(r"^.*?\d(?=[\u4e00-\u9fff])", "", name_part)
        name_match = re.search(
            r"(?P<name>[\u4e00-\u9fffA-Za-z0-9（）()·-]{2,60}?(?:股份有限公司|有限责任公司|有限公司))$",
            name_part,
        )
        company_name = _clean(name_match.group("name")) if name_match else None
        seen.add(code)
        found.append({
            "company_name": company_name,
            "short_name": _clean(match.group("short")),
            "security_code": code,
            "identity_source": "structured_identity_text",
        })

    # Issuer announcements often place the security code before the short name.
    code_first = re.compile(
        r"证券代码[：:](?P<code>\d{6})[^。；;]{0,40}?证券简称[：:](?P<short>[^主办券商；;，,]{1,20})"
    )
    for match in code_first.finditer(compact):
        code = match.group("code")
        if code in seen:
            continue
        # Prefer the issuer name from its own title; this avoids accidentally
        # absorbing the sponsor-broker label into the company name.
        # Resolve the issuer from its own standalone title line. Searching the
        # whitespace-stripped whole document can concatenate sponsor and issuer names.
        title_company = None
        source_lines = [re.sub(r"\s+", "", line).strip() for line in original.splitlines()]
        for line_index, line in enumerate(source_lines):
            if not re.search(r"关于.*(?:股票|终止挂牌)", line):
                continue
            for previous_line in reversed(source_lines[:line_index]):
                if re.fullmatch(r"[\u4e00-\u9fffA-Za-z0-9（）()·-]{4,60}?(?:股份有限公司|有限责任公司|有限公司)", previous_line):
                    title_company = re.match(r"(?P<name>.+)", previous_line)
                    break
            if title_company:
                break
        if not title_company:
            tail = compact[match.end():]
            tail = re.sub(r"^主办券商[：:][^青]{1,20}", "", tail)
            title_company = re.search(
                r"(?P<name>[\u4e00-\u9fffA-Za-z0-9（）()·-]{4,60}?(?:股份有限公司|有限责任公司|有限公司))",
                tail,
            )
        company_name = _clean(title_company.group("name")) if title_company else None
        seen.add(code)
        found.append({
            "company_name": company_name,
            "short_name": _clean(match.group("short")),
            "security_code": code,
            "identity_source": "issuer_header_code_first",
        })

    # Sponsor/broker notices may identify the issuer in the title and define
    # an alias in the body, without publishing a security code.
    if not found:
        title_company = re.search(
            r"关于(?P<name>[\u4e00-\u9fffA-Za-z0-9（）()·-]{4,60}?(?:股份有限公司|有限责任公司|有限公司))的(?:风险提示|终止挂牌)",
            compact,
        )
        if title_company:
            company_name = _clean(title_company.group("name"))
            tail = compact[title_company.end():]
            # The sponsor itself may be introduced first as “主办券商”.
            # Resolve the issuer alias only after the second occurrence of the
            # issuer's full name, not from the sponsor's own definition.
            issuer_position = tail.find(company_name)
            issuer_context = tail[issuer_position + len(company_name):] if issuer_position >= 0 else tail
            alias = re.search(r"以下简称[“\"「]?([^”\"」、，,；;）)]+)", issuer_context)
            found.append({
                "company_name": company_name,
                "short_name": _clean(alias.group(1)) if alias else None,
                "security_code": None,
                "identity_source": "issuer_title_and_alias",
            })

    for identity in _extract_html_table_identities(table_source if table_source is not None else original):
        if identity["security_code"] not in seen:
            seen.add(identity["security_code"])
            found.append(identity)
    return found


def _select_decision_table_scope(original: str) -> tuple[str, str]:
    """Separate a background appendix from the later table listing actual decisions.

    Some NEEQ batch announcements first list all affected/non-compliant issuers,
    then provide a smaller appendix containing only issuers for which a
    termination decision was made. The earlier list is context, not a set of
    termination notices.
    """
    marker_pattern = re.compile(
        r"(?:附表\s*[：:]?\s*)?2\s*[.．、]?\s*(?:于)?20\d{2}年"
    )
    matches = list(marker_pattern.finditer(original))
    if not matches:
        return original, ""
    marker = matches[-1]
    return original[marker.end():], original[:marker.start()]


def _parse_shared_fields(block: str) -> dict[str, Any]:
    compact = re.sub(r"\s+", "", block)
    # Remove isolated PDF page markers, including those inserted inside dates.
    date_compact = re.sub(r"(?<=月)-\d+-(?=\d{1,2}日)", "", compact)
    date_compact = re.sub(r"(?<!\d)-\d+-(?!\d)", "", date_compact)
    number = _extract_document_number(block)

    decision_match = _first(
        r"我司于\s*" + _DATE + r"\s*对[\s\S]{1,120}?作出终止其股票\s*挂牌的决定",
        date_compact,
    )
    if not decision_match:
        decision_match = _first(
            r"我司决定自\s*" + _DATE + r"\s*对[\s\S]{1,180}?作出终止其股票\s*挂牌的决定",
            date_compact,
        )
    received_match = _first(
        r"(?:公司)?于\s*" + _DATE + r"\s*收到[\s\S]{0,100}?(?:终止|挂牌)?决定",
        date_compact,
    )

    announcement_match = _first(
        r"(?:全国中小企业股份转让系统有限责任公司|全国股转公司)\s*" + _DATE,
        date_compact,
    )
    announcement_date = _date(announcement_match)

    # Do not let “决定自/决定于” consume a decision or suspension date
    # when a later date in the same sentence is the actual termination date.
    planned_match = _first(
        r"(?:将自|将于|(?<!决定)自)\s*" + _DATE + r"\s*起?[\s\S]{0,160}?终止(?:其|上述)?(?:公司)?(?:的)?股票?(?:在[^。；;]{0,80})?挂牌",
        date_compact,
    )
    if not planned_match:
        planned_match = _first(
            r"股票(?:将)?于\\s*" + _DATE + r"\\s*起?在全国中小企业股份转让系统终止挂牌",
            date_compact,
        )
    planned_date = _date(planned_match)
    if not planned_date:
        short_planned_match = _first(
            r"(\d{1,2})月(\d{1,2})日(?:起)?终止[^。]{0,100}?(?:股票挂牌|挂牌)",
            date_compact,
        )
        if short_planned_match and announcement_date:
            try:
                planned_date = datetime(
                    int(announcement_date[:4]),
                    int(short_planned_match.group(1)),
                    int(short_planned_match.group(2)),
                ).date().isoformat()
            except ValueError:
                planned_date = None
        elif short_planned_match:
            year_match = _first(r"(20\d{2})年", date_compact)
            if year_match:
                try:
                    planned_date = datetime(
                        int(year_match.group(1)),
                        int(short_planned_match.group(1)),
                        int(short_planned_match.group(2)),
                    ).date().isoformat()
                except ValueError:
                    planned_date = None

    suspension_match = _first(
        r"(?:于|自|已于)\s*" + _DATE + r"\s*(?:对[\s\S]{0,100}?)?(?:暂停转让|停牌)",
        date_compact,
    )
    resumption_match = _first(
        r"(?:" + _DATE + r"\s*起恢复转让|" + _DATE + r"\s*起复牌)",
        date_compact,
    )

    stated_match = _first(
        r"(?:将自|将于|(?<!决定)自)\s*" + _DATE + r"\s*起?[\s\S]{0,160}?终止(?:其|上述)?(?:公司)?(?:的)?股票?(?:在[^。；;]{0,80})?挂牌",
        date_compact,
    )
    if not stated_match:
        full_event_match = _first(
            _DATE + r"(?:起)?(?:(?!(?:20\d{2}年)?\d{1,2}月\d{1,2}日)[^。；;]){0,80}?终止[^。；;]{0,30}?(?:股票挂牌|挂牌)",
            date_compact,
        )
        if full_event_match:
            stated_date = _date(full_event_match)
        else:
            short_match = _first(
                r"(\d{1,2})月(\d{1,2})日(?:起)?终止[^。]{0,80}?(?:股票挂牌|挂牌)",
                date_compact,
            )
            year_match = _first(r"(20\d{2})年", date_compact)
            if short_match and year_match:
                try:
                    stated_date = datetime(
                        int(year_match.group(1)),
                        int(short_match.group(1)),
                        int(short_match.group(2)),
                    ).date().isoformat()
                except ValueError:
                    stated_date = None
            else:
                stated_date = None
    else:
        stated_date = _date(stated_match)

    reason_match = _first(r"未能按照规定\s*时间披露\s*(.{2,100}?)\s*。?\s*根据", compact)
    category, legal_basis, deregistered = _classify_termination(compact, _clean(reason_match.group(1)) if reason_match else None)

    signature_lines = [line for line in block.splitlines() if line.strip()][-6:]
    signature_text = "".join(re.sub(r"\s+", "", line) for line in signature_lines)
    signature_dates = list(re.finditer(_DATE, signature_text))
    signature_date = _date(signature_dates[-1]) if signature_dates else None
    # Issuer and sponsor announcements commonly use the board/sponsor signature
    # date as the publication date; unlike NEEQ system notices, they do not carry
    # the system-name publication footer parsed above.
    if announcement_date is None and signature_date is not None:
        announcement_date = signature_date

    conditional = bool(_first(
        r"(?:如|对于|对)[^。]{0,220}?(?:未在规定期限内提交复核申请|未提交复核申请|复核申请未被受理|未在规定期限内提出复核)",
        compact,
    ))
    if not conditional and planned_match and category == "failure_to_disclose_periodic_report":
        conditional = bool(_first(r"(?:逾期未|未按期).{0,30}复核申请", compact))

    return {
        "document_number": number,
        "decision_date": _date(decision_match),
        "decision_received_date": _date(received_match),
        "document_signature_date": signature_date,
        "transfer_resumption_date": _date(resumption_match),
        "planned_termination_date": planned_date,
        "stated_termination_date": stated_date,
        "suspension_date": _date(suspension_match),
        "announcement_date": announcement_date,
        "termination_reason": _clean(reason_match.group(1)) if reason_match else None,
        "termination_reason_category": category,
        "termination_legal_basis": legal_basis,
        "conditional_termination": conditional,
        "conditional_termination_language": conditional,
        "notice_states_company_deregistered": deregistered,
    }


def _parse_notice(block: str, index: int, *, table_source: str | None = None) -> dict[str, Any]:
    compact = re.sub(r"\s+", "", block)
    shared = _parse_shared_fields(block)
    identities = _extract_identities(compact, block, table_source=table_source)
    if not identities:
        identities = [{"company_name": None, "short_name": None, "security_code": None, "identity_source": "unresolved"}]
    title_match = _first(r"([^\n]{4,160}(?:公告|决定))", block)
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
    # A body citation can contain the same phrase as the document header.
    # First inspect the opening header as a whole (including the number on
    # the same line as "股转系统公告"). If it is a real numbered document,
    # retain the title/header and split only after a complete prior signature.
    opening_header = bool(starts and starts[0] <= 120)
    if opening_header:
        boundaries = [0]
        last_boundary = 0
        signature_pattern = re.compile(
            r"全国中小企业股份转让系统有限责任公司[\s\S]{0,120}" + _DATE
        )
        for position in starts[1:]:
            preceding_block = text[last_boundary:position]
            if signature_pattern.search(preceding_block):
                boundaries.append(position)
                last_boundary = position
        blocks = [
            text[start:(boundaries[index + 1] if index + 1 < len(boundaries) else len(text))]
            for index, start in enumerate(boundaries)
        ]
    else:
        # A citation in a sponsor/issuer body is not a document boundary.
        # Only split when the first system-notice marker is in the opening header.
        blocks = [text]
    notices = []
    contextual_identities = []
    for index, block in enumerate(blocks):
        decision_table, context_table = _select_decision_table_scope(block)
        notices.extend(_parse_notice(block, index, table_source=decision_table))
        if context_table:
            contextual_identities.extend(_extract_html_table_identities(context_table))
    return {
        "parser": "neeq_termination_notice_parser_v10",
        "notice_count": len(notices),
        "source_document_block_count": len(blocks),
        "contextual_identity_count": len(contextual_identities),
        "contextual_identities": contextual_identities,
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
                issues.append("result_date_vs_planned_date_mismatch")
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
        item["contextual_identity_count"] = parsed.get("contextual_identity_count", 0)
        item["contextual_identities"] = parsed.get("contextual_identities", [])
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
        "parser_version": "neeq_termination_notice_parser_v10",
        "source": "neeq_official_termination_result_list_and_notice_attachments",
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "attachment_count": len(items),
        "source_document_block_count": sum(item.get("source_document_block_count", 0) for item in items),
        "contextual_identity_count": sum(item.get("contextual_identity_count", 0) for item in items),
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
