# 本脚本提供NEEQ生命周期候选的命令行查看与人工审核入口，不写正式事实表。
"""CLI for reviewing NEEQ identity and lifecycle evidence candidates."""

from trendradar.market_data.neeq_lifecycle_review import main


if __name__ == "__main__":
    raise SystemExit(main())
