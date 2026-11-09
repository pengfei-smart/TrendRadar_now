# 本文件定义TrendRadar包的公开入口，并延迟加载完整应用上下文，避免导入独立子模块时触发全部应用依赖。
"""TrendRadar - 热点新闻聚合与分析工具.

使用方式:
  python -m trendradar        # 模块执行
  trendradar                  # 安装后执行
"""

__version__ = "6.5.0"
__all__ = ["AppContext", "__version__"]


def __getattr__(name: str):
    """Load the full application context only when callers explicitly request it."""
    if name == "AppContext":
        from trendradar.context import AppContext
        return AppContext
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
