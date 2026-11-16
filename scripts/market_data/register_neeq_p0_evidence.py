# 本脚本将P0案件已定位的官方/主办券商公告登记到独立NEEQ证据台账，并记录检索状态。
"""Register located P0 NEEQ evidence and append investigation states."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from typing import Any

from trendradar.market_data.storage import MarketStorage


def build_payload(retrieved_at: str) -> dict[str, Any]:
    """Build a deterministic, metadata-only payload; no document bytes or legal facts are fabricated."""
    evidence = [
        {
            "evidence_id": "neeq-evidence-836614-sponsor-20190722",
            "case_id": "neeq-result-836614-842",
            "evidence_kind": "sponsor_notice",
            "source_authority": "sponsor_official",
            "notice_number": "股转系统发〔2019〕2065号（主办券商风险提示）",
            "document_title": "关于全国中小企业股份转让系统有限责任公司对奥其斯科技股份有限公司作出终止股票挂牌决定的风险提示性公告",
            "source_url": "https://notice.10jqka.com.cn/api/pdf/91b688ddd44596d2.pdf",
            "published_date": "2019-07-22",
            "retrieved_at": retrieved_at,
            "evidence_status": "located",
            "content_excerpt": "新时代证券公告称，全国股转公司于2019年7月19日出具股转系统发〔2019〕2065号决定；若未申请复核或复核申请未获受理，自2019年7月29日起终止挂牌。",
            "metadata": {
                "document_issuer": "新时代证券股份有限公司",
                "hosting_domain": "notice.10jqka.com.cn",
                "document_is_original_neeq_decision": False,
                "limitation": "主办券商公告转述正式决定；当前未直接取得决定原件。",
            },
        },
        {
            "evidence_id": "neeq-evidence-430134-sponsor-20200217",
            "case_id": "neeq-result-430134-844",
            "evidence_kind": "sponsor_notice",
            "source_authority": "sponsor_official",
            "notice_number": "股转系统发〔2020〕110号（主办券商风险提示）",
            "document_title": "ST可来博主办券商关于收到终止股票挂牌决定的风险提示性公告",
            "source_url": "https://www.neeq.com.cn/disclosure/2020/2020-02-17/1581929765_542697.pdf",
            "published_date": "2020-02-17",
            "retrieved_at": retrieved_at,
            "evidence_status": "located",
            "content_excerpt": "主办券商公告说明公司收到全国股转公司终止挂牌决定；条件终止日期为2020年2月25日。",
            "metadata": {
                "document_issuer": "山西证券股份有限公司",
                "hosting_domain": "www.neeq.com.cn",
                "retrieval_note": "原文PDF链接已定位；网页抓取返回403，尚未完成本地下载与哈希校验。",
            },
        },
        {
            "evidence_id": "neeq-evidence-430134-sponsor-20200512",
            "case_id": "neeq-result-430134-844",
            "evidence_kind": "sponsor_notice",
            "source_authority": "sponsor_official",
            "notice_number": "",
            "document_title": "山西证券关于北京中科可来博电子科技股份有限公司的风险提示公告",
            "source_url": "https://www.neeq.com.cn/disclosure/2020/2020-05-12/1589283254_428161.pdf",
            "published_date": "2020-05-12",
            "retrieved_at": retrieved_at,
            "evidence_status": "located",
            "content_excerpt": "此前检索的公告内容显示，主办券商仍提示公司及时披露终止挂牌公告并办理后续手续；该文件不能证明2020年5月13日就是实际终止日期。",
            "metadata": {
                "document_issuer": "山西证券股份有限公司",
                "hosting_domain": "www.neeq.com.cn",
                "retrieval_note": "原文PDF链接已定位；网页抓取返回403，尚未完成本地下载与哈希校验。",
            },
        },
    ]
    links = [
        {
            "link_id": "neeq-link-836614-sponsor-20190722",
            "case_id": "neeq-result-836614-842",
            "evidence_id": "neeq-evidence-836614-sponsor-20190722",
            "relation_type": "supports_case",
            "notes": "主办券商转述正式决定及条件终止日期；不替代全国股转公司决定原件。",
        },
        {
            "link_id": "neeq-link-430134-sponsor-20200217",
            "case_id": "neeq-result-430134-844",
            "evidence_id": "neeq-evidence-430134-sponsor-20200217",
            "relation_type": "supports_case",
            "notes": "主办券商公告，支持收到决定及条件终止日期的调查。",
        },
        {
            "link_id": "neeq-link-430134-sponsor-20200512",
            "case_id": "neeq-result-430134-844",
            "evidence_id": "neeq-evidence-430134-sponsor-20200512",
            "relation_type": "supports_case",
            "notes": "主办券商后续风险提示；用于解释程序仍待核查，不证明实际终止日期。",
        },
    ]
    scope = "全国股转系统公开公告页、公告索引及可检索的主办券商公告；仅代表本次检索范围。"
    searches = [
        {
            "search_id": "neeq-search-836614-formal-decision-20261116",
            "case_id": "neeq-result-836614-842",
            "evidence_kind": "formal_decision",
            "search_status": "searched_not_found",
            "searched_at": retrieved_at,
            "search_scope": scope,
            "search_notes": "已定位主办券商对股转系统发〔2019〕2065号决定的转述，但尚未取得全国股转公司决定原件；不代表决定不存在。",
            "source_urls": ["https://notice.10jqka.com.cn/api/pdf/91b688ddd44596d2.pdf"],
            "reviewer": "TrendRadar research",
        },
        {
            "search_id": "neeq-search-836614-sponsor-notice-20261116",
            "case_id": "neeq-result-836614-842",
            "evidence_kind": "sponsor_notice",
            "search_status": "found_not_registered",
            "searched_at": retrieved_at,
            "search_scope": scope,
            "search_notes": "已定位新时代证券2019年7月22日风险提示公告，待登记后应转为registered。",
            "source_urls": ["https://notice.10jqka.com.cn/api/pdf/91b688ddd44596d2.pdf"],
            "reviewer": "TrendRadar research",
        },
        {
            "search_id": "neeq-search-836614-review-decision-20261116",
            "case_id": "neeq-result-836614-842",
            "evidence_kind": "review_decision",
            "search_status": "searched_not_found",
            "searched_at": retrieved_at,
            "search_scope": scope,
            "search_notes": "当前检索范围未找到复核申请/复核决定；不得据此推断公司未申请复核。",
            "source_urls": ["https://www.neeq.com.cn/disclosure/termination_listing.html"],
            "reviewer": "TrendRadar research",
        },
        {
            "search_id": "neeq-search-836614-final-notice-20261116",
            "case_id": "neeq-result-836614-842",
            "evidence_kind": "final_termination_notice",
            "search_status": "searched_not_found",
            "searched_at": retrieved_at,
            "search_scope": scope,
            "search_notes": "当前检索范围未找到最终终止挂牌公告或官方摘牌结果记录；不代表其不存在。",
            "source_urls": ["https://www.neeq.cn/disclosure/termination_listing.html"],
            "reviewer": "TrendRadar research",
        },
        {
            "search_id": "neeq-search-838047-formal-decision-20261116",
            "case_id": "neeq-result-838047-841",
            "evidence_kind": "formal_decision",
            "search_status": "registered",
            "searched_at": retrieved_at,
            "search_scope": scope,
            "search_notes": "股转系统公告〔2019〕984号附表2已作为批次公告证据登记，并与该公司正式决定程序关联。",
            "source_urls": ["https://www.neeq.com.cn/important_news/200006230.html"],
            "reviewer": "TrendRadar research",
        },
        {
            "search_id": "neeq-search-838047-review-decision-20261116",
            "case_id": "neeq-result-838047-841",
            "evidence_kind": "review_decision",
            "search_status": "searched_not_found",
            "searched_at": retrieved_at,
            "search_scope": scope,
            "search_notes": "当前检索范围未找到复核申请/复核决定；不得据此推断公司未申请复核。",
            "source_urls": ["https://www.neeq.cn/disclosure/termination_listing.html"],
            "reviewer": "TrendRadar research",
        },
        {
            "search_id": "neeq-search-838047-final-notice-20261116",
            "case_id": "neeq-result-838047-841",
            "evidence_kind": "final_termination_notice",
            "search_status": "searched_not_found",
            "searched_at": retrieved_at,
            "search_scope": scope,
            "search_notes": "当前检索范围未找到最终终止挂牌公告或官方摘牌结果记录；不代表其不存在。",
            "source_urls": ["https://www.neeq.cn/disclosure/termination_listing.html"],
            "reviewer": "TrendRadar research",
        },
        {
            "search_id": "neeq-search-430134-formal-decision-20261116",
            "case_id": "neeq-result-430134-844",
            "evidence_kind": "formal_decision",
            "search_status": "registered",
            "searched_at": retrieved_at,
            "search_scope": scope,
            "search_notes": "全国股转公司正式决定原文链接已定位并已存在于当前证据台账；本次不重复插入。",
            "source_urls": ["https://www.neeq.com.cn/uploads/1/file/public/202002/20200217153226_t75f1e2e80.pdf"],
            "reviewer": "TrendRadar research",
        },
        {
            "search_id": "neeq-search-430134-sponsor-notice-20261116",
            "case_id": "neeq-result-430134-844",
            "evidence_kind": "sponsor_notice",
            "search_status": "found_not_registered",
            "searched_at": retrieved_at,
            "search_scope": scope,
            "search_notes": "已定位2020年2月17日及5月12日两份山西证券风险提示公告，待登记后应转为registered。",
            "source_urls": [
                "https://www.neeq.com.cn/disclosure/2020/2020-02-17/1581929765_542697.pdf",
                "https://www.neeq.com.cn/disclosure/2020/2020-05-12/1589283254_428161.pdf",
            ],
            "reviewer": "TrendRadar research",
        },
        {
            "search_id": "neeq-search-430134-review-decision-20261116",
            "case_id": "neeq-result-430134-844",
            "evidence_kind": "review_decision",
            "search_status": "searched_not_found",
            "searched_at": retrieved_at,
            "search_scope": scope,
            "search_notes": "当前检索范围未找到复核申请/复核决定；不得据此推断公司未申请复核。",
            "source_urls": ["https://www.neeq.cn/disclosure/termination_listing.html"],
            "reviewer": "TrendRadar research",
        },
        {
            "search_id": "neeq-search-430134-final-notice-20261116",
            "case_id": "neeq-result-430134-844",
            "evidence_kind": "final_termination_notice",
            "search_status": "searched_not_found",
            "searched_at": retrieved_at,
            "search_scope": scope,
            "search_notes": "当前检索范围未找到最终终止挂牌公告或官方摘牌结果记录；不代表其不存在。",
            "source_urls": ["https://www.neeq.cn/disclosure/termination_listing.html"],
            "reviewer": "TrendRadar research",
        },
    ]
    return {"evidence": evidence, "links": links, "searches": searches}


def apply_payload(storage: MarketStorage, payload: dict[str, Any]) -> dict[str, int]:
    """Write evidence, case links and investigation history atomically to the separate NEEQ ledger."""
    expected_cases = {"neeq-result-836614-842", "neeq-result-838047-841", "neeq-result-430134-844"}
    with storage.connect() as connection:
        found = {
            row["case_id"] for row in connection.execute(
                "SELECT case_id FROM neeq_termination_case WHERE case_id IN (?,?,?)",
                tuple(sorted(expected_cases)),
            ).fetchall()
        }
        if found != expected_cases:
            raise ValueError(f"P0案件不完整，拒绝导入: missing={sorted(expected_cases-found)}")
        evidence_count = storage.save_neeq_termination_evidence(payload["evidence"], connection=connection)
        link_count = storage.link_neeq_termination_evidence(payload["links"], connection=connection)
        search_count = sum(
            storage.record_neeq_termination_evidence_search(item, connection=connection)
            for item in payload["searches"]
        )
        expected = {
            "evidence": len(payload["evidence"]),
            "links": len(payload["links"]),
            "searches": len(payload["searches"]),
        }
        actual = {
            "evidence": connection.execute("SELECT COUNT(*) FROM neeq_termination_evidence WHERE evidence_id IN (?,?,?)", tuple(item["evidence_id"] for item in payload["evidence"])).fetchone()[0],
            "links": connection.execute("SELECT COUNT(*) FROM neeq_termination_evidence_link WHERE link_id IN (?,?,?)", tuple(item["link_id"] for item in payload["links"])).fetchone()[0],
            "searches": connection.execute("SELECT COUNT(*) FROM neeq_termination_evidence_search WHERE search_id IN (" + ",".join("?" for _ in payload["searches"]) + ")", tuple(item["search_id"] for item in payload["searches"])).fetchone()[0],
        }
        if actual != expected:
            raise RuntimeError(f"P0证据导入回读校验失败: expected={expected}, actual={actual}")
    return {"evidence": evidence_count, "links": link_count, "searches": search_count}


def main() -> int:
    parser = argparse.ArgumentParser(description="登记NEEQ P0案件已定位证据及调查状态")
    parser.add_argument("--db", default="output/market/market.db")
    parser.add_argument("--retrieved-at", default="")
    parser.add_argument("--apply", action="store_true", help="实际写入；默认仅预览")
    args = parser.parse_args()
    retrieved_at = args.retrieved_at or datetime.now().astimezone().isoformat(timespec="seconds")
    payload = build_payload(retrieved_at)
    result = {
        "status": "ready",
        "retrieved_at": retrieved_at,
        "evidence_count": len(payload["evidence"]),
        "link_count": len(payload["links"]),
        "search_record_count": len(payload["searches"]),
        "apply": args.apply,
        "safety": {
            "actual_termination_facts_confirmed": 0,
            "formal_lifecycle_writes": 0,
            "security_master_writes": 0,
            "read_only": not args.apply,
        },
    }
    if args.apply:
        counts = apply_payload(MarketStorage(args.db), payload)
        result["status"] = "applied"
        result["readback"] = counts
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
