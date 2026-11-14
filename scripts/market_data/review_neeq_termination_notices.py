# 本脚本读取NEEQ附件清单、下载状态和提取文本，生成只读公告解析及交叉核验报告。
"""CLI for read-only parsing and cross-checking of NEEQ termination notices."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trendradar.market_data.neeq_termination_notice_parser import (
    build_neeq_notice_review_report,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="解析NEEQ终止挂牌公告并与结果名录候选核验；只输出报告，不写数据库。"
    )
    parser.add_argument("--manifest", required=True, help="neeq_attachment_manifest.json")
    parser.add_argument("--state", required=True, help="neeq_attachment_download_state.json")
    parser.add_argument("--text-dir", default="output/market/neeq_evidence")
    parser.add_argument("--output", default="", help="输出JSON报告；留空打印到标准输出")
    args = parser.parse_args()

    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8-sig"))
    state = json.loads(Path(args.state).read_text(encoding="utf-8-sig"))
    text_dir = Path(args.text_dir)

    def load_text(filename: str) -> str:
        path = text_dir / Path(filename).name
        if not path.is_file():
            raise FileNotFoundError(f"提取文本文件不存在: {path.name}")
        return path.read_text(encoding="utf-8")

    report = build_neeq_notice_review_report(manifest, state, load_text)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        destination = Path(args.output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
