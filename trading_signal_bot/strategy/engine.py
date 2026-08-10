"""Strategy engine: deterministic evaluate() -> SIGNAL_LONG | SIGNAL_SHORT | NO_TRADE.

Hard NO_TRADE only:
  data_quality, insufficient_bars, atr_invalid, no_directional_bias, no_liquidity_tp (E2)

Soft (scoring only): sweep, 5M confirm, premium/discount, RR>=1.5, POI overlap.
Entry prefers POI midpoint-or-better; SL = POI edge ± ATR buffer; TPs liquidity-only.
Anti look-ahead via closed-bar asof indices. No randomness.
"""

from __future__ import annotations

from typing import Any, Literal

import numpy as np
import pandas as pd

from trading_signal_bot.config import StrategyConfig
from trading_signal_bot.data import AssetClass, MultiTimeframeBundle
from trading_signal_bot.data.sessions import SessionWindow, is_in_session_windows
from trading_signal_bot.indicators import atr
from trading_signal_bot.liquidity import (
    build_liquidity_levels,
    detect_sweep_on_bar,
)
from trading_signal_bot.market_structure import biases_aligned, compute_structure
from trading_signal_bot.risk import (
    RiskPlan,
    apply_entry_costs,
    build_liquidity_tps,
    build_stop,
)
from trading_signal_bot.signals import SignalDecision
from trading_signal_bot.strategy.poi import (
    POI,
    bar_intersects_poi,
    detect_fvgs,
    detect_order_blocks,
    is_fvg_mitigated,
    is_ob_invalidated,
)
from trading_signal_bot.strategy.premium_discount import in_ote, premium_discount
from trading_signal_bot.strategy.scoring import explanation_from_flags

# Soft RR target for scoring (not a hard block)
RR_SCORE_TARGET = 1.5
# Relaxed 5M confirmation (scoring only)
CONFIRM_MAX_BARS = 10
CONFIRM_THETA_BODY = 0.3


def _asof_index(df: pd.DataFrame, ts: pd.Timestamp) -> int | None:
    """Last bar index with close_time <= ts (anti look-ahead)."""
    if df is None or df.empty:
        return None
    idx = df.index.searchsorted(ts, side="right") - 1
    if idx < 0:
        return None
    return int(idx)


def _no_trade(
    symbol: str,
    ts: pd.Timestamp,
    reason: str,
    config_hash: str,
    missing: list[str] | None = None,
    **meta: Any,
) -> SignalDecision:
    return SignalDecision(
        decision="NO_TRADE",
        symbol=symbol,
        ts_utc=ts,
        conditions_missing=missing or [reason],
        explanation=reason,
        config_hash=config_hash,
        meta=meta,
    )


def _session_ok(ts: pd.Timestamp, asset_class: AssetClass, cfg: StrategyConfig) -> bool:
    if asset_class == "CRYPTO":
        return True
    windows_raw = cfg.get("sessions", "fx_default", default=[]) or []
    windows = [
        SessionWindow(tz=w["tz"], start=w["start"], end=w["end"]) for w in windows_raw
    ]
    if not windows:
        return True
    return is_in_session_windows(ts, windows)


def _vol_ok(df_15m: pd.DataFrame, asof: int, cfg: StrategyConfig) -> bool:
    """Kept for optional re-enable; not used as a hard gate."""
    period = cfg.atr_period
    w = int(cfg.get("volatility_filter", "W_vol", default=100))
    vmin = float(cfg.get("volatility_filter", "V_min", default=0.5))
    vmax = float(cfg.get("volatility_filter", "V_max", default=2.5))
    sub = df_15m.iloc[: asof + 1]
    atr_s = atr(sub, period)
    if asof < w or not np.isfinite(atr_s.iloc[asof]):
        return False
    atr_now = float(atr_s.iloc[asof])
    atr_ref = float(atr_s.iloc[asof - w : asof + 1].median())
    if atr_ref <= 0:
        return False
    ratio = atr_now / atr_ref
    return vmin <= ratio <= vmax


