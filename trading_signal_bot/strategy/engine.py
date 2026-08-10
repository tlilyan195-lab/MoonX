"""Strategy engine: deterministic evaluate() -> SIGNAL_LONG | SIGNAL_SHORT | NO_TRADE."""

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
from trading_signal_bot.risk import make_risk_plan
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
from trading_signal_bot.strategy.scoring import compute_score, explanation_from_flags


def _asof_index(df: pd.DataFrame, ts: pd.Timestamp) -> int | None:
    """Last bar index with close_time <= ts (anti look-ahead)."""
    if df.empty:
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
        if cfg.get("sessions", "crypto_allow_all_until_calibrated", default=True):
            return True
        return True
    windows_raw = cfg.get("sessions", "fx_default", default=[]) or []
    windows = [
        SessionWindow(tz=w["tz"], start=w["start"], end=w["end"]) for w in windows_raw
    ]
    if not windows:
        return True
    return is_in_session_windows(ts, windows)


def _vol_ok(df_15m: pd.DataFrame, asof: int, cfg: StrategyConfig) -> bool:
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
    theta_body: float,
    atr_period: int,
) -> bool:
    """
    BOS/CHoCH on 5M with body/displacement measured on the *event candle*
    that produced the break — not on a later unrelated bar.
    """
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
    # Event must be recent enough relative to evaluation bar
    if asof_5m - last.index > 2:
        return False
    if direction == "LONG" and not last.event_type.value.endswith("BULL"):
        return False
    if direction == "SHORT" and not last.event_type.value.endswith("BEAR"):
        return False
    # P1: body ratio on the BOS/CHoCH candle itself
    row = df_5m.iloc[last.index]
    rng = float(row["high"] - row["low"])
    body = abs(float(row["close"] - row["open"]))
    return rng > 0 and (body / rng) >= theta_body


def _poi_overlap_score(poi: POI, others: list[POI], theta: float) -> float:
    """Max overlap of `poi` with opposite-type POIs in `others` (0 or 1 if >= theta)."""
    best = 0.0
    for other in others:
        if other.poi_id == poi.poi_id:
            continue
        if poi.poi_type == other.poi_type:
            continue
        # Only FVG↔OB confluence counts
        types = {poi.poi_type, other.poi_type}
        if types != {"FVG", "OB"}:
            continue
        best = max(best, poi.overlaps(other))
    if best >= theta:
        return 1.0
    return best


def _select_poi(
    candidates: list[POI],
    mode: str,
    direction: Literal["LONG", "SHORT"],
    raw_entry: float,
    sweep_extreme: float,
    levels: list,
    atr_value: float,
    asset_class: str,
    cfg: StrategyConfig,
) -> tuple[POI | None, Any]:
    """
    P1 tie-break (deterministic):
      best RR → recency → FVG+OB overlap → OB → FVG
    Variant: most_recent_valid (separate run).
    """
    risk_cfg = cfg.get("risk", default={}) or {}
    costs_cfg = cfg.get("costs", default={}) or {}
    overlap_theta = float(cfg.get("scoring", "overlap_theta", default=0.25))
    scored: list[tuple[float, float, float, int, POI, Any]] = []
    for poi in candidates:
        plan = make_risk_plan(
            direction,
            raw_entry,
            sweep_extreme,
            poi,
            levels,
            atr_value,
            asset_class,
            risk_cfg,
            costs_cfg,
        )
        if plan is None:
            continue
        ov = _poi_overlap_score(poi, candidates, overlap_theta)
        # type_rank: OB=1, FVG=0 for ascending sort we invert later
        type_rank = 1 if poi.poi_type == "OB" else 0
        scored.append(
            (
                plan.rr1,
                poi.created_ts.timestamp(),
                ov,
                type_rank,
                poi,
                plan,
            )
        )

    if not scored:
        return None, None

    if mode == "most_recent_valid":
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[0][4], scored[0][5]

    # best RR → recency → overlap → OB → FVG
    scored.sort(
        key=lambda x: (x[0], x[1], x[2], x[3]),
        reverse=True,
    )
    return scored[0][4], scored[0][5]


