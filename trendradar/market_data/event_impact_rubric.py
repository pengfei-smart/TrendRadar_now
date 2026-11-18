"""Versioned human-readable rubric for four-dimensional event impact assessment.

This module defines rating anchors and evidence guidance only. It does not
assign scores to events, call an AI model, or change the impact calculation.
"""
from __future__ import annotations

from dataclasses import dataclass


EVENT_IMPACT_RUBRIC_VERSION = "event-impact-rubric-v1"


@dataclass(frozen=True)
class ImpactScoreAnchor:
    """Reference point on the continuous 0..1 rating scale."""

    score: float
    label: str
    description: str


@dataclass(frozen=True)
class ImpactDimensionRubric:
    """Scoring policy and evidence requirements for one impact dimension."""

    dimension: str
    weight: float
    purpose: str
    anchors: tuple[ImpactScoreAnchor, ...]
    evidence_requirements: tuple[str, ...]
    cautions: tuple[str, ...]


_RUBRIC = (
    ImpactDimensionRubric(
        dimension="severity",
        weight=0.35,
        purpose="衡量事件本身造成的实际或可合理论证的影响程度，不判断利好或利空方向。",
        anchors=(
            ImpactScoreAnchor(0.00, "无实质影响", "一般信息或没有可识别的实质影响。"),
            ImpactScoreAnchor(0.25, "轻微影响", "局部、有限或影响较小的变化。"),
            ImpactScoreAnchor(0.50, "显著影响", "对相关企业、细分行业或局部供需产生明显影响。"),
            ImpactScoreAnchor(0.75, "重大影响", "影响重要行业、关键供应链环节或重要宏观政策环境。"),
            ImpactScoreAnchor(1.00, "系统性重大影响", "可能显著改变关键行业格局或宏观经济环境。"),
        ),
        evidence_requirements=(
            "指出已发生的事实、正式措施或可核验的事件结果。",
            "说明影响机制及受影响对象，区分事实与推断。",
        ),
        cautions=(
            "不得仅凭标题措辞、媒体形容词或市场涨跌幅提高评分。",
            "尚未落地的计划、传闻和情景推演必须明确标注。",
        ),
    ),
    ImpactDimensionRubric(
        dimension="scope",
        weight=0.25,
        purpose="衡量事件客观涉及的主体、地区和行业范围，不等同于对某个A股行业的相关性。",
        anchors=(
            ImpactScoreAnchor(0.00, "单一主体", "仅涉及单一主体，暂无可证实的外溢影响。"),
            ImpactScoreAnchor(0.25, "局部范围", "涉及少数企业或局部地区。"),
            ImpactScoreAnchor(0.50, "行业或多地区", "涉及一个行业或多个地区。"),
            ImpactScoreAnchor(0.75, "跨行业重要范围", "涉及多个重要行业或较广泛经济区域。"),
            ImpactScoreAnchor(1.00, "全球或广泛跨领域", "跨地区、跨行业或具有全球性影响范围。"),
        ),
        evidence_requirements=(
            "列明已确认涉及的企业、地区、行业或统计覆盖范围。",
            "区分直接涉及范围与可能的间接传导范围。",
        ),
        cautions=(
            "不得把潜在传导范围直接写成已经受到影响的范围。",
            "不得因为事件与A股相关就扩大事件客观范围。",
        ),
    ),
    ImpactDimensionRubric(
        dimension="duration",
        weight=0.20,
        purpose="衡量事件影响预计持续的时间尺度，而非股价走势持续时间。",
        anchors=(
            ImpactScoreAnchor(0.00, "短暂或无持续影响", "影响短暂，或没有证据表明存在持续影响。"),
            ImpactScoreAnchor(0.25, "短期", "影响预计在数日内明显消退。"),
            ImpactScoreAnchor(0.50, "中期", "影响可能持续数周。"),
            ImpactScoreAnchor(0.75, "较长期", "影响可能持续数月。"),
            ImpactScoreAnchor(1.00, "结构性长期", "影响具有结构性，可能持续一年以上。"),
        ),
        evidence_requirements=(
            "优先引用政策有效期、合同期限、项目周期、库存或供需数据等依据。",
            "说明持续时间是已知期限还是分析推断。",
        ),
        cautions=(
            "不得把股价上涨持续时间当作事件影响持续时间。",
            "没有时间依据时应返回未评估，不得凭感觉补分。",
        ),
    ),
    ImpactDimensionRubric(
        dimension="surprise",
        weight=0.20,
        purpose="衡量事件结果相对事件发生前可获得预期基准的偏离程度。",
        anchors=(
            ImpactScoreAnchor(0.00, "完全符合预期", "结果已被充分预期，未带来实质新增信息。"),
            ImpactScoreAnchor(0.25, "略超预期", "相对事前基准存在轻微正向偏差。"),
            ImpactScoreAnchor(0.50, "明显超预期", "相对可靠事前基准存在清晰偏差。"),
            ImpactScoreAnchor(0.75, "大幅超预期", "偏差显著，可能改变市场原有判断基准。"),
            ImpactScoreAnchor(1.00, "极端意外", "事前几乎未被预期，且结果与主流预期存在重大偏离。"),
        ),
        evidence_requirements=(
            "必须提供事件发生前形成的预期基准及其时间戳、来源或可追溯记录。",
            "尽可能比较实际结果与一致预期、官方指引或事前市场定价。",
        ),
        cautions=(
            "缺少可靠事前预期基准时必须返回 None，不得主观猜测。",
            "新闻措辞强烈、来源权威或结果重大，不等于超预期。",
        ),
    ),
)


def get_event_impact_rubric() -> tuple[ImpactDimensionRubric, ...]:
    """Return the immutable V1 rubric in the assessor's calculation order."""
    return _RUBRIC


def get_dimension_rubric(dimension: str) -> ImpactDimensionRubric:
    """Return one dimension's rubric; reject unknown dimension names."""
    if not isinstance(dimension, str):
        raise TypeError("dimension must be a string")
    normalized = dimension.strip().casefold()
    for item in _RUBRIC:
        if item.dimension == normalized:
            return item
    raise ValueError(f"unknown event impact dimension: {dimension!r}")
