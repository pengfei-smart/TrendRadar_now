# 本脚本分批下载NEEQ官方终止挂牌附件并提取文本，默认只预览任务，不写数据库或确认法律事实。
"""CLI for a small, resumable first batch of NEEQ evidence downloads."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trendradar.market_data.neeq_attachment_downloader import download_neeq_attachments
from trendradar.market_data.lifecycle_source_registry import is_allowed_url


def main() -> int:
    parser = argparse.ArgumentParser(
        description="分批下载NEEQ官方附件并提取文本；默认 dry-run，不写数据库。"
    )
    parser.add_argument("--input", required=True, help="neeq_attachment_manifest.json")
    parser.add_argument("--output-dir", default="output/market/neeq_evidence")
    parser.add_argument("--limit", type=int, default=5, help="本批最多处理任务数，默认5")
    parser.add_argument("--offset", type=int, default=0, help="从第几个任务开始（从0计数），用于分批推进")
    parser.add_argument("--delay-seconds", type=float, default=1.0)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--download", action="store_true", help="明确开启实际下载；不指定时只预览")
    args = parser.parse_args()
    if args.limit < 1 or args.limit > 259:
        parser.error("--limit 必须在1到259之间")
    if args.offset < 0 or args.offset >= 259:
        parser.error("--offset 必须在0到258之间")
    if args.delay_seconds < 0:
        parser.error("--delay-seconds 不能小于0")
    if not 0 <= args.retries <= 5:
        parser.error("--retries 必须在0到5之间")

    payload = json.loads(Path(args.input).read_text(encoding="utf-8-sig"))
    tasks = payload.get("tasks")
    if not isinstance(tasks, list):
        parser.error("输入清单缺少 tasks 数组")
    selected = tasks[args.offset:args.offset + args.limit]
    for task in selected:
        if not is_allowed_url("neeq", str(task.get("url") or "")):
            parser.error("所选任务包含不在NEEQ官方HTTPS白名单内的URL")

    if not args.download:
        print(json.dumps({
            "mode": "dry_run",
            "manifest_task_count": len(tasks),
            "selected_task_count": len(selected),
            "offset": args.offset,
            "output_dir": args.output_dir,
            "tasks": [{
                "attachment_id": task.get("attachment_id"),
                "url": task.get("url"),
                "attachment_kinds_reported": task.get("attachment_kinds_reported"),
                "linked_candidate_count": task.get("linked_candidate_count"),
            } for task in selected],
            "network_downloads": 0,
            "database_writes": 0,
        }, ensure_ascii=False, indent=2))
        return 0

    result = download_neeq_attachments(
        payload, args.output_dir, limit=args.limit, offset=args.offset,
        delay_seconds=args.delay_seconds, retries=args.retries,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