def evaluate(
    bundle: MultiTimeframeBundle,
    ts: pd.Timestamp,
    cfg: StrategyConfig,
    news_blackout: bool = False,
) -> SignalDecision:
    """
    Deterministic evaluation at 5M close timestamp `ts`.
    Uses only fully closed HTF bars with close_time <= ts.
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

    # Evaluate on last fully closed 5M bar at/before ts (anti look-ahead)
    ts = pd.Timestamp(ts)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    ts = bundle.m5.df.index[i5]

    if news_blackout:
        return _no_trade(symbol, ts, "news_blackout", ch)
    if not _session_ok(ts, asset_class, cfg):
        return _no_trade(symbol, ts, "session_filter", ch)
    if not _vol_ok(bundle.m15.df, i15, cfg):
        return _no_trade(symbol, ts, "volatility_filter", ch)

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
    # HTF bias alignment is informational only (no hard NO_TRADE block).
    # Direction prefers 1H structure; fall back to 4H; NEUTRAL → NO_TRADE.
    if st1.bias == "BULL":
        direction: Literal["LONG", "SHORT"] = "LONG"
    elif st1.bias == "BEAR":
        direction = "SHORT"
    elif st4.bias == "BULL":
        direction = "LONG"
    elif st4.bias == "BEAR":
        direction = "SHORT"
    else:
        return _no_trade(
            symbol,
            ts,
            "no_directional_bias",
            ch,
            meta={"bias_4h": st4.bias, "bias_1h": st1.bias},
        )
    structure_shift = bool(st1.bos_count_in_bias >= 1) or any(
        (direction == "LONG" and str(e.event_type.value).endswith("BULL"))
        or (direction == "SHORT" and str(e.event_type.value).endswith("BEAR"))
        for e in st1.events[-5:]
    )
    if not structure_shift:
        return _no_trade(symbol, ts, "no_structure_shift", ch)

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

    # Find recent sweep on 15M within X bars
    x_max = int(cfg.get("confirmation", "X_max_bars_15m_after_sweep", default=6))
    sweep = None
    start_i = max(0, i15 - x_max)
    for j in range(i15, start_i - 1, -1):
        evs = detect_sweep_on_bar(bundle.m15.df.iloc[j], bundle.m15.df.index[j], j, levels)
        for ev in evs:
            if direction == "LONG" and ev.direction_taken == "LOW":
                sweep = ev
                break
            if direction == "SHORT" and ev.direction_taken == "HIGH":
                sweep = ev
                break
        if sweep is not None:
            break
    if sweep is None:
        return _no_trade(symbol, ts, "no_liquidity_sweep", ch)

    bars_since = i15 - sweep.index
    if bars_since > x_max:
        return _no_trade(symbol, ts, "sweep_expired", ch)

    atr_15 = atr(bundle.m15.df.iloc[: i15 + 1], atr_period)
    atr_v = float(atr_15.iloc[i15])
    if not np.isfinite(atr_v) or atr_v <= 0:
        return _no_trade(symbol, ts, "atr_invalid", ch)

    dist = abs(float(bundle.m5.df.iloc[i5]["close"]) - sweep.extreme) / atr_v
    if dist > float(cfg.get("liquidity", "D_max_atr", default=2.0)):
        return _no_trade(symbol, ts, "sweep_entry_distance", ch)

    mit_mode = cfg.fvg_mitigation_mode
    fvgs = detect_fvgs(
        bundle.m15.df,
        atr_period,
        float(cfg.get("fvg", "theta_fvg_atr", default=0.15)),
        i15,
    )
    # BOS indices on 15M for OB detection
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
    valid_pois: list[POI] = []
    for poi in fvgs + obs:
        if poi.direction != want_dir:
            continue
        if poi.created_index < sweep.index:
            # Prefer POIs formed after/during reaction; allow OB slightly before if still unmitigated
            if poi.poi_type == "FVG":
                continue
        if poi.poi_type == "FVG" and is_fvg_mitigated(
            poi, bundle.m15.df, poi.created_index, i15, mit_mode  # type: ignore[arg-type]
        ):
            continue
        if poi.poi_type == "OB" and is_ob_invalidated(poi, bundle.m15.df, poi.created_index, i15):
            continue
        # Price must intersect POI on recent 15M bar(s) since sweep
        intersected = False
        for k in range(sweep.index, i15 + 1):
            if bar_intersects_poi(bundle.m15.df.iloc[k], poi):
                intersected = True
                break
        if intersected:
            valid_pois.append(poi)

    if not valid_pois:
        return _no_trade(symbol, ts, "no_valid_poi", ch)

    pd_state = premium_discount(
        bundle.h1.df,
        n1,
        i1,
        discount_max=float(cfg.get("premium_discount", "discount_max", default=0.45)),
        premium_min=float(cfg.get("premium_discount", "premium_min", default=0.55)),
        price=float(bundle.m5.df.iloc[i5]["close"]),
    )
    if direction == "LONG" and pd_state.zone != "DISCOUNT":
        return _no_trade(symbol, ts, "not_in_discount", ch, meta={"pd": pd_state.zone})
    if direction == "SHORT" and pd_state.zone != "PREMIUM":
        return _no_trade(symbol, ts, "not_in_premium", ch, meta={"pd": pd_state.zone})

    if not _confirm_5m(
        bundle.m5.df,
        i5,
        direction,
        n5,
        float(cfg.get("confirmation", "theta_body_5m", default=0.5)),
        atr_period,
    ):
        return _no_trade(symbol, ts, "no_5m_confirmation", ch)

    raw_entry = float(bundle.m5.df.iloc[i5]["close"])
    mode = str(cfg.get("meta", "poi_selection_mode", default="best_rr_then_recency"))
    selected, plan = _select_poi(
        valid_pois,
        mode,
        direction,
        raw_entry,
        sweep.extreme,
        levels,
        atr_v,
        asset_class,
        cfg,
    )
    if selected is None or plan is None:
        return _no_trade(symbol, ts, "no_liquidity_tp_or_rr", ch)  # E2

    # Keep RR filter (no fake TP)
    if float(plan.rr1) < float(cfg.rr_min):
        return _no_trade(
            symbol,
            ts,
            "rr_below_min",
            ch,
            meta={"rr": float(plan.rr1), "rr_min": float(cfg.rr_min)},
        )

    # P1 A+: overlap must involve the *selected* POI, not unrelated POI pairs
    overlap_theta = float(cfg.get("scoring", "overlap_theta", default=0.25))
    selected_overlap = _poi_overlap_score(selected, valid_pois, overlap_theta)

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
        "f_maj_liq": 1.0 if sweep.level.major else 0.0,
        "f_mtf_fvg": 0.0,
        "f_disp": 1.0 if any(e.displacement_ok for e in st15.events[-3:]) else 0.0,
        "f_rr": min(plan.rr1 / 3.0, 1.0),
    }
    weights = cfg.get("scoring", "weights", default={}) or {}
    breakdown = compute_score(
        features,
        weights,
        s_b=float(cfg.get("scoring", "S_B", default=55)),
        s_a=float(cfg.get("scoring", "S_A", default=70)),
        s_aplus=float(cfg.get("scoring", "S_Aplus", default=85)),
        require_overlap_for_aplus=cfg.require_overlap_for_aplus,
    )

    # Category from structural gates (not score thresholds alone).
    # A+ keeps FVG+OB overlap requirement; A allows FVG or OB.
    has_fvg = any(p.poi_type == "FVG" for p in valid_pois)
    has_ob = any(p.poi_type == "OB" for p in valid_pois)
    fvg_ob_overlap = bool(selected_overlap >= overlap_theta)
    liquidity_sweep = True  # sweep already required above
    # structure_shift already enforced earlier; keep explicit for category rule
    is_aplus = fvg_ob_overlap and liquidity_sweep and structure_shift
    is_a = (has_fvg or has_ob) and liquidity_sweep and structure_shift
    if is_aplus:
        setup_type: Literal["A+", "A"] = "A+"
    elif is_a:
        setup_type = "A"
    else:
        return _no_trade(symbol, ts, "setup_not_a_or_aplus", ch)

    print({"setup": setup_type, "rr": float(plan.rr1)})

    validated = [
        "structure_shift",
        "liquidity_sweep",
        "poi_fvg_or_ob",
        "confirm_5m",
        "rr_min",
        "session_vol_ok",
        "premium_discount",
    ]
    if fvg_ob_overlap:
        validated.append("fvg_ob_overlap")
    flags = {
        "LiquiditySweep": True,
        "POI": True,
        "Confirm5M": True,
        "StructureShift": True,
        "HTFAlignment": bool(biases_aligned(st4.bias, st1.bias)),
        "OverlapFVGOB": fvg_ob_overlap,
        "MajorLiquidity": bool(sweep.level.major),
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
        setup_score=breakdown.score,
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
        sweep_id=sweep.level.level_id,
        poi_id=selected.poi_id,
        config_hash=ch,
        features=features,
        meta={
            "sweep_time": str(sweep.ts),
            "poi_type": selected.poi_type,
            "fvg_mitigation_mode": mit_mode,
            "poi_selection_mode": mode,
            "asset_class": asset_class,
            "risk_distance": plan.risk_distance,
            "cost_applied": plan.cost_applied,
            "tp1_level_id": plan.tp1_level_id,
            "tp2_level_id": plan.tp2_level_id,
            "selected_poi_overlap": selected_overlap,
            "confirm_5m_event_index": None,
            "setup_type": setup_type,
            "htf_bias_aligned": bool(biases_aligned(st4.bias, st1.bias)),
            "has_fvg": has_fvg,
            "has_ob": has_ob,
            "structure_shift": structure_shift,
        },
    )
