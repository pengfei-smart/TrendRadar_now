# 本文件定义 A 股证券生命周期历史资料的官方来源登记表与域名白名单。
"""Registry of authoritative public sources for Chinese security lifecycle evidence."""

from dataclasses import dataclass


@dataclass(frozen=True)
class LifecycleSource:
    source_id: str
    name: str
    base_url: str
    allowed_hosts: tuple[str, ...]
    source_type: str
    priority: int
    description: str


SOURCES: tuple[LifecycleSource, ...] = (
    LifecycleSource(
        source_id="sse",
        name="上海证券交易所",
        base_url="https://www.sse.com.cn/",
        allowed_hosts=("sse.com.cn", "www.sse.com.cn"),
        source_type="exchange",
        priority=1,
        description="沪市上市、暂停上市、恢复上市、终止上市及公司行动公告",
    ),
    LifecycleSource(
        source_id="szse",
        name="深圳证券交易所",
        base_url="https://www.szse.cn/",
        allowed_hosts=("szse.cn", "www.szse.cn"),
        source_type="exchange",
        priority=1,
        description="深市主板、创业板上市状态及公司行动公告",
    ),
    LifecycleSource(
        source_id="csrc",
        name="中国证券监督管理委员会",
        base_url="https://www.csrc.gov.cn/",
        allowed_hosts=("csrc.gov.cn", "www.csrc.gov.cn"),
        source_type="regulator",
        priority=1,
        description="行政决定、监管文件及重大资产重组相关公告",
    ),
    LifecycleSource(
        source_id="neeq",
        name="全国中小企业股份转让系统",
        base_url="https://www.neeq.com.cn/",
        allowed_hosts=("neeq.com.cn", "www.neeq.com.cn"),
        source_type="transfer_market",
        priority=2,
        description="两网及退市板块、挂牌和转让相关公开资料",
    ),
)

_SOURCE_BY_ID = {source.source_id: source for source in SOURCES}


def get_source(source_id: str) -> LifecycleSource:
    """Return one configured source; unknown IDs fail closed."""
    try:
        return _SOURCE_BY_ID[source_id]
    except KeyError as exc:
        raise ValueError(f"未知生命周期资料来源: {source_id}") from exc


def list_sources() -> tuple[LifecycleSource, ...]:
    """Return sources in configured priority order."""
    return tuple(sorted(SOURCES, key=lambda source: (source.priority, source.source_id)))


def is_allowed_url(source_id: str, url: str) -> bool:
    """Allow HTTPS URLs only on the source's explicitly registered hosts."""
    from urllib.parse import urlparse

    source = get_source(source_id)
    parsed = urlparse(url)
    return (
        parsed.scheme == "https"
        and parsed.hostname is not None
        and parsed.hostname.lower() in source.allowed_hosts
        and parsed.username is None
        and parsed.password is None
    )
