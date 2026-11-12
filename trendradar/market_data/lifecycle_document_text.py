# 本文件将官方生命周期公告的HTML、XML、JSON和纯文本原文安全转换为可解析文本。
"""Extract readable text from fetched lifecycle evidence documents using the standard library."""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser

from trendradar.market_data.lifecycle_evidence_fetcher import FetchedDocument


_BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "br", "dd", "div", "dl", "dt",
    "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3",
    "h4", "h5", "h6", "header", "hr", "li", "main", "nav", "ol", "p",
    "section", "table", "tbody", "td", "th", "thead", "tr", "ul",
}
_IGNORED_TAGS = {"script", "style", "noscript", "svg"}


class _ReadableHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in _IGNORED_TAGS:
            self._ignored_depth += 1
        elif not self._ignored_depth and tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in _IGNORED_TAGS and self._ignored_depth:
            self._ignored_depth -= 1
        elif not self._ignored_depth and tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._ignored_depth and data:
            self.parts.append(data)


def _json_text(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        result = []
        for key, item in value.items():
            result.append(str(key))
            result.extend(_json_text(item))
        return result
    if isinstance(value, list):
        result = []
        for item in value:
            result.extend(_json_text(item))
        return result
    return []


def _normalize_text(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")
    value = re.sub(r"[\t\f\v ]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def extract_lifecycle_document_text(document: FetchedDocument) -> str:
    """Extract visible text; unsupported binary formats fail closed, never decode as noise."""
    if not isinstance(document, FetchedDocument):
        raise TypeError("document 必须是 FetchedDocument")
    if document.status_code != 200:
        raise ValueError("只能提取 HTTP 200 的官方文档")
    if not document.body:
        raise ValueError("官方文档内容为空")

    content_type = document.content_type.split(";", 1)[0].strip().lower()
    if content_type in {"text/plain", "application/xml", "text/xml"}:
        text = document.body.decode("utf-8-sig", errors="replace")
    elif content_type == "text/html":
        parser = _ReadableHTMLParser()
        parser.feed(document.body.decode("utf-8-sig", errors="replace"))
        parser.close()
        text = "".join(parser.parts)
    elif content_type == "application/json":
        try:
            payload = json.loads(document.body.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("官方 JSON 文档无法按 UTF-8/JSON 解析") from exc
        text = "\n".join(_json_text(payload))
    else:
        raise ValueError(f"暂不支持从该内容类型提取文本: {content_type or 'unknown'}")

    normalized = _normalize_text(text)
    if not normalized:
        raise ValueError("官方文档未提取到可读文本")
    return normalized