def _confirm_5m(
    df_5m: pd.DataFrame,
    asof_5m: int,
    direction: Literal["LONG", "SHORT"],
    n_pivot: int,
    atr_period: int,
    theta_body: float = CONFIRM_THETA_BODY,
    max_bars_ago: int = CONFIRM_MAX_BARS,
) -> bool:
    """Soft 5M BOS/CHoCH confirmation (scoring only). Closed bars only."""
    if df_5m is None or df_5m.empty or asof_5m < 0:
        return False
    st = compute_structure(
        df_5m.iloc[: asof_5m + 1],
        n_pivot=n_pivot,
        atr_period=atr_period,
        theta_body=theta_body,
        theta_disp=0.0,
        asof_index=asof_5m,
    )
    if not st.events:
        return False
    last = st.events[-1]
    if asof_5m - int(last.index) > max_bars_ago:
        return False
    if direction == "LONG" and not str(last.event_type.value).endswith("BULL"):
        return False
    if direction == "SHORT" and not str(last.event_type.value).endswith("BEAR"):
        return False
    row = df_5m.iloc[int(last.index)]
    rng = float(row["high"] - row["low"])
    if rng <= 0:
        return False
    body = abs(float(row["close"] - row["open"]))
    return (body / rng) >= theta_body


def _poi_mid(poi: POI) -> float:
    return 0.5 * (float(poi.top) + float(poi.bottom))


def _entry_in_poi(
    direction: Literal["LONG", "SHORT"],
    poi: POI,
    price: float,
) -> float:
    """
    Optimized entry inside POI: midpoint or better.
    LONG  → mid or lower (closer to demand)
    SHORT → mid or higher (closer to supply)
    """
    bottom = float(poi.bottom)
    top = float(poi.top)
    if top < bottom:
        bottom, top = top, bottom
    mid = 0.5 * (top + bottom)
    px = float(price)
    if direction == "LONG":
        if bottom <= px <= top:
            return float(min(px, mid))
        return float(mid)
    if bottom <= px <= top:
        return float(max(px, mid))
    return float(mid)


def _price_anchor_poi(
    direction: Literal["LONG", "SHORT"],
    price: float,
    atr_value: float,
    ts: pd.Timestamp,
    asof_index: int,
) -> POI:
    """Tiny synthetic POI at price when no FVG/OB — enables SL geometry + plan."""
    pad = max(float(atr_value) * 0.05, abs(float(price)) * 1e-5, 1e-12)
    if direction == "LONG":
        bottom, top = float(price) - pad, float(price)
        want: Literal["BULL", "BEAR"] = "BULL"
    else:
        bottom, top = float(price), float(price) + pad
        want = "BEAR"
    return POI(
        poi_id=f"PRICE_ANCHOR_{want}:{ts.isoformat()}",
        poi_type="FVG",
        direction=want,
        bottom=bottom,
        top=top,
        created_ts=ts,
        created_index=int(asof_index),
        mitigated=False,
    )


def _poi_overlap_score(poi: POI, others: list[POI], theta: float) -> float:
    best = 0.0
    for other in others:
        if other.poi_id == poi.poi_id:
            continue
        if poi.poi_type == other.poi_type:
            continue
        if {poi.poi_type, other.poi_type} != {"FVG", "OB"}:
            continue
        best = max(best, poi.overlaps(other))
    if best >= theta:
        return 1.0
    return best


def _make_plan(
    direction: Literal["LONG", "SHORT"],
    raw_entry: float,
    poi: POI,
    levels: list,
    atr_value: float,
    asset_class: str,
    cfg: StrategyConfig,
    sl_ref: float | None = None,
) -> RiskPlan | None:
    """
    Build risk plan in-engine:
      - entry from caller (POI mid-or-better or raw price)
      - SL from POI edge ± ATR buffer (build_stop)
      - TP from liquidity only (E2); rr_min=0 so low RR is scoring-only
    """
    if not np.isfinite(raw_entry) or not np.isfinite(atr_value) or atr_value <= 0:
        return None

    risk_cfg = cfg.get("risk", default={}) or {}
    costs_cfg = cfg.get("costs", default={}) or {}
    theta_sl = float(risk_cfg.get("theta_sl_atr", 0.15))

    entry, cost = apply_entry_costs(
        direction, float(raw_entry), float(atr_value), asset_class, costs_cfg
    )

    # SL: POI extreme ± ATR buffer; sl_ref tightens further when present
    if direction == "LONG":
        extreme = float(poi.bottom) if sl_ref is None else min(float(sl_ref), float(poi.bottom))
    else:
        extreme = float(poi.top) if sl_ref is None else max(float(sl_ref), float(poi.top))

    sl = build_stop(direction, extreme, poi, float(atr_value), theta_sl)

    if direction == "LONG" and not (entry > sl):
        return None
    if direction == "SHORT" and not (entry < sl):
        return None

    risk = abs(entry - sl)
    if risk <= 0 or not np.isfinite(risk):
        return None

    # E2: any opposite liquidity TP (rr_min=0); no synthetic TP
    tp1_lv, tp2_lv, tp1, tp2 = build_liquidity_tps(
        direction, entry, sl, levels, float(atr_value), rr_min=0.0
    )
    if tp1 is None or tp1_lv is None:
        return None

    rr1 = abs(float(tp1) - entry) / risk
    if not np.isfinite(rr1):
        return None
    rr2 = abs(float(tp2) - entry) / risk if tp2 is not None else None

    return RiskPlan(
        direction=direction,
        entry=float(entry),
        sl=float(sl),
        tp1=float(tp1),
        tp2=float(tp2) if tp2 is not None else None,
        rr1=float(rr1),
        rr2=float(rr2) if rr2 is not None else None,
        risk_distance=float(risk),
        tp1_level_id=tp1_lv.level_id,
        tp2_level_id=tp2_lv.level_id if tp2_lv else None,
        cost_applied=float(cost),
    )


