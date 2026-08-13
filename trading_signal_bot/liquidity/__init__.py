"""Liquidity levels and sweep detection."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal

import numpy as np
import pandas as pd

from trading_signal_bot.data.sessions import (
    previous_crypto_utc_day_bounds,
    previous_fx_trading_day_bounds,
    previous_fx_week_bounds,
)
from trading_signal_bot.indicators import atr, pivot_highs, pivot_lows


class LiqRank(str, Enum):
    L1_PD = "L1_PD"
    L2_PW = "L2_PW"
    L3_SESSION = "L3_SESSION"
    L4_EQUAL = "L4_EQUAL"
    L5_SWING = "L5_SWING"


@dataclass(frozen=True)
class LiquidityLevel:
    level_id: str
    price: float
    kind: Literal["HIGH", "LOW"]
    rank: LiqRank
    ts_ref: pd.Timestamp
    major: bool = False


@dataclass(frozen=True)
class SweepEvent:
    ts: pd.Timestamp
    index: int
    direction_taken: Literal["HIGH", "LOW"]  # HIGH swept => bearish setup bias
    level: LiquidityLevel
    extreme: float


def _pdh_pdl(
    df: pd.DataFrame,
    ts: pd.Timestamp,
    asset_class: str,
) -> list[LiquidityLevel]:
    if asset_class in ("FX", "XAU"):
        start, end = previous_fx_trading_day_bounds(ts)
    else:
        start, end = previous_crypto_utc_day_bounds(ts)
    window = df.loc[(df.index >= start) & (df.index < end)]
    if window.empty:
        return []
    levels = [
        LiquidityLevel(
            level_id=f"PDH:{start.isoformat()}",
            price=float(window["high"].max()),
            kind="HIGH",
            rank=LiqRank.L1_PD,
            ts_ref=start,
            major=True,
        ),
        LiquidityLevel(
            level_id=f"PDL:{start.isoformat()}",
            price=float(window["low"].min()),
            kind="LOW",
            rank=LiqRank.L1_PD,
            ts_ref=start,
            major=True,
        ),
    ]
    return levels


def _pwh_pwl_fx(df: pd.DataFrame, ts: pd.Timestamp) -> list[LiquidityLevel]:
    start, end = previous_fx_week_bounds(ts)
    window = df.loc[(df.index >= start) & (df.index < end)]
    if window.empty:
        return []
    return [
        LiquidityLevel(
            f"PWH:{start.isoformat()}",
            float(window["high"].max()),
            "HIGH",
            LiqRank.L2_PW,
            start,
            True,
        ),
        LiquidityLevel(
            f"PWL:{start.isoformat()}",
            float(window["low"].min()),
            "LOW",
            LiqRank.L2_PW,
            start,
            True,
        ),
    ]


def equal_highs_lows(
    df: pd.DataFrame,
    n_pivot: int,
    epsilon_atr: float,
    atr_period: int,
    min_separation: int,
    asof_index: int,
) -> list[LiquidityLevel]:
    sub = df.iloc[: asof_index + 1]
    if len(sub) < 2 * n_pivot + 2:
        return []
    high = sub["high"].to_numpy(dtype=float)
    low = sub["low"].to_numpy(dtype=float)
    ph = pivot_highs(high, n_pivot)
    pl = pivot_lows(low, n_pivot)
    atr_s = atr(sub, atr_period)
    atr_v = float(atr_s.iloc[asof_index]) if np.isfinite(atr_s.iloc[asof_index]) else np.nan
    if not np.isfinite(atr_v) or atr_v <= 0:
        return []
    eps = epsilon_atr * atr_v
    levels: list[LiquidityLevel] = []

    ph_idx = np.where(ph)[0]
    # only confirmed pivots: i+n <= asof
    ph_idx = ph_idx[ph_idx + n_pivot <= asof_index]
    for a in range(len(ph_idx)):
        for b in range(a + 1, len(ph_idx)):
            i, j = int(ph_idx[a]), int(ph_idx[b])
            if j - i < min_separation:
                continue
            if abs(high[i] - high[j]) <= eps:
                price = (high[i] + high[j]) / 2.0
                levels.append(
                    LiquidityLevel(
                        f"EQH:{sub.index[i].isoformat()}:{sub.index[j].isoformat()}",
                        price,
                        "HIGH",
                        LiqRank.L4_EQUAL,
                        sub.index[j],
                        major=True,
                    )
                )

    pl_idx = np.where(pl)[0]
    pl_idx = pl_idx[pl_idx + n_pivot <= asof_index]
    for a in range(len(pl_idx)):
        for b in range(a + 1, len(pl_idx)):
            i, j = int(pl_idx[a]), int(pl_idx[b])
            if j - i < min_separation:
                continue
            if abs(low[i] - low[j]) <= eps:
                price = (low[i] + low[j]) / 2.0
                levels.append(
                    LiquidityLevel(
                        f"EQL:{sub.index[i].isoformat()}:{sub.index[j].isoformat()}",
                        price,
                        "LOW",
                        LiqRank.L4_EQUAL,
                        sub.index[j],
                        major=True,
                    )
                )
    return levels


def build_liquidity_levels(
    df_15m: pd.DataFrame,
    df_1h: pd.DataFrame,
    ts: pd.Timestamp,
    asset_class: str,
    n_pivot_1h: int,
    epsilon_eq_atr: float,
    atr_period: int,
    min_separation: int,
    asof_15m: int,
    asof_1h: int,
) -> list[LiquidityLevel]:
    levels: list[LiquidityLevel] = []
    levels.extend(_pdh_pdl(df_15m, ts, asset_class))
    if asset_class in ("FX", "XAU"):
        levels.extend(_pwh_pwl_fx(df_15m, ts))
    levels.extend(
        equal_highs_lows(
            df_1h,
            n_pivot_1h,
            epsilon_eq_atr,
            atr_period,
            min_separation,
            asof_1h,
        )
    )
    # L5: last confirmed 1H swings
    high = df_1h["high"].to_numpy(dtype=float)
    low = df_1h["low"].to_numpy(dtype=float)
    ph = pivot_highs(high, n_pivot_1h)
    pl = pivot_lows(low, n_pivot_1h)
    from trading_signal_bot.indicators import last_confirmed_pivot

    shi, shv = last_confirmed_pivot(high, ph, asof_1h, n_pivot_1h)
    sli, slv = last_confirmed_pivot(low, pl, asof_1h, n_pivot_1h)
    if shv is not None and shi is not None:
        levels.append(
            LiquidityLevel(
                f"SWING_H_1H:{df_1h.index[shi].isoformat()}",
                shv,
                "HIGH",
                LiqRank.L5_SWING,
                df_1h.index[shi],
                major=False,
            )
        )
    if slv is not None and sli is not None:
        levels.append(
            LiquidityLevel(
                f"SWING_L_1H:{df_1h.index[sli].isoformat()}",
                slv,
                "LOW",
                LiqRank.L5_SWING,
                df_1h.index[sli],
                major=False,
            )
        )
    # Deduplicate by level_id
    uniq = {lv.level_id: lv for lv in levels}
    return list(uniq.values())


def detect_sweep_on_bar(
    bar: pd.Series,
    ts: pd.Timestamp,
    index: int,
    levels: list[LiquidityLevel],
) -> list[SweepEvent]:
    """Sweep = wick beyond level + close back on original side."""
    events: list[SweepEvent] = []
    high = float(bar["high"])
    low = float(bar["low"])
    close = float(bar["close"])
    for lv in levels:
        if lv.kind == "HIGH":
            if high > lv.price and close < lv.price:
                events.append(SweepEvent(ts, index, "HIGH", lv, high))
        else:
            if low < lv.price and close > lv.price:
                events.append(SweepEvent(ts, index, "LOW", lv, low))
    return events


def opposite_liquidity_targets(
    levels: list[LiquidityLevel],
    direction: Literal["LONG", "SHORT"],
    entry: float,
    min_distance: float,
) -> list[LiquidityLevel]:
    """Order liquidity targets in trade direction by distance ascending."""
    out: list[LiquidityLevel] = []
    for lv in levels:
        if direction == "LONG" and lv.kind == "HIGH" and lv.price >= entry + min_distance:
            out.append(lv)
        if direction == "SHORT" and lv.kind == "LOW" and lv.price <= entry - min_distance:
            out.append(lv)
    out.sort(key=lambda x: abs(x.price - entry))
    return out
