"""Risk: entry, stop, targets, RR gates, costs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from trading_signal_bot.liquidity import LiquidityLevel, opposite_liquidity_targets
from trading_signal_bot.strategy.poi import POI


@dataclass(frozen=True)
class RiskPlan:
    direction: Literal["LONG", "SHORT"]
    entry: float
    sl: float
    tp1: float
    tp2: float | None
    rr1: float
    rr2: float | None
    risk_distance: float
    tp1_level_id: str
    tp2_level_id: str | None
    cost_applied: float


def apply_entry_costs(
    direction: Literal["LONG", "SHORT"],
    entry: float,
    atr_value: float,
    asset_class: str,
    costs_cfg: dict,
) -> tuple[float, float]:
    """Return (effective_entry, cost_price_units)."""
    if asset_class in ("FX", "XAU"):
        spread = float(costs_cfg.get("fx_spread_atr_fraction", 0.02)) * atr_value
        slip = float(costs_cfg.get("fx_slippage_atr_fraction", 0.01)) * atr_value
        half = 0.5 * spread
        cost = half + slip
    else:
        fee_bps = float(costs_cfg.get("crypto_taker_fee_bps", 4.0))
        slip = float(costs_cfg.get("crypto_slippage_atr_fraction", 0.02)) * atr_value
        cost = entry * (fee_bps / 10000.0) + slip
    if direction == "LONG":
        return entry + cost, cost
    return entry - cost, cost


def build_stop(
    direction: Literal["LONG", "SHORT"],
    sweep_extreme: float,
    poi: POI | None,
    atr_value: float,
    theta_sl_atr: float,
) -> float:
    buffer = theta_sl_atr * atr_value
    if direction == "LONG":
        extreme = sweep_extreme
        if poi is not None:
            extreme = min(extreme, poi.bottom)
        return extreme - buffer
    extreme = sweep_extreme
    if poi is not None:
        extreme = max(extreme, poi.top)
    return extreme + buffer


def build_liquidity_tps(
    direction: Literal["LONG", "SHORT"],
    entry: float,
    sl: float,
    levels: list[LiquidityLevel],
    atr_value: float,
    rr_min: float,
    min_tp_atr: float = 0.5,
) -> tuple[LiquidityLevel | None, LiquidityLevel | None, float | None, float | None]:
    """
    E2 validated: if no liquidity TP with RR >= rr_min => no synthetic target.
    Returns (tp1_level, tp2_level, tp1, tp2) or Nones.
    """
    risk = abs(entry - sl)
    if risk <= 0:
        return None, None, None, None
    min_dist = max(min_tp_atr * atr_value, rr_min * risk * 0.999)
    targets = opposite_liquidity_targets(levels, direction, entry, min_distance=min_dist)
    valid: list[tuple[LiquidityLevel, float, float]] = []
    for lv in targets:
        rr = abs(lv.price - entry) / risk
        if rr >= rr_min:
            if direction == "LONG" and lv.price > entry > sl:
                valid.append((lv, lv.price, rr))
            if direction == "SHORT" and lv.price < entry < sl:
                valid.append((lv, lv.price, rr))
    if not valid:
        return None, None, None, None
    tp1_lv, tp1, rr1 = valid[0]
    tp2_lv, tp2, rr2 = (valid[1] if len(valid) > 1 else (None, None, None))
    return tp1_lv, tp2_lv, tp1, tp2


def make_risk_plan(
    direction: Literal["LONG", "SHORT"],
    raw_entry: float,
    sweep_extreme: float,
    poi: POI,
    levels: list[LiquidityLevel],
    atr_value: float,
    asset_class: str,
    risk_cfg: dict,
    costs_cfg: dict,
) -> RiskPlan | None:
    entry, cost = apply_entry_costs(direction, raw_entry, atr_value, asset_class, costs_cfg)
    sl = build_stop(
        direction,
        sweep_extreme,
        poi,
        atr_value,
        float(risk_cfg.get("theta_sl_atr", 0.15)),
    )
    # After costs, geometric validity
    if direction == "LONG" and not (entry > sl):
        return None
    if direction == "SHORT" and not (entry < sl):
        return None

    rr_min = float(risk_cfg.get("rr_min", 2.0))
    tp1_lv, tp2_lv, tp1, tp2 = build_liquidity_tps(
        direction, entry, sl, levels, atr_value, rr_min
    )
    if tp1 is None or tp1_lv is None:
        return None  # E2 strict NO_TRADE

    risk = abs(entry - sl)
    rr1 = abs(tp1 - entry) / risk
    if rr1 < rr_min:
        return None
    rr2 = abs(tp2 - entry) / risk if tp2 is not None else None
    return RiskPlan(
        direction=direction,
        entry=entry,
        sl=sl,
        tp1=tp1,
        tp2=tp2,
        rr1=rr1,
        rr2=rr2,
        risk_distance=risk,
        tp1_level_id=tp1_lv.level_id,
        tp2_level_id=tp2_lv.level_id if tp2_lv else None,
        cost_applied=cost,
    )
