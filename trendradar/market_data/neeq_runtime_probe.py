# 本文件对全国股转系统已确认使用 POST 的公告分类树接口执行单次、限量的运行时探测。
"""Run a bounded runtime probe against the confirmed NEEQ category-tree endpoint."""

from __future__ import annotations

import hashlib
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, build_opener

from trendradar.market_data.lifecycle_evidence_fetcher import _SameSourceRedirectHandler
from trendradar.market_data.lifecycle_source_registry import is_allowed_url


NEEQ_CATEGORY_ENDPOINT = "https://www.neeq.com.cn/screening/advanced.do"
DEFAULT_CALLBACK = "TrendRadarCategoryProbe"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_ALLOWED_CONTENT_TYPES = {
    "application/javascript",
    "text/javascript",
    "application/x-javascript",
    "application/json",
    "text/plain",
    # NEEQ 实际响应头可能错误标记为 text/html；正文仍必须通过严格 JSONP 校验。
    "text/html",
}


def parse_neeq_jsonp(body: bytes, callback: str = DEFAULT_CALLBACK):
    """Parse JSON or one exact JSONP callback; reject arbitrary wrappers."""
    if not isinstance(body, bytes):
        raise TypeError("body 必须为 bytes")
    if not re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", callback):
        raise ValueError("callback 名称格式无效")
    text = body.decode("utf-8-sig").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    pattern = rf"^{re.escape(callback)}\s*\((.*)\)\s*;?$"
    match = re.fullmatch(pattern, text, flags=re.DOTALL)
    if not match:
        raise ValueError("响应不是预期 JSON 或指定 callback 的 JSONP")
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise ValueError("JSONP callback 内部不是有效 JSON") from exc


def probe_neeq_category_tree(
    *,
    timeout_seconds: float = 15.0,
    max_bytes: int = MAX_RESPONSE_BYTES,
    opener=None,
    callback: str = DEFAULT_CALLBACK,
) -> dict:
    """Issue exactly one allowlisted POST request to the statically confirmed tree API."""
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds 必须大于 0")
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or not 1 <= max_bytes <= MAX_RESPONSE_BYTES:
        raise ValueError("max_bytes 必须在 1 到 2 MiB 之间")
    if not re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", callback):
        raise ValueError("callback 名称格式无效")
    url = NEEQ_CATEGORY_ENDPOINT + "?" + urlencode({"callback": callback})
    if not is_allowed_url("neeq", url):
        raise ValueError("分类接口 URL 不在 NEEQ 官方来源白名单内")
    request = Request(
        url,
        data=b"",
        method="POST",
        headers={
            "User-Agent": "TrendRadar-LifecycleEvidence/1.0",
            "Accept": "application/javascript,application/json,text/plain,*/*;q=0.5",
            "Referer": "https://www.neeq.com.cn/disclosure/announcement.html",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        },
    )
    client = opener or build_opener(_SameSourceRedirectHandler("neeq"))
    try:
        response = client.open(request, timeout=timeout_seconds)
        with response:
            final_url = response.geturl()
            if not is_allowed_url("neeq", final_url):
                raise ValueError("分类接口最终响应 URL 不在 NEEQ 官方来源白名单内")
            status = response.status if hasattr(response, "status") else response.getcode()
            if status != 200:
                raise RuntimeError(f"分类接口返回 HTTP {status}")
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type not in _ALLOWED_CONTENT_TYPES:
                raise ValueError(f"分类接口返回不支持的 MIME: {content_type or 'unknown'}")
            declared_size = response.headers.get("Content-Length")
            if declared_size and int(declared_size) > max_bytes:
                raise ValueError("分类接口响应超过大小限制")
            raw = response.read(max_bytes + 1)
            if len(raw) > max_bytes:
                raise ValueError("分类接口响应超过大小限制")
    except (HTTPError, URLError) as exc:
        raise RuntimeError(f"NEEQ 分类接口探测失败: {exc}") from exc

    payload = parse_neeq_jsonp(raw, callback)
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict) or not isinstance(payload[0].get("list"), list):
        raise ValueError("分类接口响应未满足已观察到的 [0].list 结构")
    return {
        "source_id": "neeq",
        "endpoint": NEEQ_CATEGORY_ENDPOINT,
        "http_method": "POST",
        "request_encoding": "JSONP",
        "status_code": status,
        "content_type": content_type,
        "content_type_warning": (
            "服务端响应头为 text/html，但正文通过严格 JSONP 与分类结构校验"
            if content_type == "text/html" else None
        ),
        "final_url": final_url,
        "content_length": len(raw),
        "content_sha256": hashlib.sha256(raw).hexdigest(),
        "payload": payload,
        "runtime_verified": True,
        "request_count": 1,
    }
