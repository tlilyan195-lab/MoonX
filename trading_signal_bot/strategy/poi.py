"""POI detection: Fair Value Gaps and Order Blocks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

from trading_signal_bot.indicators import atr


POIType = Literal["FVG", "OB"]
MitigationMode = Literal["close_through", "touch", "fifty_percent_fill"]


@dataclass(frozen=True)
class POI:
    poi_id: str
    poi_type: POIType
    direction: Literal["BULL", "BEAR"]
    bottom: float
    top: float
    created_ts: pd.Timestamp
    created_index: int
    mitigated: bool = False

    @property
    def mid(self) -> float:
        return 0.5 * (self.bottom + self.top)

    def overlaps(self, other: POI) -> float:
        inter = max(0.0, min(self.top, other.top) - max(self.bottom, other.bottom))
        union = max(self.top, other.top) - min(self.bottom, other.bottom)
        if union <= 0:
            return 0.0
        return inter / union


def detect_fvgs(
    df: pd.DataFrame,
    atr_period: int,
    theta_fvg_atr: float,
    asof_index: int,
) -> list[POI]:
    """ICT/SMC 3-candle FVG with minimum ATR size filter."""
    sub = df.iloc[: asof_index + 1]
    if len(sub) < 3:
        return []
    atr_s = atr(sub, atr_period)
    pois: list[POI] = []
    for j in range(2, len(sub)):
        c0 = sub.iloc[j - 2]
        c2 = sub.iloc[j]
        atr_v = float(atr_s.iloc[j])
        if not np.isfinite(atr_v) or atr_v <= 0:
            continue
        # Bullish FVG: low[j] > high[j-2]
        if float(c2["low"]) > float(c0["high"]):
            gap = float(c2["low"]) - float(c0["high"])
            if gap >= theta_fvg_atr * atr_v:
                pois.append(
                    POI(
                        poi_id=f"FVG_BULL:{sub.index[j].isoformat()}",
                        poi_type="FVG",
                        direction="BULL",
                        bottom=float(c0["high"]),
                        top=float(c2["low"]),
                        created_ts=sub.index[j],
                        created_index=j,
                    )
                )
        # Bearish FVG: high[j] < low[j-2]
        if float(c2["high"]) < float(c0["low"]):
            gap = float(c0["low"]) - float(c2["high"])
            if gap >= theta_fvg_atr * atr_v:
                pois.append(
                    POI(
                        poi_id=f"FVG_BEAR:{sub.index[j].isoformat()}",
                        poi_type="FVG",
                        direction="BEAR",
                        bottom=float(c2["high"]),
                        top=float(c0["low"]),
                        created_ts=sub.index[j],
                        created_index=j,
                    )
                )
    return pois


def is_fvg_mitigated(
    poi: POI,
    df: pd.DataFrame,
    from_index_exclusive: int,
    to_index_inclusive: int,
    mode: MitigationMode,
) -> bool:
    if poi.poi_type != "FVG":
        return False
    height = poi.top - poi.bottom
    if height <= 0:
        return True
    for i in range(from_index_exclusive + 1, to_index_inclusive + 1):
        row = df.iloc[i]
        o, h, l, c = map(float, (row["open"], row["high"], row["low"], row["close"]))
        if mode == "close_through":
            if poi.direction == "BULL" and c < poi.bottom:
                return True
            if poi.direction == "BEAR" and c > poi.top:
                return True
        elif mode == "touch":
            if poi.direction == "BULL" and l <= poi.top:
                # any touch into/through gap
                if l <= poi.top and h >= poi.bottom:
                    return True
            if poi.direction == "BEAR" and h >= poi.bottom:
                if h >= poi.bottom and l <= poi.top:
                    return True
        elif mode == "fifty_percent_fill":
            mid = poi.mid
            if poi.direction == "BULL" and l <= mid:
                return True
            if poi.direction == "BEAR" and h >= mid:
                return True
        else:
            raise ValueError(f"Unknown mitigation mode: {mode}")
    return False


def bar_intersects_poi(row: pd.Series, poi: POI) -> bool:
    h = float(row["high"])
    l = float(row["low"])
    return l <= poi.top and h >= poi.bottom


def detect_order_blocks(
    df: pd.DataFrame,
    bos_indices_bull: list[int],
    bos_indices_bear: list[int],
    w_ob: int,
    geometry: str = "full_range",
) -> list[POI]:
    """
    OB = last opposite candle in W_ob before impulsive BOS bar.
    geometry: full_range | body_only
    """
    pois: list[POI] = []

    def _zone(row: pd.Series) -> tuple[float, float]:
        if geometry == "body_only":
            bottom = min(float(row["open"]), float(row["close"]))
            top = max(float(row["open"]), float(row["close"]))
        else:
            bottom = float(row["low"])
            top = float(row["high"])
        return bottom, top

    for bos_i in bos_indices_bull:
        start = max(0, bos_i - w_ob)
        ob_idx = None
        for i in range(bos_i - 1, start - 1, -1):
            row = df.iloc[i]
            if float(row["close"]) < float(row["open"]):  # bearish candle
                ob_idx = i
                break
        if ob_idx is None:
            continue
        bottom, top = _zone(df.iloc[ob_idx])
        pois.append(
            POI(
                poi_id=f"OB_BULL:{df.index[ob_idx].isoformat()}",
                poi_type="OB",
                direction="BULL",
                bottom=bottom,
                top=top,
                created_ts=df.index[ob_idx],
                created_index=ob_idx,
            )
        )

    for bos_i in bos_indices_bear:
        start = max(0, bos_i - w_ob)
        ob_idx = None
        for i in range(bos_i - 1, start - 1, -1):
            row = df.iloc[i]
            if float(row["close"]) > float(row["open"]):
                ob_idx = i
                break
        if ob_idx is None:
            continue
        bottom, top = _zone(df.iloc[ob_idx])
        pois.append(
            POI(
                poi_id=f"OB_BEAR:{df.index[ob_idx].isoformat()}",
                poi_type="OB",
                direction="BEAR",
                bottom=bottom,
                top=top,
                created_ts=df.index[ob_idx],
                created_index=ob_idx,
            )
        )
    return pois


def is_ob_invalidated(
    poi: POI,
    df: pd.DataFrame,
    from_index_exclusive: int,
    to_index_inclusive: int,
) -> bool:
    if poi.poi_type != "OB":
        return False
    for i in range(from_index_exclusive + 1, to_index_inclusive + 1):
        c = float(df.iloc[i]["close"])
        if poi.direction == "BULL" and c < poi.bottom:
            return True
        if poi.direction == "BEAR" and c > poi.top:
            return True
    return False
