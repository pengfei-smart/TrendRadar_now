# 本文件对NEEQ公告分页结果执行本地证券身份匹配和覆盖审计，不依赖未经验证的服务端代码/关键词筛选。
"""Scan bounded NEEQ announcement pages and match issuer identities locally."""

from __future__ import annotations

import math
import re
import unicodedata
from typing import Iterable

from trendradar.market_data.neeq_lifecycle_collector import is_lifecycle_announcement_title


def normalize_issuer_name(value: str) -> str:
    """Normalize spacing and punctuation for conservative exact-name matching."""
    if not isinstance(value, str):
        return ""
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[\W_]+", "", normalized, flags=re.UNICODE)


def match_neeq_announcement_records(
    records: Iterable[dict],
    *,
    target_codes: Iterable[str] = (),
    target_names: Iterable[str] = (),
) -> list[dict]:
    """Match only exact security codes or normalized exact issuer names.

    No substring/fuzzy matching is used: false-positive identity links are more
    harmful than missed candidates in a lifecycle evidence workflow.
    """
    codes = {str(value).strip() for value in target_codes if str(value).strip()}
    names = {
        normalize_issuer_name(str(value))
        for value in target_names
        if normalize_issuer_name(str(value))
    }
    matched = []
    for raw in records:
        if not isinstance(raw, dict):
            continue
        record = dict(raw)
        code = str(record.get("companyCd") or "").strip()
        name = str(record.get("companyName") or "").strip()
        methods = []
        if code and code in codes:
            methods.append("exact_security_code")
        if name and normalize_issuer_name(name) in names:
            methods.append("exact_normalized_company_name")
        if methods:
            record["_identity_match_methods"] = methods
            matched.append(record)
    return matched


def lifecycle_records(records: Iterable[dict]) -> list[dict]:
    """Return lifecycle-title records only; this is a review filter, not a fact."""
    result = []
    for raw in records:
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("disclosureTitle") or "").strip()
        if not is_lifecycle_announcement_title(title):
            continue
        source_record = {key: value for key, value in raw.items() if key != "_identity_match_methods"}
        result.append({
            "company_code": str(raw.get("companyCd") or ""),
            "company_name": str(raw.get("companyName") or ""),
            "title": title,
            "publish_date": str(raw.get("publishDate") or ""),
            "file_path": str(raw.get("destFilePath") or ""),
            "file_extension": str(raw.get("fileExt") or ""),
            "record": source_record,
        })
    return result


def page_coverage(total_elements: int, page_size: int, pages_scanned: int) -> dict:
    """Calculate honest page coverage; zero/unknown page size is incomplete."""
    if total_elements < 0 or page_size < 0 or pages_scanned < 0:
        raise ValueError("分页覆盖参数不得为负数")
    total_pages = math.ceil(total_elements / page_size) if page_size else 0
    if total_elements == 0:
        coverage_status = "empty_response_unverified"
        coverage_ratio = None
    elif page_size > 0 and pages_scanned >= total_pages:
        coverage_status = "complete"
        coverage_ratio = 1.0
    else:
        coverage_status = "partial"
        coverage_ratio = min(1.0, pages_scanned / total_pages) if total_pages else 0.0
    return {
        "total_elements": total_elements,
        "page_size": page_size,
        "total_pages": total_pages,
        "pages_scanned": pages_scanned,
        "coverage_ratio": coverage_ratio,
        "coverage_status": coverage_status,
    }
