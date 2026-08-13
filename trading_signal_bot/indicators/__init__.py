"""Technical indicators (ATR, pivots)."""

from __future__ import annotations

import numpy as np
import pandas as pd


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    ranges = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    )
    return ranges.max(axis=1)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder ATR."""
    tr = true_range(df)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def pivot_highs(high: np.ndarray | pd.Series, n: int) -> np.ndarray:
    """
    Boolean mask: True at confirmed pivot highs.
    Confirmation requires n bars on the right => look-ahead of n bars.
    When evaluating at time t, only use pivots with index i where i+n <= t_index.
    """
    h = np.asarray(high, dtype=float)
    out = np.zeros(len(h), dtype=bool)
    if n < 1 or len(h) < 2 * n + 1:
        return out
    for i in range(n, len(h) - n):
        left = h[i - n : i]
        right = h[i + 1 : i + n + 1]
        if h[i] > left.max() and h[i] > right.max():
            out[i] = True
    return out


def pivot_lows(low: np.ndarray | pd.Series, n: int) -> np.ndarray:
    l = np.asarray(low, dtype=float)
    out = np.zeros(len(l), dtype=bool)
    if n < 1 or len(l) < 2 * n + 1:
        return out
    for i in range(n, len(l) - n):
        left = l[i - n : i]
        right = l[i + 1 : i + n + 1]
        if l[i] < left.min() and l[i] < right.min():
            out[i] = True
    return out


def last_confirmed_pivot(
    values: np.ndarray,
    pivot_mask: np.ndarray,
    asof_index: int,
    n: int,
) -> tuple[int | None, float | None]:
    """
    Last pivot confirmed by asof_index (pivot at i confirmed when asof_index >= i+n).
    """
    confirmed_upto = asof_index - n
    if confirmed_upto < 0:
        return None, None
    idxs = np.where(pivot_mask[: confirmed_upto + 1])[0]
    if len(idxs) == 0:
        return None, None
    i = int(idxs[-1])
    return i, float(values[i])
