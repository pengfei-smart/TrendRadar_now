# 本文件解析全国股转公司批量终止挂牌决定公告附表，并按证券代码核对结果接口记录。
"""Parse company rows in batch NEEQ termination decision notices."""

from __future__ import annotations

import re


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value or "")


def parse_neeq_termination_roster(text: str) -> dict:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("批量终止挂牌公告正文不能为空")
    compact_lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    compact_lines = [line for line in compact_lines if line]
    rows = {}
    for index, line in enumerate(compact_lines):
        codes = list(re.finditer(r"(?<!\d)(\d{6})(?!\d)", line))
        for match in codes:
            code = match.group(1)
            start = max(0, match.start() - 8)
            end = min(len(line), match.end() + 100)
            context = line[start:end]
            # PDF 双栏表格可能把同一行的两家公司放在一起；以代码为锚点保留局部上下文。
            tail = line[match.end():]
            broker_match = re.search(
                r"([\u4e00-\u9fffA-Za-z0-9（）()]+(?:证券|承销保荐))",
                tail,
            )
            broker = broker_match.group(1) if broker_match else ""
            before = line[max(0, match.start() - 24):match.start()].strip()
            after = tail[:broker_match.start()].strip() if broker_match else tail[:32].strip()
            # 附表列通常为“序号 证券代码 证券简称 主办券商”；不强制名称与结果列表一致。
            rows.setdefault(code, {
                "security_code": code,
                "line_index": index,
                "context": context,
                "name_context": after,
                "prefix_context": before,
                "host_broker_in_notice": broker,
            })
    return {
        "security_codes": sorted(rows),
        "rows": rows,
        "document_mentions_decision": (
            "作出终止" in text and "挂牌决定" in text
        ),
        "document_number": (
            re.search(r"股转公告\s*〔\s*(20\d{2})\s*〕\s*(\d+)\s*号", text)
        ).group(0).replace(" ", "") if re.search(
            r"股转公告\s*〔\s*(20\d{2})\s*〕\s*(\d+)\s*号", text
        ) else "",
        "decision_date": (
            (lambda m: f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}")(
                re.search(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", text)
            )
            if re.search(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", text)
            else ""
        ),
    }


def match_neeq_result_record(record: dict, roster: dict) -> dict:
    if not isinstance(record, dict) or not isinstance(roster, dict):
        raise TypeError("record 和 roster 必须是 dict")
    code = str(record.get("companyCode") or "").strip()
    if len(code) != 6 or not code.isdigit():
        raise ValueError("结果记录缺少有效 companyCode")
    row = roster.get("rows", {}).get(code)
    if not row:
        return {
            "company_code": code, "roster_code_match": False,
            "broker_match": False, "name_match": False,
            "match_status": "code_not_found_in_batch_notice",
        }
    broker = str(record.get("hostBroker") or "").strip()
    notice_broker = str(row.get("host_broker_in_notice") or "").strip()
    return {
        "company_code": code,
        "roster_code_match": True,
        "broker_match": bool(broker and notice_broker and broker == notice_broker),
        "result_name": str(record.get("companyName") or ""),
        "notice_name_context": row.get("name_context", ""),
        "name_match": bool(
            _compact(str(record.get("companyName") or "")) in
            _compact(row.get("name_context", ""))
        ),
        "host_broker": broker,
        "notice_host_broker": notice_broker,
        "match_status": (
            "code_and_broker_match" if broker and broker == notice_broker
            else "code_match_broker_unconfirmed"
        ),
    }
