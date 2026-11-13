# 本文件检查官方公告入口的HTML表单、iframe、脚本和站内链接，辅助确认真实检索请求契约。
"""Inspect allowlisted official announcement pages without submitting search forms."""

from __future__ import annotations

import argparse
import json
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

from trendradar.market_data.lifecycle_evidence_fetcher import LifecycleEvidenceFetcher
from trendradar.market_data.lifecycle_source_registry import is_allowed_url


SOURCE_PAGES = {
    "sse": (
        "https://www.sse.com.cn/home/search/",
    ),
    "szse": (
        "https://www.szse.cn/disclosure/notice/company/index.html",
        "https://www.szse.cn/disclosure/notice/",
    ),
    "csrc": (
        "https://www.csrc.gov.cn/csrc/c101971/common_list.shtml?channelid=17d5ff2fe43e488dba825807ae40d63f",
        "https://www.csrc.gov.cn/csrc/c101954/common_list.shtml",
    ),
    "neeq": (
        "https://www.neeq.com.cn/disclosure/announcement.html",
        "https://www.neeq.com.cn/disclosure/snadc_announcement.html?PC=PC",
    ),
}


class _PageInspector(HTMLParser):
    def __init__(self, base_url: str, source_id: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.source_id = source_id
        self.title_parts = []
        self.in_title = False
        self.forms = []
        self.iframes = []
        self.scripts = []
        self.links = []
        self._form = None
        self._link = None

    def _resolved(self, value: str) -> str:
        return urljoin(self.base_url, value.strip()) if value else ""

    def _url_record(self, value: str) -> dict:
        resolved = self._resolved(value)
        parsed = urlparse(resolved)
        return {
            "url": resolved,
            "allowed_by_source_registry": is_allowed_url(self.source_id, resolved),
            "host": parsed.hostname or "",
        }

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        tag = tag.lower()
        if tag == "title":
            self.in_title = True
        elif tag == "form":
            self._form = {
                "action": self._url_record(attrs.get("action", self.base_url)),
                "method": (attrs.get("method") or "get").lower(),
                "fields": [],
            }
        elif tag in {"input", "select", "textarea", "button"} and self._form is not None:
            self._form["fields"].append({
                "tag": tag,
                "name": attrs.get("name", ""),
                "type": attrs.get("type", "text" if tag == "input" else tag),
                "id": attrs.get("id", ""),
                "placeholder": attrs.get("placeholder", ""),
            })
        elif tag == "iframe" and attrs.get("src"):
            self.iframes.append(self._url_record(attrs["src"]))
        elif tag == "script" and attrs.get("src"):
            self.scripts.append(self._url_record(attrs["src"]))
        elif tag == "a" and attrs.get("href"):
            self._link = {
                **self._url_record(attrs["href"]),
                "text": "",
            }

    def handle_data(self, data):
        if self.in_title:
            self.title_parts.append(data.strip())
        if self._link is not None:
            self._link["text"] += data.strip()

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "title":
            self.in_title = False
        elif tag == "form" and self._form is not None:
            self.forms.append(self._form)
            self._form = None
        elif tag == "a" and self._link is not None:
            if self._link["text"] or self._link["url"]:
                self.links.append(self._link)
            self._link = None

    def result(self) -> dict:
        return {
            "page_url": self.base_url,
            "title": " ".join(part for part in self.title_parts if part),
            "forms": self.forms,
            "iframes": self.iframes,
            "scripts": self.scripts,
            "links": self.links,
        }


def inspect_announcement_page(source_id: str, url: str, fetcher=None) -> dict:
    """Fetch one registered page and report its visible search contract."""
    if not is_allowed_url(source_id, url):
        raise ValueError("公告入口 URL 不在该官方来源白名单内")
    fetcher = fetcher or LifecycleEvidenceFetcher()
    document = fetcher.fetch(source_id, url)
    if document.content_type != "text/html":
        raise ValueError(f"入口不是 HTML 页面: {document.content_type}")
    parser = _PageInspector(document.final_url, source_id)
    parser.feed(document.body.decode("utf-8-sig", errors="replace"))
    parser.close()
    result = parser.result()
    result.update({
        "source_id": source_id,
        "status_code": document.status_code,
        "content_sha256": document.content_sha256,
    })
    return result



def _extract_neeq_endpoint_contracts(source: str) -> list[dict]:
    """从已抓取脚本中归纳新三板公告接口契约；仅记录静态证据，不发起请求。"""
    definitions = (
        {
            "endpoint": "/disclosureInfoController/companyAnnouncement.do",
            "role": "announcement_list",
            "request_method": "not_explicit_in_inspected_context",
            "request_encoding": "JSONP",
            "request_params": [
                "noticeType", "disclosureType", "disclosureSubtype", "page",
                "companyCd", "isNewThree", "startTime", "endTime", "keyword",
                "xxfcbj", "hyType", "needFields", "siteId",
            ],
            "response_paths": ["[0].listInfo.content", "[0].listInfo.totalElements"],
            "notes": [
                "脚本明确配置 dataType=jsonp；当前截取上下文未能可靠确认该接口的 HTTP method。",
                "分页判断和列表读取均使用响应数组首项的 listInfo。",
            ],
        },
        {
            "endpoint": "/disclosureInfoController/initDisclosureList.do",
            "role": "announcement_list_initialization",
            "request_method": "POST",
            "request_encoding": "JSONP",
            "request_params": [
                "disclosureType", "disclosureSubtype", "page", "companyCd",
                "isNewThree", "startTime", "endTime", "keyword", "hyType",
                "xxfcbj", "needFields",
            ],
            "response_paths": ["data.content[0].disclosures", "data.content[0].totalElements"],
            "notes": [
                "脚本同时在分页处读取 data.totalElements，与 content[0].totalElements 不一致；需用真实响应样本核验。",
            ],
        },
        {
            "endpoint": "/disclosureInfoController/infoResult_en.do",
            "role": "basic_announcement_list",
            "request_method": "POST",
            "request_encoding": "JSONP",
            "request_params": [
                "flag", "disclosureType", "page", "companyCd", "isNewThree",
                "startTime", "endTime", "keyword", "needFields",
            ],
            "response_paths": ["[0].listInfo.content", "[0].listInfo.totalElements"],
            "notes": ["基础公告列表脚本明确使用响应数组首项的 listInfo。"],
        },
        {
            "endpoint": "/screening/advanced.do",
            "role": "announcement_category_tree",
            "request_method": "POST",
            "request_encoding": "JSONP",
            "request_params": [],
            "response_paths": ["[0].list"],
            "record_fields": ["title", "datebaseNumber", "businessNumber", "sonList"],
            "child_fields": ["datebaseNumber", "businessNumber", "title"],
            "notes": [
                "分类父子节点及 datebaseNumber 均由接口动态返回；不得将分类 ID 固定写入代码。",
                "页面将父节点 key 编码为 0:<id>，子节点 key 编码为 1:<id>。",
            ],
        },
        {
            "endpoint": "/disclosureInfoController/disclosure_hy.do",
            "role": "industry_tree",
            "request_method": "not_explicit_in_inspected_context",
            "request_encoding": "JSONP",
            "request_params": ["type", "neeqhyfl"],
            "observed_request_values": {"type": "1", "neeqhyfl": "1"},
            "response_paths": ["response passed to industry callback v(e)"],
            "notes": ["行业响应的具体字段结构尚未由当前脚本上下文确认。"],
        },
        {
            "endpoint": "/disclosureInfoController/disclosure_type.do",
            "role": "announcement_type_dictionary",
            "request_method": "not_explicit_in_inspected_context",
            "request_encoding": "JSONP",
            "request_params": ["type"],
            "observed_request_values": {"type": "1"},
            "response_paths": ["[0].values"],
            "notes": ["脚本读取 values 并按 dvalue 处理公告类型；完整业务含义仍需官方响应样本确认。"],
        },
    )
    contracts = []
    for definition in definitions:
        if definition["endpoint"] not in source:
            continue
        contracts.append({
            **definition,
            "evidence_level": "static_javascript_inspection",
            "runtime_verified": False,
        })
    return contracts


def inspect_script_asset(source_id: str, url: str, fetcher=None) -> dict:
    """Fetch one allowlisted JavaScript asset and extract endpoint-like literals."""
    if not is_allowed_url(source_id, url):
        raise ValueError("脚本 URL 不在该官方来源白名单内")
    fetcher = fetcher or LifecycleEvidenceFetcher()
    document = fetcher.fetch(source_id, url)
    if document.content_type not in {
        "application/javascript", "text/javascript", "application/x-javascript",
        "text/plain", "application/octet-stream",
    }:
        raise ValueError(f"不是可识别的 JavaScript 内容: {document.content_type}")
    source = document.body.decode("utf-8-sig", errors="replace")
    literal_pattern = re.compile(r"""["'`]([^"'\`\\]{1,500})["'`]""")
    keywords = re.compile(
        r"(?:api|search|disclosure|announcement|notice|query|list|page|json|info|ajax)",
        re.IGNORECASE,
    )
    candidates = []
    seen = set()
    for match in literal_pattern.finditer(source):
        value = match.group(1).strip()
        is_controller_path = value.lower().split("?", 1)[0].endswith(".do")
        if not value or value in seen or not (keywords.search(value) or is_controller_path):
            continue
        if not (
            value.startswith(("/", "http://", "https://", "./", "../"))
            or ".json" in value.lower()
            or ".shtml" in value.lower()
            or ".html" in value.lower()
            or is_controller_path
        ):
            continue
        seen.add(value)
        candidates.append(value)

    # 保留接口字符串附近的有限代码上下文，便于人工确认请求方法和参数。
    endpoint_contexts = []
    for endpoint in candidates[:200]:
        occurrences = []
        start = 0
        while len(occurrences) < 10:
            index = source.find(endpoint, start)
            if index < 0:
                break
            left = max(0, index - 1600)
            right = min(len(source), index + len(endpoint) + 2200)
            context = source[left:right]
            context = re.sub(
                r"(?i)(token|authorization|password|secret)(\s*[:=]\s*)[^,;&}]+",
                r"\1\2[REDACTED]",
                context,
            )
            occurrences.append({
                "offset": index,
                "context": context,
            })
            start = index + len(endpoint)
        endpoint_contexts.append({
            "endpoint": endpoint,
            "occurrence_count": source.count(endpoint),
            "occurrences": occurrences,
        })

    # 提取查询参数、分页和响应处理相关标识符的上下文，追踪其赋值来源。
    contract_terms = (
        "noticeType", "disclosureType", "disclosureSubtype", "companyCd",
        "companyCode", "startTime", "endTime", "keyword", "xxfcbj",
        "hyType", "needFields", "siteId", "pageId", "pageNum",
        "totalElements", "listInfo", "defaultParams", "pagination",
        "ajax", "exchangeData", "distype", "pageNumId",
        "defaultPaginationParams", "initDisclosureList", "xxzrlx",
        "advanced.do", "disclosure_hy.do", "sonList", "datebaseNumber",
        "businessNumber", "isAll", "dvalue", "listInfo", "disclosures",
    )
    term_contexts = []
    for term in contract_terms:
        occurrences = []
        start = 0
        while len(occurrences) < 8:
            index = source.find(term, start)
            if index < 0:
                break
            left = max(0, index - 650)
            right = min(len(source), index + len(term) + 1050)
            context = source[left:right]
            context = re.sub(
                r"(?i)(token|authorization|password|secret)(\s*[:=]\s*)[^,;&}]+",
                r"\1\2[REDACTED]",
                context,
            )
            occurrences.append({"offset": index, "context": context})
            start = index + len(term)
        if occurrences:
            term_contexts.append({
                "term": term,
                "occurrence_count": source.count(term),
                "occurrences": occurrences,
            })
    return {
        "source_id": source_id,
        "script_url": url,
        "status_code": document.status_code,
        "content_type": document.content_type,
        "content_length": len(document.body),
        "content_sha256": document.content_sha256,
        "endpoint_candidates": candidates[:200],
        "endpoint_contexts": endpoint_contexts,
        "endpoint_contracts": _extract_neeq_endpoint_contracts(source) if source_id == "neeq" else [],
        "term_contexts": term_contexts,
        "candidate_count": len(candidates),
    }


def inspect_source_assets(source_id: str, fetcher=None) -> dict:
    """Inspect registered script assets referenced by the configured source pages."""
    if source_id not in SOURCE_PAGES:
        raise ValueError(f"未知公告来源: {source_id}")
    fetcher = fetcher or LifecycleEvidenceFetcher()
    assets = {}
    pages = []
    for page_url in SOURCE_PAGES[source_id]:
        try:
            page = inspect_announcement_page(source_id, page_url, fetcher=fetcher)
            pages.append({"page_url": page_url, "status": "ok"})
            for script in page["scripts"]:
                if script["allowed_by_source_registry"]:
                    assets[script["url"]] = None
        except Exception as exc:
            pages.append({"page_url": page_url, "error": f"{type(exc).__name__}: {exc}"})
    results = []
    for url in sorted(assets):
        try:
            results.append(inspect_script_asset(source_id, url, fetcher=fetcher))
        except Exception as exc:
            results.append({"script_url": url, "error": f"{type(exc).__name__}: {exc}"})
    return {"source_id": source_id, "pages": pages, "assets": results}


def inspect_source(source_id: str, fetcher=None) -> dict:
    if source_id not in SOURCE_PAGES:
        raise ValueError(f"未知公告来源: {source_id}")
    pages = []
    for url in SOURCE_PAGES[source_id]:
        try:
            pages.append(inspect_announcement_page(source_id, url, fetcher=fetcher))
        except Exception as exc:
            pages.append({"page_url": url, "error": f"{type(exc).__name__}: {exc}"})
    return {"source_id": source_id, "pages": pages}


def main() -> None:
    parser = argparse.ArgumentParser(description="检查官方公告检索页面结构，不提交检索表单")
    parser.add_argument("--source", choices=tuple(SOURCE_PAGES), help="仅检查指定官方来源")
    parser.add_argument("--inspect-scripts", action="store_true", help="额外获取同域脚本并提取疑似接口路径")
    args = parser.parse_args()
    source_ids = (args.source,) if args.source else tuple(SOURCE_PAGES)
    inspector = inspect_source_assets if args.inspect_scripts else inspect_source
    for source_id in source_ids:
        print(json.dumps(inspector(source_id), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
