# 本脚本对 NEEQ 官方公告分类树执行一次受限 POST 探测，并保存原始响应及哈希元数据。
"""Probe the official NEEQ category tree once and save its raw response."""

from __future__ import annotations

import json
from pathlib import Path

from trendradar.market_data.neeq_lifecycle_collector import extract_neeq_categories
from trendradar.market_data.neeq_runtime_probe import probe_neeq_category_tree


def main() -> int:
    try:
        result = probe_neeq_category_tree()
    except Exception as exc:
        print(json.dumps({
            "status": "failed",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }, ensure_ascii=False, indent=2))
        return 1

    categories = extract_neeq_categories(result["payload"])
    report = {
        **result,
        "categories": [
            {
                "title": item.title,
                "datebase_number": item.datebase_number,
                "business_number": item.business_number,
                "level": item.level,
                "parent_title": item.parent_title,
                "search_key": item.search_key,
            }
            for item in categories
        ],
        "category_count": len(categories),
    }
    project_root = Path(__file__).resolve().parents[2]
    output_path = project_root / "output" / "market" / "neeq_category_tree_probe.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({
        "status": "passed",
        "source_id": result["source_id"],
        "http_method": result["http_method"],
        "status_code": result["status_code"],
        "runtime_verified": result["runtime_verified"],
        "request_count": result["request_count"],
        "content_sha256": result["content_sha256"],
        "category_count": len(categories),
        "output_file": str(output_path),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
