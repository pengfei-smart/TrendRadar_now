# 本文件审计NEEQ官方终止挂牌结果名录的分页覆盖、重复记录和分页元数据一致性，不写数据库。
"""Audit pagination coverage and duplicate records in the official NEEQ result list."""

from __future__ import annotations

from collections import Counter
from typing import Iterable


def audit_neeq_termination_pages(pages: Iterable[object], *, expected_total_pages: int | None = None,
                                expected_total_elements: int | None = None,
                                start_page: int = 0, max_records: int | None = None) -> dict:
    pages = list(pages)
    if start_page < 0:
        raise ValueError("start_page 不能小于零")
    page_numbers = [getattr(page, "page_number", None) for page in pages]
    if any(isinstance(number, bool) or not isinstance(number, int) or number < 0 for number in page_numbers):
        raise ValueError("分页结果包含无效页码")
    if len(set(page_numbers)) != len(page_numbers):
        raise ValueError("分页结果存在重复页码")
    ordered = sorted(pages, key=lambda page: page.page_number)
    records = []
    metadata_errors = []
    page_sizes = set()
    total_pages_values = set()
    total_elements_values = set()
    for page in ordered:
        if getattr(page, "status_code", None) != 200:
            metadata_errors.append(f"page_{page.page_number}_http_status_invalid")
        if page.page_number < start_page:
            metadata_errors.append(f"page_{page.page_number}_before_requested_start")
        page_sizes.add(getattr(page, "page_size", None))
        total_pages_values.add(getattr(page, "total_pages", None))
        total_elements_values.add(getattr(page, "total_elements", None))
        if len(page.records) > page.page_size:
            metadata_errors.append(f"page_{page.page_number}_record_count_exceeds_page_size")
        records.extend((page.page_number, row) for row in page.records)
    if len(page_sizes) > 1:
        metadata_errors.append("page_size_changed_between_pages")
    if len(total_pages_values) > 1:
        metadata_errors.append("total_pages_changed_between_pages")
    if len(total_elements_values) > 1:
        metadata_errors.append("total_elements_changed_between_pages")
    reported_pages = next(iter(total_pages_values)) if len(total_pages_values) == 1 else expected_total_pages
    reported_elements = next(iter(total_elements_values)) if len(total_elements_values) == 1 else expected_total_elements
    if expected_total_pages is not None and reported_pages is not None and expected_total_pages != reported_pages:
        metadata_errors.append("expected_total_pages_mismatch")
    if expected_total_elements is not None and reported_elements is not None and expected_total_elements != reported_elements:
        metadata_errors.append("expected_total_elements_mismatch")
    codes = [str(row.get("companyCode") or "") for _, row in records]
    ids = [str(row.get("id")) for _, row in records if row.get("id") is not None]
    code_counts = Counter(codes)
    id_counts = Counter(ids)
    duplicate_codes = sorted(code for code, count in code_counts.items() if count > 1)
    duplicate_ids = sorted(record_id for record_id, count in id_counts.items() if count > 1)
    requested_pages = list(range(start_page, start_page + len(ordered)))
    missing_pages = sorted(set(requested_pages) - set(page_numbers))
    if ordered:
        missing_pages = sorted(set(range(start_page, ordered[-1].page_number + 1)) - set(page_numbers))
    record_limit_reached = max_records is not None and len(records) >= max_records
    all_pages_covered = (
        start_page == 0 and reported_pages is not None
        and len(ordered) == reported_pages
        and page_numbers == list(range(reported_pages))
    )
    complete = (
        all_pages_covered and not missing_pages and not metadata_errors
        and not duplicate_ids and not record_limit_reached
        and (reported_elements is None or len(records) == reported_elements)
    )
    return {
        "status": "complete" if complete else "partial",
        "complete_coverage": complete,
        "start_page": start_page,
        "fetched_page_count": len(ordered),
        "fetched_pages": page_numbers,
        "missing_pages_in_fetched_range": missing_pages,
        "page_size_values": sorted(x for x in page_sizes if isinstance(x, int)),
        "reported_total_pages": reported_pages,
        "reported_total_elements": reported_elements,
        "fetched_record_count": len(records),
        "unique_company_code_count": len(set(codes)),
        "duplicate_company_codes": duplicate_codes,
        "duplicate_record_ids": duplicate_ids,
        "metadata_errors": metadata_errors,
        "record_limit_reached": bool(record_limit_reached),
        "unfetched_page_count": max(0, (reported_pages or 0) - len(ordered)) if start_page == 0 else None,
    }
