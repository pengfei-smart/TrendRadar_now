# 本脚本对比NEEQ公告接口直接POST与先访问官方公告页建立Cookie会话后的POST行为，仅输出元数据，不写数据库。
"""Compare direct and page-initialized NEEQ announcement API requests."""

from __future__ import annotations

import json
import sys
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.request import HTTPCookieProcessor, Request, build_opener

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trendradar.market_data.lifecycle_evidence_fetcher import _SameSourceRedirectHandler
from trendradar.market_data.lifecycle_source_registry import is_allowed_url
from trendradar.market_data.neeq_announcement_probe import probe_neeq_announcement_list


PAGE_URL = "https://www.neeq.com.cn/disclosure/announcement.html"
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def _new_opener():
    return build_opener(
        HTTPCookieProcessor(CookieJar()),
        _SameSourceRedirectHandler("neeq"),
    )


def _initialize_page_session(opener) -> dict:
    if not is_allowed_url("neeq", PAGE_URL):
        raise ValueError("NEEQ公告页面不在官方来源白名单内")
    request = Request(PAGE_URL, headers=BROWSER_HEADERS, method="GET")
    response = opener.open(request, timeout=20.0)
    with response:
        final_url = response.geturl()
        if not is_allowed_url("neeq", final_url):
            raise ValueError("公告页面最终地址不在官方来源白名单内")
        status = response.status if hasattr(response, "status") else response.getcode()
        if status != 200:
            raise RuntimeError(f"公告页面返回HTTP {status}")
        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
        if content_type not in {"text/html", "application/xhtml+xml"}:
            raise ValueError(f"公告页面MIME异常: {content_type}")
        body = response.read(1024 * 1024 + 1)
        if len(body) > 1024 * 1024:
            raise ValueError("公告页面超过1MiB限制")
    return {
        "page_status": status,
        "page_content_type": content_type,
        "page_bytes": len(body),
        "page_final_url": final_url,
    }


def _probe(opener, label: str) -> dict:
    result = probe_neeq_announcement_list(
        start_date="2026-11-04",
        end_date="2026-11-13",
        page=0,
        omit_date_filter=True,
        opener=opener,
    )
    return {
        "test": label,
        "status_code": result["status_code"],
        "content_type": result["content_type"],
        "returned_count": result["returned_count"],
        "total_elements": result["total_elements"],
        "page_size": result["page_size"],
        "response_sha256": result["response_sha256"],
        "response_preview": result["response_preview"][:500],
    }


def main() -> int:
    direct = _probe(_new_opener(), "direct_post")
    session_opener = _new_opener()
    session = _initialize_page_session(session_opener)
    session_result = _probe(session_opener, "get_page_then_post")
    print(json.dumps({
        "direct": direct,
        "session_initialization": session,
        "session_post": session_result,
        "interpretation": (
            "仅比较两种请求路径的响应差异；HTTP 200和空列表均不构成公告不存在或退市的证据。"
        ),
        "database_writes": 0,
        "attachment_downloads": 0,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
