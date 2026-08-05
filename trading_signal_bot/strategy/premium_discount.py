"""Premium / discount positioning on 1H structural range."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

from trading_signal_bot.indicators import last_confirmed_pivot, pivot_highs, pivot_lows


@dataclass(frozen=True)
class PremiumDiscountState:
    pos: float | None
    swing_low: float | None
    swing_high: float | None
    zone: Literal["DISCOUNT", "PREMIUM", "EQUILIBRIUM", "UNKNOWN"]


def premium_discount(
    df_1h: pd.DataFrame,
    n_pivot: int,
    asof_index: int,
    discount_max: float = 0.45,
    premium_min: float = 0.55,
    price: float | None = None,
) -> PremiumDiscountState:
    sub = df_1h.iloc[: asof_index + 1]
    if len(sub) == 0:
        return PremiumDiscountState(None, None, None, "UNKNOWN")
    high = sub["high"].to_numpy(dtype=float)
    low = sub["low"].to_numpy(dtype=float)
    _, sh = last_confirmed_pivot(high, pivot_highs(high, n_pivot), asof_index, n_pivot)
    _, sl = last_confirmed_pivot(low, pivot_lows(low, n_pivot), asof_index, n_pivot)
    if sh is None or sl is None or sh <= sl:
        return PremiumDiscountState(None, sl, sh, "UNKNOWN")
    px = float(sub.iloc[-1]["close"] if price is None else price)
    pos = (px - sl) / (sh - sl)
    if pos < discount_max:
        zone: Literal["DISCOUNT", "PREMIUM", "EQUILIBRIUM", "UNKNOWN"] = "DISCOUNT"
    elif pos > premium_min:
        zone = "PREMIUM"
    else:
        zone = "EQUILIBRIUM"
    return PremiumDiscountState(pos, sl, sh, zone)


def in_ote(pos: float | None, ote_low: float = 0.618, ote_high: float = 0.79) -> bool:
    if pos is None or not np.isfinite(pos):
        return False
    # OTE measured as retracement depth from premium side for shorts / discount for longs
    # Here pos is location in range; for long OTE typically 0.618-0.79 retracement from high
    # => price in lower portion: pos between (1-0.79)=0.21 and (1-0.618)=0.382 is alternative.
    # Spec V1: "Retracement Fibonacci of impulsive move" — use range position in [ote_low, ote_high]
    # for SHORT (premium OTE) and [1-ote_high, 1-ote_low] for LONG handled by caller.
    return ote_low <= pos <= ote_high
