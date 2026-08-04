"""Setup scoring (placeholder weights until TRAIN/VAL calibration)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ScoreBreakdown:
    score: float
    features: dict[str, float]
    category: str


def compute_score(
    features: dict[str, float],
    weights: dict[str, float],
    s_b: float,
    s_a: float,
    s_aplus: float,
    require_overlap_for_aplus: bool,
) -> ScoreBreakdown:
    """
    Weighted sum of features in [0,1], scaled to 0-100.
    Missing weight keys ignored; missing features treated as 0.
    """
    if not weights:
        raw = 0.0
    else:
        num = 0.0
        den = 0.0
        for k, w in weights.items():
            w = float(w)
            if w <= 0:
                continue
            num += w * float(features.get(k, 0.0))
            den += w
        raw = (num / den) if den > 0 else 0.0
    score = 100.0 * raw

    overlap = float(features.get("f_overlap_fvg_ob", 0.0)) >= 1.0
    if score >= s_aplus and (overlap or not require_overlap_for_aplus):
        category = "A+"
    elif score >= s_a:
        category = "A"
    elif score >= s_b:
        category = "B"
    else:
        category = "NO_TRADE"
    return ScoreBreakdown(score=score, features=dict(features), category=category)


def explanation_from_flags(flags: dict[str, Any]) -> str:
    parts = [k for k, v in flags.items() if v is True]
    if not parts:
        return "Aucune condition bonus; gates hard uniquement."
    return "Setup détecté car: " + ", ".join(parts) + "."
