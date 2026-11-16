# 本脚本只读生成NEEQ三个P0终止挂牌案件的人工证据审阅报告，不写入案件、证据、复核或正式生命周期数据。
"""Build a read-only manual-review dossier for the three P0 NEEQ cases."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from trendradar.market_data.neeq_termination_review import (
    get_neeq_termination_case_timeline,
    list_neeq_termination_review_queue,
)
from trendradar.market_data.storage import MarketStorage


P0_CASES = {
    "836614": {
        "case_id": "neeq-result-836614-842",
        "name": "奥其斯",
        "known_context": [
            "2019-984公告的背景名单（附件一）中出现该公司，但不在该公告正式终止挂牌名单（附件二）的46家公司之列；背景名单成员身份不能当作该公告对其作出的正式终止决定。",
            "另有2019年7月22日新时代证券主办券商公告转述全国股转公司于2019年7月19日作出股转系统发〔2019〕2065号决定，条件终止日期为2019年7月29日。该材料是主办券商转述，不是已取得的全国股转公司决定原件。",
            "NEEQ结果接口候选日期为2019-09-06，与条件终止日期2019-07-29相差39天；当前材料不足以解释差异。",
        ],
        "review_focus": [
            "继续查找股转系统发〔2019〕2065号决定原件，并核对决定对象、送达日期及复核条款。",
            "查找复核申请受理、复核决定或补充决定。",
            "查找最终终止挂牌公告、证券登记结算退出记录或其他能证明实际终止日期的官方材料。",
        ],
        "suggested_review_status": "needs_more_evidence",
    },
    "838047": {
        "case_id": "neeq-result-838047-841",
        "name": "二中科技",
        "known_context": [
            "2019-984公告于2019-07-01发布；该公司列入附件二正式终止挂牌名单，公告所列计划终止挂牌日期为2019-07-09。它同时出现在附件一背景名单中，但正式名单身份来自附件二。",
            "NEEQ结果接口候选日期为2019-09-06，与公告计划日期2019-07-09相差59天。",
            "该公司未出现在此前核对的2019-09-06批量终止挂牌名单（10家公司）中；目前没有找到能解释日期差异的复核决定、补充决定或最终终止挂牌公告。",
        ],
        "review_focus": [
            "重新核对2019-984附件二中该公司的原始名单条目及日期语义。",
            "查找复核申请受理、复核决定或补充决定，确认是否存在程序变更。",
            "查找最终终止挂牌公告、证券登记结算退出记录或其他官方实际终止日期凭证。",
        ],
        "suggested_review_status": "needs_more_evidence",
    },
    "430134": {
        "case_id": "neeq-result-430134-844",
        "name": "ST可来博",
        "known_context": [
            "已登记的正式决定证据对应股转系统发〔2020〕110号，决定日期为2020-02-13，条件终止日期为2020-02-25；同时登记了主办券商2020-02-17及2020-05-12公告。",
            "2020-05-12主办券商风险提示仍要求公司披露终止挂牌公告并完成终止后的登记事项。该表述提示最终公告/登记手续当时仍是待核事项，但不能单凭它认定后来实际终止日期。",
            "NEEQ结果接口候选日期为2020-05-13，与条件终止日期2020-02-25相差78天；当前材料不足以确认该接口日期的法律含义或解释差异。",
        ],
        "review_focus": [
            "核对股转系统发〔2020〕110号决定原件及送达材料。",
            "查找复核申请受理、复核决定或补充决定。",
            "重点追查2020-05-12之后的最终终止挂牌公告及中国结算退出/登记记录。",
        ],
        "suggested_review_status": "needs_more_evidence",
    },
}


def _latest_searches(searches: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for item in searches:
        kind = item["evidence_kind"]
        previous = latest.get(kind)
        if previous is None or (item["searched_at"], item["search_id"]) > (
            previous["searched_at"], previous["search_id"]
        ):
            latest[kind] = item
    return latest


def build_report(storage: MarketStorage, generated_at: str) -> dict[str, Any]:
    """Build a report exclusively from current ledger reads plus explicitly labeled research notes."""
    queue = {
        item["company_code"]: item
        for item in list_neeq_termination_review_queue(storage, limit=1000)
    }
    cases: list[dict[str, Any]] = []
    for code, context in P0_CASES.items():
        dossier = get_neeq_termination_case_timeline(storage, context["case_id"])
        case = dossier["case"]
        if case["company_code"] != code:
            raise ValueError(f"案件代码与配置不一致: {context['case_id']}")
        latest_searches = _latest_searches(dossier["evidence_searches"])
        queue_item = queue.get(code, {})
        linked_evidence: list[dict[str, Any]] = []
        links_by_evidence: dict[str, list[dict[str, Any]]] = {}
        for link in dossier["evidence_links"]:
            links_by_evidence.setdefault(link["evidence_id"], []).append(link)
        for evidence in dossier["evidence"]:
            linked_evidence.append({
                "evidence_id": evidence["evidence_id"],
                "evidence_kind": evidence["evidence_kind"],
                "source_authority": evidence["source_authority"],
                "notice_number": evidence["notice_number"],
                "document_title": evidence["document_title"],
                "source_url": evidence["source_url"],
                "published_date": evidence["published_date"],
                "retrieved_at": evidence["retrieved_at"],
                "evidence_status": evidence["evidence_status"],
                "content_sha256": evidence["content_sha256"],
                "content_excerpt": evidence["content_excerpt"],
                "metadata": evidence["metadata"],
                "case_links": links_by_evidence.get(evidence["evidence_id"], []),
            })
        searches = [
            {
                "evidence_kind": kind,
                "latest_status": item["search_status"],
                "searched_at": item["searched_at"],
                "search_scope": item["search_scope"],
                "search_notes": item["search_notes"],
                "source_urls": item["source_urls"],
                "history_count": sum(1 for row in dossier["evidence_searches"] if row["evidence_kind"] == kind),
            }
            for kind, item in sorted(latest_searches.items())
        ]
        cases.append({
            "company_code": code,
            "company_name": case["company_name_snapshot"],
            "case_id": context["case_id"],
            "case_status": case["case_status"],
            "source_record_id": case["source_record_id"],
            "source_api": case["source_api"],
            "interface_result_date": case["interface_result_date"],
            "conditional_termination_dates": queue_item.get("conditional_termination_dates", []),
            "date_mismatch": queue_item.get("date_mismatch", False),
            "mismatch_dates": queue_item.get("mismatch_dates", []),
            "evidence_coverage_scope": "current_ledger_only",
            "evidence": linked_evidence,
            "evidence_searches": searches,
            "procedures": dossier["procedures"],
            "existing_reviews": dossier["reviews"],
            "known_context_requiring_human_check": context["known_context"],
            "review_focus": context["review_focus"],
            "suggested_review_status": context["suggested_review_status"],
            "suggested_conclusion": "当前证据不足以确认实际终止挂牌日期；建议补充证据后再复核。",
            "actual_termination_confirmed": False,
            "review_record_written": False,
        })
    return {
        "report_type": "neeq_p0_manual_evidence_review",
        "report_version": "v1",
        "generated_at": generated_at,
        "database_access": "read_only",
        "case_count": len(cases),
        "cases": cases,
        "global_safety": {
            "database_writes": False,
            "review_records_written": False,
            "formal_lifecycle_writes": False,
            "security_master_writes": False,
            "actual_termination_facts_confirmed": 0,
            "date_semantics_warning": (
                "NEEQ结果接口日期是official_result_api_candidate；条件终止日期、决定日期、"
                "公告日期和实际终止挂牌日期不可互相替代。"
            ),
            "search_scope_warning": (
                "searched_not_found仅表示当前登记的检索范围内未找到，不证明官方材料不存在；"
                "evidence_coverage_scope=current_ledger_only，不代表穷尽全部公开来源。"
            ),
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    """Render a human-readable report without converting recommendations into saved reviews."""
    lines = [
        "# NEEQ三个P0终止挂牌案件人工证据审阅报告",
        "",
        f"- 报告版本：{report['report_version']}",
        f"- 生成时间：{report['generated_at']}",
        f"- 案件数量：{report['case_count']}",
        "- 数据访问：只读",
        "- 审阅记录写入：否",
        "- 正式生命周期事实写入：否",
        "",
        "> **审阅边界：** 本报告是待人工核验的证据整理材料，不是法律意见或实际终止挂牌日期确认。结果接口日期仅为候选日期；条件终止日期、决定日期、公告日期和实际终止日期必须分别处理。",
        "",
        "> **检索边界：** `searched_not_found`仅表示已记录检索范围内未找到，不代表官方材料不存在；证据覆盖范围仅限当前本地台账。",
        "",
    ]
    for item in report["cases"]:
        lines.extend([
            f"## {item['company_code']} {item['company_name']}",
            "",
            f"- 案件ID：`{item['case_id']}`",
            f"- 案件状态：`{item['case_status']}`",
            f"- 结果接口候选日期：**{item['interface_result_date'] or '未记录'}**",
            f"- 条件终止日期：**{', '.join(item['conditional_termination_dates']) or '当前台账未记录'}**",
            f"- 日期差异：**{'是' if item['date_mismatch'] else '当前台账未检出'}**",
            f"- 建议审阅状态（尚未写入）：`{item['suggested_review_status']}`",
            "",
            "### 已登记证据",
            "",
        ])
        if not item["evidence"]:
            lines.append("- 当前台账没有关联证据。")
        for evidence in item["evidence"]:
            lines.extend([
                f"- **{evidence['evidence_kind']} / {evidence['document_title'] or evidence['evidence_id']}**",
                f"  - 发布日期：{evidence['published_date'] or '未记录'}；来源权威类型：`{evidence['source_authority']}`；登记状态：`{evidence['evidence_status']}`",
                f"  - 文号：{evidence['notice_number'] or '未记录'}",
                f"  - 来源：{evidence['source_url']}",
                f"  - 内容摘要：{evidence['content_excerpt'] or '未登记摘要'}",
                f"  - SHA-256：{evidence['content_sha256'] or '未取得/未登记'}",
            ])
            for link in evidence["case_links"]:
                lines.append(f"  - 案件关联：`{link['relation_type']}`；{link['notes'] or '无补充说明'}")
        lines.extend(["", "### 检索状态（每类仅展示最近一次，完整历史保留在JSON报告）", ""])
        if not item["evidence_searches"]:
            lines.append("- 当前台账没有检索记录。")
        for search in item["evidence_searches"]:
            lines.append(
                f"- `{search['evidence_kind']}`：**{search['latest_status']}**"
                f"（{search['searched_at']}；历史记录{search['history_count']}条）"
            )
            if search["search_notes"]:
                lines.append(f"  - {search['search_notes']}")
        lines.extend(["", "### 已知研究线索（需审阅人核对）", ""])
        lines.extend(f"- {note}" for note in item["known_context_requiring_human_check"])
        lines.extend(["", "### 建议审阅动作", ""])
        lines.extend(f"{index}. {action}" for index, action in enumerate(item["review_focus"], 1))
        lines.extend([
            "",
            f"- 建议结论：{item['suggested_conclusion']}",
            "- 实际终止挂牌日期已确认：**否**",
            "- 本报告是否写入人工复核记录：**否**",
            "",
        ])
    lines.extend([
        "## 全局安全核验",
        "",
        "- 数据库写入：否",
        "- 人工复核记录写入：否",
        "- 正式生命周期写入：否",
        "- security_master写入：否",
        "- 实际终止挂牌事实确认数：0",
        "",
        "建议审阅人逐案核对原始公告及附件。完成审阅后，再由人工明确给出复核状态和意见；本报告生成程序不会自动提交复核。",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="只读生成NEEQ三个P0案件人工证据审阅报告")
    parser.add_argument("--db", default="output/market/market.db", help="市场数据库路径")
    parser.add_argument(
        "--output", default="output/market/neeq_p0_manual_review_report.md",
        help="Markdown报告输出路径",
    )
    parser.add_argument(
        "--json-output", default="output/market/neeq_p0_manual_review_report.json",
        help="JSON报告输出路径",
    )
    args = parser.parse_args()
    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    storage = MarketStorage(args.db)
    report = build_report(storage, generated_at)
    markdown_path = Path(args.output)
    json_path = Path(args.json_output)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "status": "generated",
        "report_type": report["report_type"],
        "generated_at": generated_at,
        "case_count": report["case_count"],
        "markdown_path": str(markdown_path),
        "json_path": str(json_path),
        "database_access": "read_only",
        "database_writes": False,
        "review_records_written": False,
        "formal_lifecycle_writes": False,
        "security_master_writes": False,
        "actual_termination_facts_confirmed": 0,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
