# 本脚本从NEEQ候选清单生成按附件URL分组的只读任务文件，不发起网络请求或数据库写入。
"""Create a URL-deduplicated NEEQ attachment worklist without downloading files."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trendradar.market_data.neeq_attachment_manifest import build_neeq_attachment_manifest


def main() -> int:
    parser = argparse.ArgumentParser(
        description="从候选清单生成NEEQ附件分组任务；不下载附件、不写数据库。"
    )
    parser.add_argument("--input", required=True, help="neeq_termination_candidate_manifest.json")
    parser.add_argument("--output", required=True, help="输出附件任务清单JSON")
    args = parser.parse_args()

    source_path = Path(args.input)
    payload = json.loads(source_path.read_text(encoding="utf-8-sig"))
    manifest = payload.get("manifest", payload)
    candidates = manifest.get("candidates")
    if not isinstance(candidates, list):
        parser.error("输入JSON中缺少 manifest.candidates 或 candidates 数组")

    result = build_neeq_attachment_manifest(candidates)
    result["input_file"] = str(source_path)
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
