"""Deterministic market-regime labels (CDC §13)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from trading_signal_bot.indicators import atr


@dataclass(frozen=True)
class RegimeThresholds:
    """Frozen after TRAIN (or calibration fold). Never fit on OOS."""

    vol_low: float
    vol_high: float
    trend_thresh: float = 0.5  # |slope|/ATR units or |pos-0.5| proxy

    def label(self, vol_ratio: float, trend_strength: float) -> str:
        if not np.isfinite(vol_ratio) or not np.isfinite(trend_strength):
            return "UNKNOWN"
        if vol_ratio < self.vol_low:
            vol = "LOW_VOL"
        elif vol_ratio > self.vol_high:
            vol = "HIGH_VOL"
        else:
            vol = "MID_VOL"
        trend = "TRENDING" if abs(trend_strength) >= self.trend_thresh else "RANGE"
        return f"{vol}|{trend}"


def fit_regime_thresholds_from_train(
    df_15m: pd.DataFrame,
    train_end_idx: int,
    atr_period: int = 14,
    w_vol: int = 100,
) -> RegimeThresholds:
    """Fit vol_ratio terciles on TRAIN bars only."""
    sub = df_15m.iloc[: train_end_idx + 1]
    atr_s = atr(sub, atr_period)
    ratios: list[float] = []
    for i in range(w_vol, len(sub)):
        ref = float(atr_s.iloc[i - w_vol : i + 1].median())
        now = float(atr_s.iloc[i])
        if ref > 0 and np.isfinite(now):
            ratios.append(now / ref)
    if len(ratios) < 3:
        return RegimeThresholds(vol_low=0.75, vol_high=1.25)
    lo, hi = np.percentile(ratios, [33.333, 66.666])
    return RegimeThresholds(vol_low=float(lo), vol_high=float(hi))


def compute_vol_ratio(
    df_15m: pd.DataFrame,
    asof_idx: int,
    atr_period: int = 14,
    w_vol: int = 100,
) -> float:
    sub = df_15m.iloc[: asof_idx + 1]
    if len(sub) <= w_vol:
        return float("nan")
    atr_s = atr(sub, atr_period)
    ref = float(atr_s.iloc[asof_idx - w_vol : asof_idx + 1].median())
    now = float(atr_s.iloc[asof_idx])
    if ref <= 0 or not np.isfinite(now):
        return float("nan")
    return now / ref


def compute_trend_strength_4h(
    df_4h: pd.DataFrame,
    asof_idx: int,
    atr_period: int = 14,
    lookback: int = 10,
) -> float:
    """Slope of closes over lookback, normalized by ATR(4H)."""
    sub = df_4h.iloc[: asof_idx + 1]
    if len(sub) < lookback + 1:
        return 0.0
    closes = sub["close"].to_numpy(dtype=float)
    y = closes[-(lookback + 1) :]
    x = np.arange(len(y), dtype=float)
    slope = float(np.polyfit(x, y, 1)[0])
    atr_s = atr(sub, atr_period)
    atr_v = float(atr_s.iloc[asof_idx])
    if not np.isfinite(atr_v) or atr_v <= 0:
        return 0.0
    return slope / atr_v


def label_regime_at(
    thresholds: RegimeThresholds,
    df_15m: pd.DataFrame,
    df_4h: pd.DataFrame,
    ts: pd.Timestamp,
    atr_period: int = 14,
    w_vol: int = 100,
) -> str:
    i15 = int(df_15m.index.searchsorted(ts, side="right") - 1)
    i4 = int(df_4h.index.searchsorted(ts, side="right") - 1)
    if i15 < 0 or i4 < 0:
        return "UNKNOWN"
    vol = compute_vol_ratio(df_15m, i15, atr_period, w_vol)
    trend = compute_trend_strength_4h(df_4h, i4, atr_period)
    return thresholds.label(vol, trend)
