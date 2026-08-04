"""Chronological splits, walk-forward, and anti-overfit helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class SplitWindow:
    name: str
    start: pd.Timestamp
    end: pd.Timestamp


def chronological_splits(
    index: pd.DatetimeIndex,
    train_fraction: float = 0.60,
    val_fraction: float = 0.20,
    oos_fraction: float = 0.20,
) -> dict[str, SplitWindow]:
    if abs(train_fraction + val_fraction + oos_fraction - 1.0) > 1e-6:
        raise ValueError("split fractions must sum to 1")
    if len(index) < 10:
        raise ValueError("not enough bars to split")
    n = len(index)
    i_train = int(n * train_fraction)
    i_val = i_train + int(n * val_fraction)
    i_train = max(i_train, 1)
    i_val = min(max(i_val, i_train + 1), n - 1)
    return {
        "TRAIN": SplitWindow("TRAIN", index[0], index[i_train - 1]),
        "VAL": SplitWindow("VAL", index[i_train], index[i_val - 1]),
        "OOS": SplitWindow("OOS", index[i_val], index[-1]),
    }


def mask_split(index: pd.DatetimeIndex, window: SplitWindow) -> np.ndarray:
    return (index >= window.start) & (index <= window.end)


def walk_forward_windows(
    index: pd.DatetimeIndex,
    train_bars: int,
    val_bars: int,
    step_bars: int,
) -> Iterator[tuple[SplitWindow, SplitWindow]]:
    """Yield (train, val) windows; never includes final OOS reserved externally."""
    n = len(index)
    start = 0
    while start + train_bars + val_bars <= n:
        tr = SplitWindow(
            "TRAIN_WF",
            index[start],
            index[start + train_bars - 1],
        )
        va = SplitWindow(
            "VAL_WF",
            index[start + train_bars],
            index[start + train_bars + val_bars - 1],
        )
        yield tr, va
        start += step_bars


def monte_carlo_expectancy(
    pnls: list[float],
    n_sims: int = 1000,
    seed: int = 42,
) -> dict[str, float]:
    """Bootstrap trade returns (no peeking at unused params)."""
    if not pnls:
        return {"mean": 0.0, "p05": 0.0, "p50": 0.0, "p95": 0.0, "p_exp_le_0": 1.0}
    rng = np.random.default_rng(seed)
    arr = np.asarray(pnls, dtype=float)
    means = []
    for _ in range(n_sims):
        sample = rng.choice(arr, size=len(arr), replace=True)
        means.append(float(sample.mean()))
    means_a = np.asarray(means)
    return {
        "mean": float(means_a.mean()),
        "p05": float(np.percentile(means_a, 5)),
        "p50": float(np.percentile(means_a, 50)),
        "p95": float(np.percentile(means_a, 95)),
        "p_exp_le_0": float(np.mean(means_a <= 0)),
    }


def overfitting_flags(
    train_exp: float,
    val_exp: float,
    oos_exp: float | None = None,
    train_n: int = 0,
    val_n: int = 0,
    n_trials: int = 1,
) -> list[str]:
    flags: list[str] = []
    if train_exp > 0 and val_exp <= 0:
        flags.append("sign_flip_train_val")
    if train_exp > 0 and val_exp > 0 and train_exp > 3 * val_exp:
        flags.append("train_much_stronger_than_val")
    if oos_exp is not None and val_exp > 0 and oos_exp <= 0:
        flags.append("oos_breakdown")
    if train_n < 30 or val_n < 15:
        flags.append("too_few_signals")
    if n_trials > 50:
        flags.append("high_hyperparam_trial_count")
    return flags
