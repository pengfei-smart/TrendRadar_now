# 本文件负责从登记的官方域名安全获取生命周期公告原文，并按来源设置必要请求头及计算内容哈希。
"""Bounded, allowlisted HTTP retrieval for lifecycle evidence documents."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from urllib.parse import urlsplit
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from trendradar.market_data.lifecycle_source_registry import get_source, is_allowed_url


@dataclass(frozen=True)
class FetchedDocument:
    source_id: str
    requested_url: str
    final_url: str
    status_code: int
    content_type: str
    body: bytes
    content_sha256: str


class _SameSourceRedirectHandler(HTTPRedirectHandler):
    def __init__(self, source_id: str):
        super().__init__()
        self.source_id = source_id

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not is_allowed_url(self.source_id, newurl):
            raise ValueError("官方资料重定向目标不在来源域名白名单内")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class LifecycleEvidenceFetcher:
    """Fetch only HTTPS documents hosted by a registered official source."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 20.0,
        max_bytes: int = 12 * 1024 * 1024,
        user_agent: str = "TrendRadar-LifecycleEvidence/1.0",
        opener=None,
    ):
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds 必须大于0")
        if max_bytes <= 0:
            raise ValueError("max_bytes 必须大于0")
        self.timeout_seconds = timeout_seconds
        self.max_bytes = max_bytes
        self.user_agent = user_agent
        self._opener = opener

    def _request_headers(self, source_id: str, url: str = "") -> dict[str, str]:
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/pdf,application/xml,text/xml,application/json,*/*;q=0.5",
        }
        if source_id == "neeq":
            # NEEQ 附件实测要求浏览器风格请求头；列表接口与附件接口均位于官方站点。
            headers.update({
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
                "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.8",
                "Referer": "https://www.neeq.com.cn/disclosure/announcement.html",
                "Origin": "https://www.neeq.com.cn",
            })
            if url and urlsplit(url).path.lower().startswith(
                ("/uploads/", "/disclosure/", "/important_news/")
            ):
                # 终止挂牌结果接口的历史附件分布在 uploads、disclosure 和 important_news；
                # 统一使用同源浏览器导航头，HTML 页面按类型协商 Accept。
                path = urlsplit(url).path.lower()
                accept = (
                    "text/html,application/xhtml+xml,application/pdf,*/*"
                    if path.endswith((".html", ".htm")) or "/important_news/" in path
                    else "application/pdf,*/*"
                )
                headers.update({
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/130.0.0.0 Safari/537.36"
                    ),
                    "Accept": accept,
                    "Referer": "https://www.neeq.com.cn/disclosure/termination_listing.html",
                    "Sec-Fetch-Dest": "document",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Site": "same-origin",
                })
        return headers

    def fetch(self, source_id: str, url: str) -> FetchedDocument:
        source = get_source(source_id)
        if not is_allowed_url(source_id, url):
            raise ValueError(f"URL 不属于 {source.name} 的 HTTPS 白名单")
        request = Request(url, headers=self._request_headers(source_id, url))
        opener = self._opener or build_opener(_SameSourceRedirectHandler(source_id))
        try:
            response = opener.open(request, timeout=self.timeout_seconds)
            with response:
                final_url = response.geturl()
                if not is_allowed_url(source_id, final_url):
                    raise ValueError("最终响应 URL 不在官方来源白名单内")
                status_code = response.status if hasattr(response, "status") else response.getcode()
                if status_code != 200:
                    raise RuntimeError(f"官方资料请求返回 HTTP {status_code}")
                content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                allowed_types = {
                    "text/html", "application/pdf", "application/xml", "text/xml",
                    "application/json", "text/plain", "application/octet-stream",
                    "application/javascript", "text/javascript", "application/x-javascript",
                }
                if content_type not in allowed_types:
                    raise ValueError(f"不支持的公告内容类型: {content_type or 'unknown'}")
                declared_size = response.headers.get("Content-Length")
                if declared_size and int(declared_size) > self.max_bytes:
                    raise ValueError("官方资料超过单文件大小限制")
                body = response.read(self.max_bytes + 1)
                if len(body) > self.max_bytes:
                    raise ValueError("官方资料超过单文件大小限制")
        except (HTTPError, URLError) as exc:
            raise RuntimeError(f"官方资料获取失败: {exc}") from exc

        return FetchedDocument(
            source_id=source_id,
            requested_url=url,
            final_url=final_url,
            status_code=status_code,
            content_type=content_type,
            body=body,
            content_sha256=hashlib.sha256(body).hexdigest(),
        )
