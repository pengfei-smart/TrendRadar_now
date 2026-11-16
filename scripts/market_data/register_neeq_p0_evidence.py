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
            "evidence_id": "neeq-evidence-836614-sponsor-20190722-dfcfw",
            "case_id": "neeq-result-836614-842",
            "evidence_kind": "sponsor_notice",
            "source_authority": "sponsor_official",
            "notice_number": "股转系统发〔2019〕2065号（2019-07-22主办券商风险提示）",
            "document_title": "关于全国中小企业股份转让系统有限责任公司对奥其斯科技股份有限公司作出终止股票挂牌决定的风险提示性公告",
            "source_url": "https://pdf.dfcfw.com/pdf/H2_AN201907221340608636_1.pdf",
            "published_date": "2019-07-22",
            "retrieved_at": retrieved_at,
            "evidence_status": "located",
            "content_excerpt": "PDF落款为2019年7月22日；公告转述股转系统发〔2019〕2065号决定及2019年7月29日条件终止日期。",
            "metadata": {
                "document_issuer": "新时代证券股份有限公司",
                "hosting_domain": "pdf.dfcfw.com",
                "document_is_original_neeq_decision": False,
                "relationship_to_2019_07_19_copy": "同标题、核心内容相同的另一份主办券商风险提示版本；分别保留原始落款日期和来源。",
                "limitation": "主办券商公告转述正式决定；不证明复核条件已满足或实际终止日期。",
            },
        },
        {
            "evidence_id": "neeq-evidence-836614-sponsor-20190829",
            "case_id": "neeq-result-836614-842",
            "evidence_kind": "sponsor_notice",
            "source_authority": "sponsor_official",
            "notice_number": "",
            "document_title": "关于奥其斯科技股份有限公司预计无法按期披露2019年半年度报告的风险提示公告",
            "source_url": "https://qxb-pdf-osscache.qixin.com/AnBaseinfo/735c449ef187f95c975ddfaaa02eaeba.pdf",
            "published_date": "2019-08-29",
            "retrieved_at": retrieved_at,
            "evidence_status": "located",
            "content_excerpt": "新时代证券2019年8月29日公告称奥其斯仍应于2019年8月30日前披露2019年半年度报告，并提示如2019年10月31日前仍无法披露则股票存在被终止挂牌风险；同一公告再次提及2019年7月19日股转系统发〔2019〕2065号决定。",
            "metadata": {
                "document_issuer": "新时代证券股份有限公司",
                "hosting_domain": "qxb-pdf-osscache.qixin.com",
                "document_is_original_neeq_decision": False,
                "procedural_conflict": "2019-08-29仍以持续督导和未来终止挂牌风险进行提示，与2019-07-19决定所述2019-07-29条件日期之间存在需解释的程序矛盾。",
                "limitation": "该主办券商公告不能证明复核申请已提交/受理，也不能单独证明2019-07-29未实际终止挂牌。",
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
            "link_id": "neeq-link-836614-sponsor-20190722-dfcfw",
            "case_id": "neeq-result-836614-842",
            "evidence_id": "neeq-evidence-836614-sponsor-20190722-dfcfw",
            "relation_type": "supports_case",
            "notes": "主办券商2019-07-22风险提示版本；与7月19日版本分别登记，不将两者发布日期混为一谈。",
        },
        {
            "link_id": "neeq-link-836614-sponsor-20190829",
            "case_id": "neeq-result-836614-842",
            "evidence_id": "neeq-evidence-836614-sponsor-20190829",
            "relation_type": "supports_case",
            "notes": "后续持续督导风险提示，揭示2019-07-29条件日期与2019-08-29披露义务之间的待解释矛盾；不证明复核结果或实际终止日期。",
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
            "search_id": "neeq-search-836614-review-application-20261116",
            "case_id": "neeq-result-836614-842",
            "evidence_kind": "review_application",
            "search_status": "searched_not_found",
            "searched_at": retrieved_at,
            "search_scope": scope,
            "search_notes": "当前检索范围未找到复核申请公开记录；不得据此推断公司未申请复核。",
            "source_urls": ["https://www.neeq.com.cn/disclosure/termination_listing.html"],
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
            "source_urls": ["https://www.neeq.com.cn/disclosure/termination_listing.html"],
            "reviewer": "TrendRadar research",
        },
        {
            "search_id": "neeq-search-836614-review-application-followup-20261116",
            "case_id": "neeq-result-836614-842",
            "evidence_kind": "review_application",
            "search_status": "searched_not_found",
            "searched_at": retrieved_at,
            "search_scope": scope,
            "search_notes": "新增核对2019-08-29主办券商持续督导公告后，仍未找到2065号决定的复核申请公开记录；该公告构成程序矛盾线索，但不能据此推断复核申请不存在或已获受理。",
            "source_urls": [
                "https://qxb-pdf-osscache.qixin.com/AnBaseinfo/735c449ef187f95c975ddfaaa02eaeba.pdf",
                "https://notice.10jqka.com.cn/api/pdf/91b688ddd44596d2.pdf",
                "https://www.neeq.com.cn/disclosure/termination_listing.html",
            ],
            "reviewer": "TrendRadar research",
        },
        {
            "search_id": "neeq-search-836614-review-decision-followup-20261116",
            "case_id": "neeq-result-836614-842",
            "evidence_kind": "review_decision",
            "search_status": "searched_not_found",
            "searched_at": retrieved_at,
            "search_scope": scope,
            "search_notes": "当前检索范围未找到针对股转系统发〔2019〕2065号的复核决定或撤销/变更决定；2019-08-29主办券商公告不能替代复核结果。",
            "source_urls": [
                "https://qxb-pdf-osscache.qixin.com/AnBaseinfo/735c449ef187f95c975ddfaaa02eaeba.pdf",
                "https://www.neeq.com.cn/disclosure/termination_listing.html",
            ],
            "reviewer": "TrendRadar research",
        },
        {
            "search_id": "neeq-search-836614-final-status-followup-20261116",
            "case_id": "neeq-result-836614-842",
            "evidence_kind": "final_termination_notice",
            "search_status": "searched_not_found",
            "searched_at": retrieved_at,
            "search_scope": scope,
            "search_notes": "尚未找到足以解释2019-08-29后续披露义务与2019-07-29条件日期关系的最终终止挂牌生效公告或中国结算退出登记凭证；不得确认2019-07-29为实际日期。",
            "source_urls": [
                "https://qxb-pdf-osscache.qixin.com/AnBaseinfo/735c449ef187f95c975ddfaaa02eaeba.pdf",
                "https://www.neeq.com.cn/disclosure/termination_listing.html",
            ],
            "reviewer": "TrendRadar research",
        },
        {
            "search_id": "neeq-search-836614-sponsor-date-reconciliation-20261116",
            "case_id": "neeq-result-836614-842",
            "evidence_kind": "sponsor_notice",
            "search_status": "found_not_registered",
            "searched_at": retrieved_at,
            "search_scope": scope,
            "search_notes": "已定位10jqka PDF（正文落款2019-07-19，旧台账记录日期为2019-07-22）、东方财富另一份2019-07-22 PDF及2019-08-29后续风险提示。10jqka同一URL只保留原有证据ID，不再以新ID重复登记；日期差异通过本次追加检索记录留痕，待原件哈希核验后再决定是否修正历史元数据。",
            "source_urls": [
                "https://notice.10jqka.com.cn/api/pdf/91b688ddd44596d2.pdf",
                "https://pdf.dfcfw.com/pdf/H2_AN201907221340608636_1.pdf",
                "https://qxb-pdf-osscache.qixin.com/AnBaseinfo/735c449ef187f95c975ddfaaa02eaeba.pdf",
            ],
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
            "search_id": "neeq-search-838047-review-application-20261116",
            "case_id": "neeq-result-838047-841",
            "evidence_kind": "review_application",
            "search_status": "searched_not_found",
            "searched_at": retrieved_at,
            "search_scope": scope,
            "search_notes": "当前检索范围未找到复核申请公开记录；不得据此推断公司未申请复核。",
            "source_urls": ["https://www.neeq.com.cn/disclosure/termination_listing.html"],
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
            "source_urls": ["https://www.neeq.com.cn/disclosure/termination_listing.html"],
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
            "source_urls": ["https://www.neeq.com.cn/disclosure/termination_listing.html"],
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
            "search_id": "neeq-search-430134-review-application-20261116",
            "case_id": "neeq-result-430134-844",
            "evidence_kind": "review_application",
            "search_status": "searched_not_found",
            "searched_at": retrieved_at,
            "search_scope": scope,
            "search_notes": "当前检索范围未找到复核申请公开记录；不得据此推断公司未申请复核。",
            "source_urls": ["https://www.neeq.com.cn/disclosure/termination_listing.html"],
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
            "source_urls": ["https://www.neeq.com.cn/disclosure/termination_listing.html"],
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
            "source_urls": ["https://www.neeq.com.cn/disclosure/termination_listing.html"],
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
        for item in payload["searches"]:
            if item["search_status"] != "registered":
                continue
            if item["evidence_kind"] == "formal_decision":
                registered = connection.execute(
                    """SELECT 1 FROM neeq_termination_evidence e
                    JOIN neeq_termination_evidence_link l ON l.evidence_id=e.evidence_id
                    WHERE l.case_id=? AND (
                        e.evidence_kind='formal_decision'
                        OR (e.evidence_kind='batch_notice' AND l.relation_type='supports_procedure')
                    ) LIMIT 1""",
                    (item["case_id"],),
                ).fetchone()
            else:
                registered = connection.execute(
                    """SELECT 1 FROM neeq_termination_evidence e
                    JOIN neeq_termination_evidence_link l ON l.evidence_id=e.evidence_id
                    WHERE l.case_id=? AND e.evidence_kind=? LIMIT 1""",
                    (item["case_id"], item["evidence_kind"]),
                ).fetchone()
            if not registered:
                raise ValueError(
                    f"检索状态标记为registered，但台账中没有对应案件证据: "
                    f"{item['case_id']}:{item['evidence_kind']}"
                )
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
        evidence_placeholders = ",".join("?" for _ in payload["evidence"])
        link_placeholders = ",".join("?" for _ in payload["links"])
        actual = {
            "evidence": connection.execute(
                f"SELECT COUNT(*) FROM neeq_termination_evidence WHERE evidence_id IN ({evidence_placeholders})",
                tuple(item["evidence_id"] for item in payload["evidence"]),
            ).fetchone()[0],
            "links": connection.execute(
                f"SELECT COUNT(*) FROM neeq_termination_evidence_link WHERE link_id IN ({link_placeholders})",
                tuple(item["link_id"] for item in payload["links"]),
            ).fetchone()[0],
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