def _select_poi_plan(
    candidates: list[POI],
    mode: str,
    direction: Literal["LONG", "SHORT"],
    price: float,
    levels: list,
    atr_value: float,
    asset_class: str,
    cfg: StrategyConfig,
    sl_ref: float | None,
) -> tuple[POI | None, RiskPlan | None]:
    """Deterministic POI pick: best RR → recency → FVG+OB overlap → OB → FVG."""
    overlap_theta = float(cfg.get("scoring", "overlap_theta", default=0.25))
    scored: list[tuple[float, float, float, int, POI, RiskPlan]] = []
    for poi in candidates:
        raw_entry = _entry_in_poi(direction, poi, price)
        plan = _make_plan(
            direction, raw_entry, poi, levels, atr_value, asset_class, cfg, sl_ref
        )
        if plan is None:
            continue
        ov = _poi_overlap_score(poi, candidates, overlap_theta)
        type_rank = 1 if poi.poi_type == "OB" else 0
        scored.append(
            (
                float(plan.rr1),
                float(poi.created_ts.timestamp()),
                float(ov),
                type_rank,
                poi,
                plan,
            )
        )
    if not scored:
        return None, None
    if mode == "most_recent_valid":
        scored.sort(key=lambda x: x[1], reverse=True)
    else:
        scored.sort(key=lambda x: (x[0], x[1], x[2], x[3]), reverse=True)
    return scored[0][4], scored[0][5]


