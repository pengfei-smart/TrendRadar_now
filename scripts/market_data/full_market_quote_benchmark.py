# 本脚本用于对A股有效证券池执行东方财富批量行情压力测试，逐批隔离失败并输出覆盖率、耗时和新鲜度报告。
"""Run a cautious, read-only full-universe quote batch benchmark."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from statistics import mean
from typing import Sequence

import pytz

from trendradar.market_data.models import QuoteSnapshot, Security
from trendradar.market_data.quote_fetcher import EastmoneyBatchQuoteProvider
from trendradar.market_data.security_master_provider import (
    EastmoneySecurityMasterProvider,
    eligible_securities,
)


def quote_age_seconds(timestamp: str, collected_at: datetime) -> int | None:
    """Return non-negative quote age; reject malformed, naive, or far-future timestamps."""
    try:
        parsed = datetime.fromisoformat(timestamp)
        if parsed.tzinfo is None:
            return None
        age = (collected_at - parsed.astimezone(timezone.utc)).total_seconds()
        if age < -60:
            return None
        return max(0, int(age))
    except (TypeError, ValueError, OverflowError):
        return None


def freshness_counts(quotes: Sequence[QuoteSnapshot], collected_at: datetime,
                     max_age_seconds: int) -> tuple[dict[str, int], list[dict[str, object]]]:
    counts = {"fresh": 0, "stale": 0, "invalid": 0}
    samples: list[dict[str, object]] = []
    for quote in quotes:
        age = quote_age_seconds(quote.timestamp, collected_at)
        status = "invalid" if age is None else ("fresh" if age <= max_age_seconds else "stale")
        counts[status] += 1
        samples.append({
            "code": quote.code,
            "provider_timestamp": quote.timestamp,
            "quote_age_seconds": age,
            "freshness": status,
        })
    return counts, samples


def freshness_status(counts: dict[str, int]) -> str:
    if counts["invalid"]:
        return "invalid"
    if counts["stale"]:
        return "stale"
    if counts["fresh"]:
        return "fresh"
    return "not_available"


def chunks(items: Sequence[Security], size: int) -> list[Sequence[Security]]:
    return [items[index:index + size] for index in range(0, len(items), size)]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="从东方财富证券主数据构建全A股有效证券池，逐批测试批量行情接口（只读，不写数据库）"
    )
    parser.add_argument("--batch-size", type=int, default=100,
                        help="每批证券数量，范围1-500，默认100")
    parser.add_argument("--pause-seconds", type=float, default=0.3,
                        help="批次之间暂停秒数，默认0.3；不建议设为0")
    parser.add_argument("--max-age-seconds", type=int, default=180,
                        help="行情新鲜度阈值秒数，默认180")
    parser.add_argument("--limit", type=int, default=0,
                        help="可选：只测试前N只有效证券；0表示全量")
    args = parser.parse_args()

    if not 1 <= args.batch_size <= 500:
        parser.error("--batch-size必须在1到500之间")
    if args.pause_seconds < 0:
        parser.error("--pause-seconds不能小于0")
    if args.max_age_seconds < 0:
        parser.error("--max-age-seconds不能小于0")
    if args.limit < 0:
        parser.error("--limit不能小于0")

    started = time.monotonic()
    shanghai_tz = pytz.timezone("Asia/Shanghai")
    report: dict[str, object] = {
        "status": "failed",
        "source": "eastmoney_ulist",
        "security_master_source": "eastmoney_clist",
        "batch_size": args.batch_size,
        "pause_seconds": args.pause_seconds,
        "freshness_threshold_seconds": args.max_age_seconds,
        "database_written": False,
    }

    try:
        master_started = time.monotonic()
        all_securities = list(EastmoneySecurityMasterProvider().fetch_all())
        master_elapsed = round(time.monotonic() - master_started, 3)
        eligible = eligible_securities(all_securities)
        exchange_excluded = sum(1 for item in all_securities if item.exchange == "BJ")
        if args.limit:
            eligible = eligible[:args.limit]
        if not eligible:
            raise RuntimeError("证券主数据未产生可测试的有效证券")

        status_counts: dict[str, int] = {}
        for security in all_securities:
            status_counts[security.listing_status] = status_counts.get(security.listing_status, 0) + 1
        risk_excluded = sum(1 for item in all_securities if item.risk_flag)
        non_active_excluded = sum(1 for item in all_securities if item.listing_status != "active")
        report.update({
            "security_master_total": len(all_securities),
            "eligible_security_count": len(eligible),
            "risk_excluded_count": risk_excluded,
            "non_active_excluded_count": non_active_excluded,
            "exchange_excluded_count": exchange_excluded,
            "excluded_exchanges": ["BJ"],
            "security_listing_status_counts": status_counts,
            "security_master_elapsed_seconds": master_elapsed,
        })

        batches = chunks(eligible, args.batch_size)
        all_quotes: dict[str, QuoteSnapshot] = {}
        missing_codes: list[str] = []
        batch_reports: list[dict[str, object]] = []
        successful_batches = 0
        failed_batches = 0

        for index, batch in enumerate(batches, start=1):
            batch_started = time.monotonic()
            try:
                quotes = list(EastmoneyBatchQuoteProvider(batch_size=args.batch_size).fetch_quotes(batch))
                batch_collected_at = datetime.now(timezone.utc)
                batch_elapsed = round(time.monotonic() - batch_started, 3)
                batch_counts, _ = freshness_counts(quotes, batch_collected_at, args.max_age_seconds)
                requested_codes = {item.code for item in batch}
                returned_codes = {item.code for item in quotes}
                unexpected = sorted(returned_codes - requested_codes)
                if unexpected:
                    raise RuntimeError(f"行情源返回非请求代码: {unexpected[:10]}")
                for quote in quotes:
                    if quote.code in all_quotes:
                        raise RuntimeError(f"跨批次重复返回证券代码: {quote.code}")
                    all_quotes[quote.code] = quote
                batch_missing = sorted(requested_codes - returned_codes)
                missing_codes.extend(batch_missing)
                successful_batches += 1
                batch_reports.append({
                    "batch": index,
                    "status": "passed" if not batch_missing else "partial",
                    "requested_count": len(batch),
                    "returned_count": len(quotes),
                    "missing_count": len(batch_missing),
                    "coverage": round(len(quotes) / len(batch), 4),
                    "elapsed_seconds": batch_elapsed,
                    "freshness_counts": batch_counts,
                })
            except Exception as exc:
                failed_batches += 1
                batch_elapsed = round(time.monotonic() - batch_started, 3)
                missing_codes.extend(item.code for item in batch)
                batch_reports.append({
                    "batch": index,
                    "status": "failed",
                    "requested_count": len(batch),
                    "returned_count": 0,
                    "missing_count": len(batch),
                    "coverage": 0.0,
                    "elapsed_seconds": batch_elapsed,
                    "error": f"{type(exc).__name__}: {exc}"[:500],
                })
            if index < len(batches) and args.pause_seconds:
                time.sleep(args.pause_seconds)

        final_collected_at = datetime.now(timezone.utc)
        counts, quote_samples = freshness_counts(
            list(all_quotes.values()), final_collected_at, args.max_age_seconds
        )
        returned_count = len(all_quotes)
        requested_count = len(eligible)
        coverage = returned_count / requested_count if requested_count else 0.0
        missing_unique = sorted(set(missing_codes))
        f_status = freshness_status(counts)
        connectivity = "passed" if returned_count else "failed"
        overall_status = "passed" if coverage == 1.0 and f_status == "fresh" else "partial"

        latencies = [float(item["elapsed_seconds"]) for item in batch_reports]
        report.update({
            "status": overall_status,
            "connectivity_status": connectivity,
            "freshness_status": f_status,
            "freshness_counts": counts,
            "requested_count": requested_count,
            "returned_count": returned_count,
            "coverage": round(coverage, 4),
            "missing_count": len(missing_unique),
            "missing_codes_sample": missing_unique[:50],
            "batch_count": len(batches),
            "successful_batches": successful_batches,
            "failed_batches": failed_batches,
            "batch_elapsed_seconds": {
                "min": round(min(latencies), 3) if latencies else 0,
                "mean": round(mean(latencies), 3) if latencies else 0,
                "max": round(max(latencies), 3) if latencies else 0,
            },
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "collected_at": final_collected_at.astimezone(shanghai_tz).isoformat(timespec="seconds"),
            "sample_quotes": quote_samples[:10],
            "batches": batch_reports,
            "notes": [
                "本脚本只读取上游证券列表并请求行情，不写入market.db。",
                "首轮基准测试不自动重试，避免掩盖上游自然失败率；失败批次会继续后续批次。",
                "短线雷达候选池排除北交所（BJ），沪市和深市证券继续参与测试。",
                "当前证券列表解析器未取得独立停牌状态；停牌/无有效价格可能表现为缺失报价，不能仅凭缺失确认停牌。",
                "行情源为非官方公开接口；delayed_or_unverified仍按true处理，不代表交易所逐笔实时行情。",
            ],
        })
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if overall_status == "passed" else 2

    except Exception as exc:
        report.update({
            "error": f"{type(exc).__name__}: {exc}",
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "collected_at": datetime.now(timezone.utc).astimezone(shanghai_tz).isoformat(timespec="seconds"),
        })
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    sys.exit(main())
