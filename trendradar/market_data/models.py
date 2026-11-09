# 本文件定义市场事件、证券、行情快照及映射证据的数据结构，属于逐文件开发清单第八项。
"""Typed data models for the market opportunity radar."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class MarketEvent:
    event_id: str
    source: str
    title: str
    published_at: str
    event_type: str = "unknown"
    direction: str = "neutral"
    region: str = "US"
    url: str = ""
    summary: str = ""


@dataclass(frozen=True)
class Security:
    code: str
    name: str
    exchange: str = ""
    listing_status: str = "active"
    risk_flag: bool = False
    sectors: tuple[str, ...] = ()


@dataclass(frozen=True)
class QuoteSnapshot:
    code: str
    timestamp: str
    price: Optional[float] = None
    change_pct: Optional[float] = None
    turnover: Optional[float] = None
    volume_ratio: Optional[float] = None
    source: str = ""
    delayed: bool = True


@dataclass(frozen=True)
class MappingEvidence:
    target_code: str
    mapping_type: str
    confidence: float
    evidence: str
    source: str = ""
    source_url: str = ""
    direction: str = "neutral"
    verified: bool = False


@dataclass
class Candidate:
    security: Security
    tier: str = "C"
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    evidence: list[MappingEvidence] = field(default_factory=list)
    quote: Optional[QuoteSnapshot] = None
    causal_status: str = "unverified"