def evaluate(
    bundle: MultiTimeframeBundle,
    ts: pd.Timestamp,
    cfg: StrategyConfig,
    news_blackout: bool = False,
) -> SignalDecision:
    """
    Deterministic evaluation at 5M close timestamp `ts` (V3 scoring-based).
    Uses only fully closed HTF bars with close_time <= ts.
    Hard blocks: data_quality, insufficient_bars, atr_invalid, no_directional_bias,
    no_structure_shift, no_liquidity_tp (E2). Score is the primary trade filter.
    """
    symbol = bundle.symbol
    asset_class = bundle.asset_class
    ch = cfg.hash

    q = bundle.quality()
    if not q.ok:
        return _no_trade(symbol, ts, f"data_quality:{q.status.value}", ch)

    i5 = _asof_index(bundle.m5.df, ts)
    i15 = _asof_index(bundle.m15.df, ts)
    i1 = _asof_index(bundle.h1.df, ts)
    i4 = _asof_index(bundle.h4.df, ts)
    if None in (i5, i15, i1, i4):
        return _no_trade(symbol, ts, "insufficient_bars", ch)
    assert i5 is not None and i15 is not None and i1 is not None and i4 is not None

    ts = pd.Timestamp(ts)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    # Snap to last closed 5M bar (anti look-ahead)
    ts = bundle.m5.df.index[i5]

    news_blocked = bool(news_blackout)
    session_ok = _session_ok(ts, asset_class, cfg)

    n4 = int(cfg.get("pivots", "N_4H", default=2))
    n1 = int(cfg.get("pivots", "N_1H", default=2))
    n15 = int(cfg.get("pivots", "N_15M", default=2))
    n5 = int(cfg.get("pivots", "N_5M", default=2))
    atr_period = cfg.atr_period
    theta_body = float(cfg.get("structure", "theta_body", default=0.6))
    theta_disp = float(cfg.get("structure", "theta_disp", default=1.0))

    st4 = compute_structure(
        bundle.h4.df, n4, atr_period, theta_body, theta_disp, asof_index=i4
    )
    st1 = compute_structure(
        bundle.h1.df, n1, atr_period, theta_body, theta_disp, asof_index=i1
    )

    # Softened HTF context: 1H bias with 4H same-side or RANGE/NEUTRAL
    # (engine bias enum uses NEUTRAL for range-like state)
    st4_soft = st4.bias in ("BULL", "BEAR", "RANGE", "NEUTRAL")
    if st1.bias == "BULL" and st4.bias in ("BULL", "RANGE", "NEUTRAL"):
        direction: Literal["LONG", "SHORT"] = "LONG"
    elif st1.bias == "BEAR" and st4.bias in ("BEAR", "RANGE", "NEUTRAL"):
        direction = "SHORT"
    else:
        return _no_trade(
            symbol,
            ts,
            "no_directional_bias",
            ch,
            meta={"bias_4h": st4.bias, "bias_1h": st1.bias, "st4_soft": st4_soft},
        )

    structure_shift = bool(st1.bos_count_in_bias >= 1)
    if not structure_shift:
        return _no_trade(symbol, ts, "no_structure_shift", ch)

    htf_aligned = bool(biases_aligned(st4.bias, st1.bias))

    levels = build_liquidity_levels(
        bundle.m15.df,
        bundle.h1.df,
        ts,
        asset_class,
        n_pivot_1h=n1,
        epsilon_eq_atr=float(cfg.get("liquidity", "epsilon_eq_atr", default=0.1)),
        atr_period=atr_period,
        min_separation=int(cfg.get("liquidity", "min_equal_separation_bars", default=3)),
        asof_15m=i15,
        asof_1h=i1,
    )

    # Soft: liquidity sweep (never hard-blocks)
    x_max = int(cfg.get("confirmation", "X_max_bars_15m_after_sweep", default=6))
    sweep = None
    start_i = max(0, i15 - x_max)
    for j in range(i15, start_i - 1, -1):
        evs = detect_sweep_on_bar(
            bundle.m15.df.iloc[j], bundle.m15.df.index[j], j, levels
        )
        for ev in evs:
            if direction == "LONG" and ev.direction_taken == "LOW":
                sweep = ev
                break
            if direction == "SHORT" and ev.direction_taken == "HIGH":
                sweep = ev
                break
        if sweep is not None:
            break

    atr_15 = atr(bundle.m15.df.iloc[: i15 + 1], atr_period)
    atr_v = float(atr_15.iloc[i15]) if len(atr_15) else float("nan")
    if not np.isfinite(atr_v) or atr_v <= 0:
        return _no_trade(symbol, ts, "atr_invalid", ch)

    price = float(bundle.m5.df.iloc[i5]["close"])
    sweep_valid = False
    if sweep is not None:
        bars_since = i15 - int(sweep.index)
        dist = abs(price - float(sweep.extreme)) / atr_v
        if bars_since <= 5 and dist <= 1.5:
            sweep_valid = True

    mit_mode = cfg.fvg_mitigation_mode
    fvgs = detect_fvgs(
        bundle.m15.df,
        atr_period,
        float(cfg.get("fvg", "theta_fvg_atr", default=0.15)),
        i15,
    )
    st15 = compute_structure(
        bundle.m15.df, n15, atr_period, theta_body, theta_disp, asof_index=i15
    )
    bos_bull = [e.index for e in st15.events if e.event_type.value.endswith("BULL")]
    bos_bear = [e.index for e in st15.events if e.event_type.value.endswith("BEAR")]
    obs = detect_order_blocks(
        bundle.m15.df,
        bos_bull,
        bos_bear,
        w_ob=int(cfg.get("order_block", "W_ob", default=10)),
        geometry=str(cfg.get("order_block", "geometry", default="full_range")),
    )

    want_dir = "BULL" if direction == "LONG" else "BEAR"
    poi_window_start = int(sweep.index) if sweep is not None else max(0, i15 - x_max)
    valid_pois: list[POI] = []
    for poi in fvgs + obs:
        if poi.direction != want_dir:
            continue
        if sweep is not None and poi.created_index < sweep.index and poi.poi_type == "FVG":
            continue
        if poi.poi_type == "FVG" and is_fvg_mitigated(
            poi, bundle.m15.df, poi.created_index, i15, mit_mode  # type: ignore[arg-type]
        ):
            continue
        if poi.poi_type == "OB" and is_ob_invalidated(
            poi, bundle.m15.df, poi.created_index, i15
        ):
            continue
        intersected = False
        for k in range(poi_window_start, i15 + 1):
            if bar_intersects_poi(bundle.m15.df.iloc[k], poi):
                intersected = True
                break
        if intersected:
            valid_pois.append(poi)

    if not valid_pois:
        print(
            {
                "diag": "no_valid_poi",
                "ts": str(ts),
                "symbol": symbol,
                "direction": direction,
            }
        )

    pd_state = premium_discount(
        bundle.h1.df,
        n1,
        i1,
        discount_max=float(cfg.get("premium_discount", "discount_max", default=0.45)),
        premium_min=float(cfg.get("premium_discount", "premium_min", default=0.55)),
        price=price,
    )
    if direction == "LONG":
        pd_ok = pd_state.zone == "DISCOUNT"
    else:
        pd_ok = pd_state.zone == "PREMIUM"

    confirm_ok = _confirm_5m(
        bundle.m5.df,
        i5,
        direction,
        n5,
        atr_period,
        theta_body=CONFIRM_THETA_BODY,
        max_bars_ago=CONFIRM_MAX_BARS,
    )

    if sweep is not None:
        sl_ref: float | None = float(sweep.extreme)
    elif direction == "LONG":
        sl_ref = (
            float(st1.last_swing_low)
            if st1.last_swing_low is not None
            else price - atr_v
        )
    else:
        sl_ref = (
            float(st1.last_swing_high)
            if st1.last_swing_high is not None
            else price + atr_v
        )

    mode = str(cfg.get("meta", "poi_selection_mode", default="best_rr_then_recency"))
    selected: POI | None = None
    plan: RiskPlan | None = None
    used_price_fallback = False

    if valid_pois:
        selected, plan = _select_poi_plan(
            valid_pois,
            mode,
            direction,
            price,
            levels,
            atr_v,
            asset_class,
            cfg,
            sl_ref,
        )

    if selected is None or plan is None:
        selected = _price_anchor_poi(direction, price, atr_v, ts, i15)
        raw_entry = _entry_in_poi(direction, selected, price)
        plan = _make_plan(
            direction, raw_entry, selected, levels, atr_v, asset_class, cfg, sl_ref
        )
        used_price_fallback = True

    # E2 — liquidity TP only (no synthetic TP)
    if plan is None or selected is None:
        return _no_trade(symbol, ts, "no_liquidity_tp", ch)

    overlap_theta = float(cfg.get("scoring", "overlap_theta", default=0.25))
    selected_overlap = (
        0.0
        if used_price_fallback or not valid_pois
        else _poi_overlap_score(selected, valid_pois, overlap_theta)
    )

    rr = float(plan.rr1)

    # V3 score (primary filter)
    score = 0
    if structure_shift:
        score += 2
    if confirm_ok:
        score += 1
    if pd_ok:
        score += 1
    if sweep_valid:
        score += 1
    if rr >= 1.5:
        score += 2
    elif rr >= 1.2:
        score += 1
    else:
        score -= 1
    if selected_overlap >= overlap_theta:
        score += 2
    if used_price_fallback:
        score -= 1

    print(
        {
            "ts": ts,
            "score": score,
            "rr": rr,
            "sweep": sweep_valid,
            "confirm": confirm_ok,
            "pd": pd_ok,
            "poi_count": len(valid_pois),
            "fallback": used_price_fallback,
        }
    )

    if score >= 5:
        setup_type: Literal["A+", "A", "B"] = "A+"
    elif score >= 3:
        setup_type = "A"
    elif score >= 2:
        setup_type = "B"
    else:
        return _no_trade(symbol, ts, "low_score", ch)

    deep = 0.0
    if pd_state.pos is not None:
        if direction == "LONG" and pd_state.pos <= float(
            cfg.get("premium_discount", "deep_discount_max", default=0.30)
        ):
            deep = 1.0
        if direction == "SHORT" and pd_state.pos >= float(
            cfg.get("premium_discount", "deep_premium_min", default=0.70)
        ):
            deep = 1.0

    ote_flag = 0.0
    if cfg.get("premium_discount", "ote_enabled", default=False) and pd_state.pos is not None:
        if direction == "LONG":
            ote_flag = 1.0 if in_ote(1.0 - pd_state.pos) else 0.0
        else:
            ote_flag = 1.0 if in_ote(pd_state.pos) else 0.0

    features = {
        "f_overlap_fvg_ob": float(selected_overlap >= overlap_theta),
        "f_deep_pd": deep,
        "f_ote": ote_flag,
        "f_maj_liq": 1.0 if (sweep is not None and sweep.level.major) else 0.0,
        "f_mtf_fvg": 0.0,
        "f_disp": 1.0 if any(e.displacement_ok for e in st15.events[-3:]) else 0.0,
        "f_rr": min(float(plan.rr1) / 3.0, 1.0) if plan.rr1 else 0.0,
        "f_rr_ok": float(rr >= 1.5),
        "f_htf_aligned": float(htf_aligned),
        "f_sweep_present": float(sweep_valid),
        "f_pd_ok": float(pd_ok),
        "f_confirm_ok": float(confirm_ok),
        "f_session_ok": float(session_ok),
        "f_news_blocked": float(news_blocked),
        "f_poi_count": float(len(valid_pois)),
        "f_structure_shift": float(structure_shift),
        "f_price_fallback": float(used_price_fallback),
    }

    validated = ["liquidity_tp", "structure_shift"]
    if not used_price_fallback:
        validated.append("poi_fvg_or_ob")
    else:
        validated.append("poi_price_fallback")
    if rr >= 1.5:
        validated.append("rr_ge_1_5")
    elif rr >= 1.2:
        validated.append("rr_ge_1_2")
    if selected_overlap >= overlap_theta:
        validated.append("fvg_ob_overlap")
    if confirm_ok:
        validated.append("confirm_5m")
    if pd_ok:
        validated.append("premium_discount")
    if sweep_valid:
        validated.append("liquidity_sweep")
    if htf_aligned:
        validated.append("htf_aligned")
    if session_ok:
        validated.append("session_ok")

    flags = {
        "LiquiditySweep": sweep_valid,
        "POI": not used_price_fallback,
        "Confirm5M": confirm_ok,
        "StructureShift": structure_shift,
        "HTFAlignment": htf_aligned,
        "PremiumDiscount": pd_ok,
        "OverlapFVGOB": selected_overlap >= overlap_theta,
        "MajorLiquidity": bool(sweep is not None and sweep.level.major),
    }

    decision_type: Literal["SIGNAL_LONG", "SIGNAL_SHORT"] = (
        "SIGNAL_LONG" if direction == "LONG" else "SIGNAL_SHORT"
    )
    return SignalDecision(
        decision=decision_type,
        symbol=symbol,
        ts_utc=ts,
        direction=direction,
        category=setup_type,
        setup_score=float(score),
        entry=plan.entry,
        sl=plan.sl,
        tp1=plan.tp1,
        tp2=plan.tp2,
        rr1=plan.rr1,
        rr2=plan.rr2,
        bias_4h=st4.bias,
        bias_1h=st1.bias,
        conditions_validated=validated,
        explanation=explanation_from_flags(flags),
        sweep_id=(sweep.level.level_id if sweep is not None else ""),
        poi_id=selected.poi_id,
        config_hash=ch,
        features=features,
        meta={
            "sweep_time": str(sweep.ts) if sweep is not None else None,
            "poi_type": selected.poi_type,
            "poi_mid": _poi_mid(selected),
            "fvg_mitigation_mode": mit_mode,
            "poi_selection_mode": mode,
            "asset_class": asset_class,
            "risk_distance": plan.risk_distance,
            "cost_applied": plan.cost_applied,
            "tp1_level_id": plan.tp1_level_id,
            "tp2_level_id": plan.tp2_level_id,
            "selected_poi_overlap": selected_overlap,
            "setup_type": setup_type,
            "score": score,
            "htf_aligned": htf_aligned,
            "sweep_valid": sweep_valid,
            "pd_ok": pd_ok,
            "confirm_ok": confirm_ok,
            "structure_shift": structure_shift,
            "poi_price_fallback": used_price_fallback,
            "poi_count": len(valid_pois),
            "rr": rr,
            "rr_score_target": RR_SCORE_TARGET,
            "session_ok": session_ok,
            "news_blocked": news_blocked,
            "strategy_version": "V3",
        },
    )
