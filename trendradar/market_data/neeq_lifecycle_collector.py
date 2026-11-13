# 本文件解析全国股转系统动态公告分类并构建退市公告检索计划，不直接发起未验证的接口请求。
"""Build safe, reviewable NEEQ lifecycle announcement search plans."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.parse import urljoin

from trendradar.market_data.lifecycle_source_registry import get_source, is_allowed_url


NEEQ_BASE_URL = "https://www.neeq.com.cn/"
NEEQ_LIST_ENDPOINT = "/disclosureInfoController/companyAnnouncement.do"
NEEQ_LIFECYCLE_TERMS = (
    "终止挂牌", "摘牌", "终止上市", "退市", "两网及退市",
    "退市板块", "终止股票挂牌", "终止转让",
)
NEEQ_MARKET_LAYER_VALUES = frozenset({"0", "1", "3"})


@dataclass(frozen=True)
class NEEQCategory:
    """A category node discovered dynamically from the official category response."""

    title: str
    datebase_number: str
    business_number: str
    level: int
    parent_title: str = ""

    @property
    def search_key(self) -> str:
        return f"{self.level}:{self.datebase_number}"


def _first_category_list(payload: Any) -> list:
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict) and isinstance(item.get("list"), list):
                return item["list"]
        return []
    if isinstance(payload, dict) and isinstance(payload.get("list"), list):
        return payload["list"]
    return []


def extract_neeq_categories(payload: Any) -> list[NEEQCategory]:
    """Extract dynamic parent/child categories; never hardcode category IDs."""
    result: list[NEEQCategory] = []
    seen: set[tuple[int, str, str]] = set()

    def add(node: Any, level: int, parent_title: str = "") -> None:
        if not isinstance(node, dict):
            return
        title = str(node.get("title") or "").strip()
        category_id = str(node.get("datebaseNumber") or "").strip()
        business_number = str(node.get("businessNumber") or "").strip()
        if title and category_id:
            key = (level, category_id, business_number)
            if key not in seen:
                seen.add(key)
                result.append(NEEQCategory(
                    title, category_id, business_number, level, parent_title
                ))
        children = node.get("sonList")
        if isinstance(children, list):
            for child in children:
                add(child, 1, title or parent_title)

    for root in _first_category_list(payload):
        add(root, 0)
    return result


def find_neeq_lifecycle_categories(
    categories: list[NEEQCategory],
    terms: tuple[str, ...] = NEEQ_LIFECYCLE_TERMS,
) -> list[NEEQCategory]:
    """Find title matches as review candidates, not as confirmed legal categories."""
    normalized = tuple(term.casefold() for term in terms)
    return [
        item for item in categories
        if any(term in item.title.casefold() for term in normalized)
    ]


def _validate_date(value: str, field_name: str) -> str:
    if not value:
        return ""
    try:
        return date.fromisoformat(value).isoformat()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} 必须为 YYYY-MM-DD") from exc


def build_neeq_announcement_search_plan(
    *,
    start_date: str,
    end_date: str,
    keyword: str = "",
    market_layer: str = "3",
    page: int = 1,
    company_code: str = "",
    notice_type: str = "",
    disclosure_type: str = "",
    disclosure_subtype: str = "",
    industry_type: str = "",
    need_fields: tuple[str, ...] = (),
) -> dict:
    """Build a query plan only; dictionary values must come from official responses."""
    get_source("neeq")
    start = _validate_date(start_date, "start_date")
    end = _validate_date(end_date, "end_date")
    if not start or not end or start > end:
        raise ValueError("必须提供有效日期范围，且 start_date 不得晚于 end_date")
    if market_layer not in NEEQ_MARKET_LAYER_VALUES:
        raise ValueError("market_layer 仅允许已确认的 0、1、3；不接受未核实的值")
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise ValueError("page 必须是大于等于 1 的整数")
    if company_code and (len(company_code) != 6 or not company_code.isdigit()):
        raise ValueError("company_code 必须是六位数字证券代码")
    if any(not isinstance(value, str) or not value.strip() for value in need_fields):
        raise ValueError("need_fields 必须由非空字符串组成")

    endpoint = urljoin(NEEQ_BASE_URL, NEEQ_LIST_ENDPOINT)
    params = {
        "noticeType": notice_type,
        "disclosureType": disclosure_type,
        "disclosureSubtype": disclosure_subtype,
        "page": page,
        "companyCd": company_code,
        "isNewThree": 1,
        "startTime": start,
        "endTime": end,
        "keyword": keyword.strip(),
        "xxfcbj": market_layer,
        "hyType": industry_type,
        "needFields": list(need_fields),
        "siteId": 1,
    }
    return {
        "source_id": "neeq",
        "endpoint": endpoint,
        "endpoint_allowed": is_allowed_url("neeq", endpoint),
        "http_method": "unverified",
        "request_encoding": "JSONP",
        "params": params,
        "evidence_level": "static_contract_plan",
        "runtime_verified": False,
        "warnings": [
            "当前仅生成检索计划，尚未发送请求。",
            "公告类型、行业类型及分类 ID 必须来自实时官方字典/分类响应。",
            "companyAnnouncement.do 的 HTTP method 尚未由静态证据确认。",
            "检索结果必须保存原始公告元数据和原文哈希，并进入人工审核。",
        ],
    }


def normalize_neeq_announcement_response(payload: Any) -> dict:
    """Normalize the observed companyAnnouncement response envelope, fail closed."""
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
        raise ValueError("公告响应不符合已观察到的数组首项结构")
    list_info = payload[0].get("listInfo")
    if not isinstance(list_info, dict):
        raise ValueError("公告响应缺少 listInfo 对象")
    records = list_info.get("content")
    total = list_info.get("totalElements")
    if not isinstance(records, list):
        raise ValueError("公告响应 listInfo.content 不是数组")
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        raise ValueError("公告响应 listInfo.totalElements 无效")
    return {
        "records": records,
        "total_elements": total,
        "response_shape": "array[0].listInfo",
        "runtime_verified": False,
    }


def is_lifecycle_announcement_title(title: str) -> bool:
    """Flag likely lifecycle titles for review; never confirms an event."""
    if not isinstance(title, str):
        return False
    normalized = title.replace(" ", "").casefold()
    return any(term in normalized for term in NEEQ_LIFECYCLE_TERMS)
