# 本文件只读采集全国股转公司官方终止挂牌结果列表，校验 JSONP 和分页，不写入数据库。
"""Read-only provider for the official NEEQ termination-result listing."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlparse
from urllib.request import Request, build_opener

from trendradar.market_data.lifecycle_source_registry import is_allowed_url

ENDPOINT = "https://www.neeq.com.cn/terminationController/infoResultse.do"
REFERER = "https://www.neeq.com.cn/disclosure/termination_listing.html"
CALLBACK = "TrendRadarTerminationProbe"
MAX_RESPONSE_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class NEEQTerminationPage:
    source_url: str
    response_sha256: str
    retrieved_at: str
    page_number: int
    page_size: int
    total_elements: int
    total_pages: int
    records: tuple[dict, ...]
    request_parameters: dict
    request_body_sha256: str
    status_code: int = 200
    rejected_record_count: int = 0
    record_validation_errors: tuple[str, ...] = ()
    rejected_record_details: tuple[dict, ...] = ()


def _format_epoch_date(value) -> str:
    if isinstance(value, dict):
        value = value.get("time")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return ""
    try:
        return datetime.fromtimestamp(
            value / 1000, tz=timezone(timedelta(hours=8))
        ).date().isoformat()
    except (OverflowError, OSError, ValueError):
        return ""


def _parse_jsonp(body: str, callback: str) -> object:
    if not re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$.]*", callback):
        raise ValueError("JSONP callback 名称无效")
    match = re.fullmatch(
        rf"\s*{re.escape(callback)}\((.*)\)\s*;?\s*", body, re.DOTALL
    )
    if not match:
        raise ValueError("NEEQ 响应不是预期 callback(...) JSONP")
    return json.loads(match.group(1))


def _normalize_attachment_url(value: str) -> str:
    """Normalize only root-relative NEEQ paths; absolute URLs must already be allowlisted."""
    from urllib.parse import urljoin, urlsplit

    raw = str(value or "").strip()
    if not raw:
        return ""
    if any(ord(char) < 32 for char in raw) or "\\" in raw:
        raise ValueError("终止挂牌公告附件 URL 含非法字符")
    if raw.startswith("/") and not raw.startswith("//"):
        from urllib.parse import unquote
        raw_path = unquote(raw.split("?", 1)[0].split("#", 1)[0])
        if any(part in {".", ".."} for part in raw_path.split("/")):
            raise ValueError("终止挂牌公告附件 URL 不允许路径回退")
        normalized = urljoin("https://www.neeq.com.cn/", raw)
    else:
        normalized = raw
    parsed = urlsplit(normalized)
    if parsed.scheme != "https" or parsed.username is not None or parsed.password is not None:
        raise ValueError("终止挂牌公告附件必须使用官方 HTTPS 地址")
    if not is_allowed_url("neeq", normalized):
        raise ValueError("终止挂牌公告附件 URL 不在 NEEQ 官方 HTTPS 白名单")
    return normalized


def _attachment_kind(url: str) -> str:
    from urllib.parse import urlsplit

    path = urlsplit(url).path.lower()
    if path.endswith(".pdf"):
        return "pdf"
    if path.endswith((".html", ".htm")):
        return "html"
    return "unknown"


def _validate_record(record: object) -> dict:
    if not isinstance(record, dict):
        raise ValueError("终止挂牌记录必须是对象")
    code = str(record.get("companyCode") or "").strip()
    name = str(record.get("companyName") or "").strip()
    broker = str(record.get("hostBroker") or "").strip()
    original_notice = str(record.get("delistingNotice") or "").strip()
    if len(code) != 6 or not code.isdigit() or not name:
        raise ValueError("终止挂牌记录缺少有效证券代码或名称")
    notice = _normalize_attachment_url(original_notice)
    result = dict(record)
    result["companyCode"] = code
    result["companyName"] = name
    result["hostBroker"] = broker
    result["delistingNoticeOriginal"] = original_notice
    result["delistingNotice"] = notice
    result["attachmentKind"] = _attachment_kind(notice)
    result["delistingDateISO"] = _format_epoch_date(record.get("delistingDate"))
    return result


class NEEQTerminationResultProvider:
    """Fetch bounded, paginated official termination-result records."""

    def __init__(self, *, timeout_seconds: float = 25, max_response_bytes: int = MAX_RESPONSE_BYTES,
                 opener=None, user_agent: str | None = None):
        if timeout_seconds <= 0 or max_response_bytes <= 0:
            raise ValueError("timeout 和响应字节上限必须大于零")
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = min(max_response_bytes, MAX_RESPONSE_BYTES)
        self.opener = opener or build_opener()
        self.user_agent = user_agent or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/130.0.0.0 Safari/537.36"
        )

    def fetch_page(self, *, page: int = 0, company_code: str = "",
                   keyword: str = "", market_layers: tuple[str, ...] = ("0", "1", "2"),
                   retrieved_at: str | None = None,
                   tolerate_invalid_records: bool = False) -> NEEQTerminationPage:
        if isinstance(page, bool) or not isinstance(page, int) or page < 0 or page > 100000:
            raise ValueError("page 必须在 0 到 100000 之间")
        if company_code and (len(company_code) != 6 or not company_code.isdigit()):
            raise ValueError("company_code 必须是六位数字")
        if len(market_layers) > 3 or any(x not in {"0", "1", "2"} for x in market_layers):
            raise ValueError("market_layers 只允许 0、1、2，最多三项")
        params = [
            ("page", str(page)), ("sortfield", "bean.delisting_date"),
            ("sorttype", "desc"), ("companyCode", company_code),
        ]
        params.extend(("newThreeArray[]", layer) for layer in market_layers)
        params.append(("keyword", keyword))
        source_url = ENDPOINT + "?" + urlencode({"callback": CALLBACK})
        if not is_allowed_url("neeq", source_url):
            raise ValueError("NEEQ 查询接口不在官方 HTTPS 白名单")
        request_body = urlencode(params).encode("utf-8")
        request = Request(
            source_url, data=request_body, method="POST",
            headers={
                "User-Agent": self.user_agent,
                "Accept": "application/javascript, text/javascript, */*;q=0.01",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Origin": "https://www.neeq.com.cn",
                "Referer": REFERER,
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        with self.opener.open(request, timeout=self.timeout_seconds) as response:
            status = response.status if hasattr(response, "status") else response.getcode()
            final_url = response.geturl()
            if status != 200 or not is_allowed_url("neeq", final_url):
                raise RuntimeError("NEEQ 终止挂牌接口返回非成功状态或非官方 URL")
            body_bytes = response.read(self.max_response_bytes + 1)
        if len(body_bytes) > self.max_response_bytes:
            raise ValueError("NEEQ 终止挂牌接口响应超过字节上限")
        body = body_bytes.decode("utf-8-sig", errors="strict")
        payload = _parse_jsonp(body, CALLBACK)
        if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
            raise ValueError("NEEQ JSONP 外层结构不符合预期")
        page_data = payload[0].get("page")
        if not isinstance(page_data, dict) or not isinstance(page_data.get("content"), list):
            raise ValueError("NEEQ JSONP 缺少 page.content")
        valid_records = []
        validation_errors = []
        rejected_details = []
        for index, raw_record in enumerate(page_data["content"]):
            try:
                valid_records.append(_validate_record(raw_record))
            except (TypeError, ValueError) as exc:
                if not tolerate_invalid_records:
                    raise
                validation_errors.append(f"record_{index}:{exc}")
                raw = raw_record if isinstance(raw_record, dict) else {}
                notice_url = str(raw.get("delistingNotice") or "").strip()
                parsed_url = urlparse(notice_url)
                rejected_details.append({
                    "record_index": index,
                    "company_code": str(raw.get("companyCode") or "")[:16],
                    "company_name": str(raw.get("companyName") or "")[:100],
                    "host_broker": str(raw.get("hostBroker") or "")[:100],
                    "attachment_url": notice_url[:500],
                    "attachment_scheme": parsed_url.scheme,
                    "attachment_host": parsed_url.hostname or "",
                    "validation_error": str(exc)[:200],
                })
        records = tuple(valid_records)
        number = page_data.get("number")
        size = page_data.get("size")
        total = page_data.get("totalElements")
        pages = page_data.get("totalPages")
        if any(isinstance(x, bool) or not isinstance(x, int) or x < 0 for x in (number, size, total, pages)):
            raise ValueError("NEEQ 分页字段无效")
        if number != page or len(records) > size:
            raise ValueError("NEEQ 返回页码或记录数与请求不一致")
        return NEEQTerminationPage(
            source_url=source_url,
            response_sha256=hashlib.sha256(body_bytes).hexdigest(),
            retrieved_at=retrieved_at or datetime.now(timezone.utc).isoformat(),
            page_number=number, page_size=size, total_elements=total,
            total_pages=pages, records=records,
            request_parameters={
                "page": page, "sortfield": "bean.delisting_date",
                "sorttype": "desc", "companyCode": company_code,
                "newThreeArray": list(market_layers), "keyword": keyword,
            },
            request_body_sha256=hashlib.sha256(request_body).hexdigest(),
            status_code=200, rejected_record_count=len(validation_errors),
            record_validation_errors=tuple(validation_errors),
            rejected_record_details=tuple(rejected_details),
        )

    def fetch_all(self, *, company_code: str = "", keyword: str = "",
                  max_pages: int = 20, max_records: int = 1000) -> list[NEEQTerminationPage]:
        if not 1 <= max_pages <= 100 or not 1 <= max_records <= 10000:
            raise ValueError("分页或记录上限超出允许范围")
        first = self.fetch_page(company_code=company_code, keyword=keyword)
        pages = [first]
        collected = len(first.records)
        page_count = min(first.total_pages, max_pages)
        for page_number in range(1, page_count):
            if collected >= max_records:
                break
            current = self.fetch_page(
                page=page_number, company_code=company_code, keyword=keyword
            )
            pages.append(current)
            collected += len(current.records)
        return pages
