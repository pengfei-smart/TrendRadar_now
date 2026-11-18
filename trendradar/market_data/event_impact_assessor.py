"""Four-dimensional, conservative event-impact strength assessment.

The assessor estimates event impact magnitude only. It does not infer direction,
source trust, industry relevance, transmission distance, or trading opportunity.
Missing dimensions are excluded from the weighted mean and reduce confidence.
"""
from __future__ import annotations

from dataclasses import dataclass
import math


_DIMENSION_WEIGHTS = (
    ("severity", 0.35),
    ("scope", 0.25),
    ("duration", 0.20),
    ("surprise", 0.20),
)


@dataclass(frozen=True)
class EventImpactInput:
    """Dimension ratings on a 0..1 scale; None means not assessed."""

    severity: float | None = None
    scope: float | None = None
    duration: float | None = None
    surprise: float | None = None


@dataclass(frozen=True)
class EventImpactAssessment:
    """Impact magnitude, weighted confidence and auditable dimension details."""

    event_strength: float | None
    assessment_confidence: float
    observed_dimensions: tuple[str, ...]
    missing_dimensions: tuple[str, ...]
    dimension_weights: tuple[tuple[str, float], ...]
    weighted_contributions: tuple[tuple[str, float], ...]
    explanation: tuple[str, ...]


def _validate_dimension(value: float | None, name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise TypeError(f"{name} must be a number or None, not bool")
    try:
        normalized = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise TypeError(f"{name} must be a number or None") from exc
    if not math.isfinite(normalized):
        raise ValueError(f"{name} must be finite")
    if not 0.0 <= normalized <= 1.0:
        raise ValueError(f"{name} must be between 0 and 1")
    return normalized


def assess_event_impact(item: EventImpactInput) -> EventImpactAssessment:
    """Calculate 0..1 impact strength without treating unknown as zero.

    The weighted mean is renormalized over observed dimensions only. Confidence
    is the sum of the original weights for observed dimensions, so a score based
    on partial information is visibly less certain.
    """
    if not isinstance(item, EventImpactInput):
        raise TypeError("item must be an EventImpactInput")

    values = {
        "severity": _validate_dimension(item.severity, "severity"),
        "scope": _validate_dimension(item.scope, "scope"),
        "duration": _validate_dimension(item.duration, "duration"),
        "surprise": _validate_dimension(item.surprise, "surprise"),
    }
    observed = tuple(name for name, _ in _DIMENSION_WEIGHTS if values[name] is not None)
    missing = tuple(name for name, _ in _DIMENSION_WEIGHTS if values[name] is None)
    observed_weight = sum(
        weight for name, weight in _DIMENSION_WEIGHTS if values[name] is not None
    )

    if observed_weight == 0.0:
        return EventImpactAssessment(
            event_strength=None,
            assessment_confidence=0.0,
            observed_dimensions=(),
            missing_dimensions=missing,
            dimension_weights=_DIMENSION_WEIGHTS,
            weighted_contributions=(),
            explanation=("四个影响维度均未评估，事件强度暂不可计算",),
        )

    contributions = tuple(
        (name, values[name] * weight / observed_weight)
        for name, weight in _DIMENSION_WEIGHTS
        if values[name] is not None
    )
    strength = sum(
        values[name] * weight
        for name, weight in _DIMENSION_WEIGHTS
        if values[name] is not None
    ) / observed_weight

    explanation = [
        f"已评估维度：{', '.join(observed)}",
        f"缺失维度：{', '.join(missing) if missing else '无'}",
        "事件强度仅衡量影响幅度，不代表利好或利空",
        "来源可信度、行业相关性和产业链传导不参与本次计算",
    ]
    if missing:
        explanation.append("存在未评估维度，强度分按已评估维度归一化，置信度相应降低")

    return EventImpactAssessment(
        event_strength=max(0.0, min(1.0, strength)),
        assessment_confidence=observed_weight,
        observed_dimensions=observed,
        missing_dimensions=missing,
        dimension_weights=_DIMENSION_WEIGHTS,
        weighted_contributions=contributions,
        explanation=tuple(explanation),
    )
