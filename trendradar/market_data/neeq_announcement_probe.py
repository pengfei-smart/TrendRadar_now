# 本文件对全国股转系统公告列表接口执行单次、限量、只读探测，不下载公告附件或确认生命周期事实。
"""Run one bounded read-only probe against the NEEQ announcement list API."""

from __future__ import annotations

import hashlib
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, build_opener

from trendradar.market_data.lifecycle_evidence_fetcher import _SameSourceRedirectHandler
from trendradar.market_data.lifecycle_source_registry import is_allowed_url
from trendradar.market_data.neeq_lifecycle_collector import (
    NEEQ_LIST_ENDPOINT,
    build_neeq_announcement_search_plan,
    is_lifecycle_announcement_title,
    normalize_neeq_announcement_response,
)
from trendradar.market_data.neeq_runtime_probe import DEFAULT_CALLBACK, parse_neeq_jsonp


MAX_RESPONSE_BYTES = 2 * 1024 * 1024
DEFAULT_NEED_FIELDS = (
    "companyCd", "companyName", "disclosureTitle", "disclosurePostTitle",
    "destFilePath", "publishDate", "xxfcbj", "fileExt", "xxzrlx",
)


def probe_neeq_announcement_list(
    *,
    start_date: str,
    end_date: str,
    keyword: str = "",
    market_layer: str = "3",
    page: int = 0,
    company_code: str = "",
    notice_type: str = "",
    disclosure_type: str = "",
    disclosure_subtype: str = "",
    industry_type: str = "",
    need_fields: tuple[str, ...] = DEFAULT_NEED_FIELDS,
    timeout_seconds: float = 15.0,
    max_bytes: int = MAX_RESPONSE_BYTES,
    opener=None,
    callback: str = "TrendRadarAnnouncementProbe",
    omit_date_filter: bool = False,
) -> dict:
    """Send exactly one allowlisted POST; return metadata only, never fetch PDF attachments."""
    if not re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", callback):
        raise ValueError("callback 名称格式无效")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds 必须大于 0")
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or not 1 <= max_bytes <= MAX_RESPONSE_BYTES:
        raise ValueError("max_bytes 必须在 1 到 2 MiB 之间")

    plan = build_neeq_announcement_search_plan(
        start_date=start_date,
        end_date=end_date,
        keyword=keyword,
        market_layer=market_layer,
        page=page,
        company_code=company_code,
        notice_type=notice_type,
        disclosure_type=disclosure_type,
        disclosure_subtype=disclosure_subtype,
        industry_type=industry_type,
        need_fields=need_fields,
    )
    endpoint = plan["endpoint"]
    if not plan["endpoint_allowed"] or not is_allowed_url("neeq", endpoint):
        raise ValueError("公告列表接口不在 NEEQ 官方 HTTPS 白名单内")

    params = dict(plan["params"])
    if omit_date_filter:
        params.pop("startTime", None)
        params.pop("endTime", None)
    params["sortfield"] = "xxssdq"
    params["sorttype"] = "asc"
    url = endpoint + "?" + urlencode({"callback": callback})
    if not is_allowed_url("neeq", url):
        raise ValueError("公告列表请求 URL 不在 NEEQ 官方 HTTPS 白名单内")

    body = urlencode(params, doseq=True).encode("utf-8")
    request = Request(
        url,
        data=body,
        method="POST",
        headers={
            "User-Agent": "TrendRadar-LifecycleEvidence/1.0",
            "Accept": "text/javascript, application/javascript, application/ecmascript, */*; q=0.01",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": "https://www.neeq.com.cn",
            "Referer": "https://www.neeq.com.cn/disclosure/announcement.html",
            "X-Requested-With": "XMLHttpRequest",
        },
    )
    client = opener or build_opener(_SameSourceRedirectHandler("neeq"))
    try:
        response = client.open(request, timeout=timeout_seconds)
        with response:
            final_url = response.geturl()
            if not is_allowed_url("neeq", final_url):
                raise ValueError("公告列表最终响应 URL 不在 NEEQ 官方来源白名单内")
            status = response.status if hasattr(response, "status") else response.getcode()
            if status != 200:
                raise RuntimeError(f"公告列表接口返回 HTTP {status}")
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            allowed_types = {
                "application/javascript", "text/javascript", "application/x-javascript",
                "application/json", "text/plain", "text/html",
            }
            if content_type not in allowed_types:
                raise ValueError(f"公告列表接口返回不支持的 MIME: {content_type or 'unknown'}")
            declared_size = response.headers.get("Content-Length")
            if declared_size and int(declared_size) > max_bytes:
                raise ValueError("公告列表响应超过大小限制")
            raw = response.read(max_bytes + 1)
            if len(raw) > max_bytes:
                raise ValueError("公告列表响应超过大小限制")
    except (HTTPError, URLError) as exc:
        raise RuntimeError(f"NEEQ 公告列表探测失败: {exc}") from exc

    payload = parse_neeq_jsonp(raw, callback)
    normalized = normalize_neeq_announcement_response(payload)
    records = normalized["records"]
    requested_company_code = str(params.get("companyCd") or "").strip()
    company_code_filter_verified = bool(
        requested_company_code
        and records
        and all(
            isinstance(record, dict)
            and str(record.get("companyCd") or "").strip() == requested_company_code
            for record in records
        )
    )
    title_field = "disclosureTitle"
    lifecycle_candidates = [
        {
            "company_code": str(record.get("companyCd") or ""),
            "company_name": str(record.get("companyName") or ""),
            "title": str(record.get(title_field) or ""),
            "publish_date": str(record.get("publishDate") or ""),
            "file_path": str(record.get("destFilePath") or ""),
            "file_extension": str(record.get("fileExt") or ""),
            "record": dict(record),
        }
        for record in records
        if isinstance(record, dict) and is_lifecycle_announcement_title(str(record.get(title_field) or ""))
    ]
    return {
        "source_id": "neeq",
        "endpoint": endpoint,
        "http_method": "POST",
        "status_code": status,
        "content_type": content_type,
        "content_type_warning": (
            "服务端响应头为 text/html，但正文通过严格 JSONP 和 listInfo 结构校验"
            if content_type == "text/html" else None
        ),
        "final_url": final_url,
        "metadata_source_url": final_url,
        "request_count": 1,
        "request_page": page,
        "request_params": params,
        "date_filter_omitted": omit_date_filter,
        "response_sha256": hashlib.sha256(raw).hexdigest(),
        "response_bytes": len(raw),
        "response_preview": raw.decode("utf-8-sig", errors="replace")[:1000],
        "page_number": normalized.get("page_number"),
        "page_size": normalized.get("page_size"),
        "returned_count": len(records),
        "total_elements": normalized["total_elements"],
        "lifecycle_candidate_count": len(lifecycle_candidates),
        "records": [dict(record) for record in records if isinstance(record, dict)],
        "lifecycle_candidates": lifecycle_candidates,
        "runtime_verified": True,
        "classification_filter_verified": False,
        "company_code_filter_verified": company_code_filter_verified,
        "lifecycle_only_query": False,
        "warnings": [
            "本探测只验证公告列表请求、响应包装及分页元数据，不证明高级筛选分类参数已生效。",
            "当前公告列表可能是混合公告；标题命中仅为人工审核候选，不是退市事实。",
            "只返回公告元数据，不下载 PDF，不写入正式证券生命周期表。",
            "noticeType、disclosureType、disclosureSubtype 的业务字典含义尚未全部核验；不要据此推断分类语义。",
            (
                "companyCd 参数已通过本次非空返回记录逐条匹配验证；keyword 参数仍未完成阳性对照。"
                if company_code_filter_verified
                else (
                    "本次未逐条验证 companyCd 返回记录；keyword 参数也未完成阳性对照。"
                    if requested_company_code
                    else "本次未指定 companyCd；keyword 参数尚未完成阳性对照。"
                )
            ),
            "即使筛选结果为零，也不得单凭零结果认定证券无公告或已经终止挂牌。",
        ],
    }
