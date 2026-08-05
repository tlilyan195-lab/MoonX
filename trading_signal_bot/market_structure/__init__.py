"""Market structure: swings, BOS, CHoCH, bias."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal

import numpy as np
import pandas as pd

from trading_signal_bot.indicators import atr, last_confirmed_pivot, pivot_highs, pivot_lows


Bias = Literal["BULL", "BEAR", "NEUTRAL"]


class StructureEventType(str, Enum):
    BOS_BULL = "BOS_BULL"
    BOS_BEAR = "BOS_BEAR"
    CHOCH_BULL = "CHOCH_BULL"
    CHOCH_BEAR = "CHOCH_BEAR"


@dataclass(frozen=True)
class StructureEvent:
    ts: pd.Timestamp
    index: int
    event_type: StructureEventType
    level: float
    displacement_ok: bool


@dataclass
class StructureState:
    bias: Bias
    last_swing_high: float | None
    last_swing_low: float | None
    last_swing_high_idx: int | None
    last_swing_low_idx: int | None
    events: list[StructureEvent]
    bos_count_in_bias: int


def _displacement_ok(
    row: pd.Series,
    atr_value: float,
    theta_body: float,
    theta_disp: float,
) -> bool:
    rng = float(row["high"] - row["low"])
    if rng <= 0 or not np.isfinite(atr_value) or atr_value <= 0:
        return False
    body = abs(float(row["close"] - row["open"]))
    return (body / rng) >= theta_body and rng >= theta_disp * atr_value


def compute_structure(
    df: pd.DataFrame,
    n_pivot: int,
    atr_period: int = 14,
    theta_body: float = 0.6,
    theta_disp: float = 1.0,
    asof_index: int | None = None,
) -> StructureState:
    """
    Build structure state using only bars up to asof_index (inclusive).
    BOS/CHoCH require close beyond swing (wick-only insufficient).
    """
    if asof_index is None:
        asof_index = len(df) - 1
    asof_index = min(asof_index, len(df) - 1)
    if asof_index < 0:
        return StructureState("NEUTRAL", None, None, None, None, [], 0)

    sub = df.iloc[: asof_index + 1]
    high = sub["high"].to_numpy(dtype=float)
    low = sub["low"].to_numpy(dtype=float)
    close = sub["close"].to_numpy(dtype=float)
    ph = pivot_highs(high, n_pivot)
    pl = pivot_lows(low, n_pivot)
    atr_s = atr(df.iloc[: asof_index + 1], atr_period)

    bias: Bias = "NEUTRAL"
    events: list[StructureEvent] = []
    last_sh: float | None = None
    last_sl: float | None = None
    last_sh_i: int | None = None
    last_sl_i: int | None = None
    bos_in_bias = 0

    # Walk forward chronologically with delayed pivots
    for i in range(len(sub)):
        # Update confirmed pivots available at i
        sh_i, sh_v = last_confirmed_pivot(high, ph, i, n_pivot)
        sl_i, sl_v = last_confirmed_pivot(low, pl, i, n_pivot)
        if sh_v is not None:
            last_sh, last_sh_i = sh_v, sh_i
        if sl_v is not None:
            last_sl, last_sl_i = sl_v, sl_i

        if last_sh is None or last_sl is None:
            continue

        bull_break = close[i] > last_sh
        bear_break = close[i] < last_sl
        if not bull_break and not bear_break:
            continue

        atr_v = float(atr_s.iloc[i]) if i < len(atr_s) and np.isfinite(atr_s.iloc[i]) else np.nan
        disp = _displacement_ok(sub.iloc[i], atr_v, theta_body, theta_disp)
        ts = sub.index[i]

        if bull_break:
            if bias == "BEAR":
                et = StructureEventType.CHOCH_BULL
                bias = "BULL"
                bos_in_bias = 0
            else:
                et = StructureEventType.BOS_BULL
                if bias == "BULL":
                    bos_in_bias += 1
                else:
                    bias = "BULL"
                    bos_in_bias = 1
            events.append(StructureEvent(ts, i, et, last_sh, disp))
            # After bullish break, refresh swing high reference eventually via pivots
        elif bear_break:
            if bias == "BULL":
                et = StructureEventType.CHOCH_BEAR
                bias = "BEAR"
                bos_in_bias = 0
            else:
                et = StructureEventType.BOS_BEAR
                if bias == "BEAR":
                    bos_in_bias += 1
                else:
                    bias = "BEAR"
                    bos_in_bias = 1
            events.append(StructureEvent(ts, i, et, last_sl, disp))

    return StructureState(
        bias=bias,
        last_swing_high=last_sh,
        last_swing_low=last_sl,
        last_swing_high_idx=last_sh_i,
        last_swing_low_idx=last_sl_i,
        events=events,
        bos_count_in_bias=bos_in_bias,
    )


def biases_aligned(bias_4h: Bias, bias_1h: Bias) -> bool:
    return bias_4h == bias_1h and bias_4h in ("BULL", "BEAR")
