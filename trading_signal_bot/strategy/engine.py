"""Strategy engine: deterministic evaluate() -> SIGNAL_LONG | SIGNAL_SHORT | NO_TRADE.

Hard NO_TRADE (V7):
  data_quality, insufficient_bars, atr_invalid, no_directional_bias, low_volatility

Decision intelligence:
  HTF regime (trend_up / trend_down / range), ATR percentile volatility,
  adaptive cooldown, entry-quality anti-chop, refined PD/RR tiers.
Anti look-ahead via closed-bar asof indices. No randomness.
"""

from __future__ import annotations

from typing import Any, Literal

import numpy as np
import pandas as pd

from trading_signal_bot.config import StrategyConfig
from trading_signal_bot.data import AssetClass, MultiTimeframeBundle
from trading_signal_bot.data.sessions import SessionWindow, is_in_session_windows
from trading_signal_bot.indicators import atr, pivot_highs, pivot_lows
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

Regime = Literal["trend_up", "trend_down", "range"]
VolState = Literal["low", "normal", "high"]

RR_SCORE_TARGET = 1.5
RR_HARD_MIN = 1.5
CONFIRM_MAX_BARS = 10
CONFIRM_THETA_BODY = 0.3

REGIME_LOOKBACK = 40
REGIME_PIVOT = 2
ATR_PCT_WINDOW = 100
LOW_VOL_PERCENTILE = 20.0
HIGH_VOL_PERCENTILE = 80.0
ENTRY_NEAR_ATR_MULT = 0.3
MID_RANGE_FRAC = 0.25
COOLDOWN_HIGH_SCORE = 3
COOLDOWN_LOW_SCORE = 6
SCORE_HIGH_FOR_SHORT_CD = 5

# Per-symbol last accepted signal (adaptive cooldown + entry quality)
_LAST_SIGNAL: dict[str, dict[str, Any]] = {}


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
    """Legacy config-based session helper (feature flag only)."""
    if asset_class == "CRYPTO":
        return True
    windows_raw = cfg.get("sessions", "fx_default", default=[]) or []
    windows = [
        SessionWindow(tz=w["tz"], start=w["start"], end=w["end"]) for w in windows_raw
    ]
    if not windows:
        return True
    return is_in_session_windows(ts, windows)


def _session_valid_london_ny(ts: pd.Timestamp) -> bool:
    """V7 soft sessions: London 06:00–12:00 UTC, New York 12:00–18:00 UTC."""
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    else:
        t = t.tz_convert("UTC")
    hour = int(t.hour)
    return 6 <= hour < 12 or 12 <= hour < 18


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


def _swing_series(
    df: pd.DataFrame,
    asof: int,
    lookback: int = REGIME_LOOKBACK,
    n_pivot: int = REGIME_PIVOT,
) -> tuple[list[float], list[float]]:
    """Confirmed swing highs/lows in the lookback window ending at asof."""
    if df is None or df.empty or asof < 0:
        return [], []
    start = max(0, asof - lookback + 1)
    sub = df.iloc[start : asof + 1]
    if len(sub) < (2 * n_pivot + 3):
        return [], []
    high = sub["high"].to_numpy(dtype=float)
    low = sub["low"].to_numpy(dtype=float)
    ph = pivot_highs(high, n_pivot)
    pl = pivot_lows(low, n_pivot)
    highs = [float(high[i]) for i in range(len(high)) if bool(ph[i])]
    lows = [float(low[i]) for i in range(len(low)) if bool(pl[i])]
    return highs, lows


def _classify_swing_regime(highs: list[float], lows: list[float]) -> Regime:
    """HH+HL → trend_up; LH+LL → trend_down; else range."""
    hh = len(highs) >= 2 and highs[-1] > highs[-2]
    hl = len(lows) >= 2 and lows[-1] > lows[-2]
    lh = len(highs) >= 2 and highs[-1] < highs[-2]
    ll = len(lows) >= 2 and lows[-1] < lows[-2]
    if hh and hl:
        return "trend_up"
    if lh and ll:
        return "trend_down"
    return "range"


def _detect_regime(
    df_h1: pd.DataFrame,
    df_h4: pd.DataFrame,
    i1: int,
    i4: int,
) -> Regime:
    """Combine H1 + H4 swing structure into a single regime label."""
    h1_hi, h1_lo = _swing_series(df_h1, i1)
    h4_hi, h4_lo = _swing_series(df_h4, i4)
    r1 = _classify_swing_regime(h1_hi, h1_lo)
    r4 = _classify_swing_regime(h4_hi, h4_lo)
    if r1 == r4:
        return r1
    if r1 != "range" and r4 == "range":
        return r1
    if r4 != "range" and r1 == "range":
        return r4
    # Conflicting directional HTF → treat as range (choppy)
    return "range"


def _regime_aligns(regime: Regime, direction: Literal["LONG", "SHORT"]) -> bool:
    if regime == "trend_up" and direction == "LONG":
        return True
    if regime == "trend_down" and direction == "SHORT":
        return True
    return False


def _regime_opposes(regime: Regime, direction: Literal["LONG", "SHORT"]) -> bool:
    if regime == "trend_up" and direction == "SHORT":
        return True
    if regime == "trend_down" and direction == "LONG":
        return True
    return False


def _atr_percentile_state(
    atr_series: pd.Series,
    asof: int,
    window: int = ATR_PCT_WINDOW,
) -> tuple[float, VolState, bool]:
    """
    ATR percentile in [0, 100], volatility_state, and low_vol flag.
    low_vol when ATR < 20th percentile of the rolling window.
    """
    if atr_series is None or len(atr_series) == 0 or asof < 0:
        return float("nan"), "normal", False
    end = asof + 1
    start = max(0, end - window)
    window_vals = atr_series.iloc[start:end].to_numpy(dtype=float)
    window_vals = window_vals[np.isfinite(window_vals) & (window_vals > 0)]
    now = float(atr_series.iloc[asof]) if asof < len(atr_series) else float("nan")
    if len(window_vals) < 5 or not np.isfinite(now) or now <= 0:
        return float("nan"), "normal", False
    pct = float(100.0 * np.mean(window_vals <= now))
    low_vol = pct < LOW_VOL_PERCENTILE
    if low_vol:
        state: VolState = "low"
    elif pct >= HIGH_VOL_PERCENTILE:
        state = "high"
    else:
        state = "normal"
    return pct, state, low_vol


def _recent_range(
    df: pd.DataFrame,
    asof: int,
    lookback: int = REGIME_LOOKBACK,
) -> tuple[float, float]:
    start = max(0, asof - lookback + 1)
    sub = df.iloc[start : asof + 1]
    if sub.empty:
        return float("nan"), float("nan")
    return float(sub["high"].max()), float(sub["low"].min())


def _mid_range_blocked(price: float, range_hi: float, range_lo: float) -> bool:
    if not (np.isfinite(price) and np.isfinite(range_hi) and np.isfinite(range_lo)):
        return False
    width = range_hi - range_lo
    if width <= 0:
        return False
    mid = 0.5 * (range_hi + range_lo)
    return abs(price - mid) < (MID_RANGE_FRAC * width)


def _adaptive_cooldown_bars(last_score: float | None) -> int:
    if last_score is not None and float(last_score) >= SCORE_HIGH_FOR_SHORT_CD:
        return COOLDOWN_HIGH_SCORE
    return COOLDOWN_LOW_SCORE


def _record_signal(
    symbol: str,
    index: int,
    direction: Literal["LONG", "SHORT"],
    entry: float,
    score: float,
) -> None:
    _LAST_SIGNAL[symbol] = {
        "index": int(index),
        "direction": direction,
        "entry": float(entry),
        "score": float(score),
    }


def _cooldown_blocked(symbol: str, current_index: int) -> tuple[bool, int]:
    prev = _LAST_SIGNAL.get(symbol)
    if not prev:
        return False, COOLDOWN_LOW_SCORE
    last_i = int(prev["index"])
    if current_index < last_i:
        _LAST_SIGNAL.pop(symbol, None)
        return False, COOLDOWN_LOW_SCORE
    cd = _adaptive_cooldown_bars(prev.get("score"))
    return (current_index - last_i) < cd, cd


def _entry_distance_from_last(symbol: str, entry: float) -> float:
    prev = _LAST_SIGNAL.get(symbol)
    if not prev:
        return float("nan")
    return abs(float(entry) - float(prev["entry"]))


def _entry_too_close(symbol: str, entry: float, atr_value: float) -> bool:
    prev = _LAST_SIGNAL.get(symbol)
    if not prev:
        return False
    if not np.isfinite(atr_value) or atr_value <= 0:
        return False
    return abs(float(entry) - float(prev["entry"])) < (ENTRY_NEAR_ATR_MULT * float(atr_value))


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
      - TP from liquidity only (E2); rr_min=0 so low RR is scored/gated later
    """
    if not np.isfinite(raw_entry) or not np.isfinite(atr_value) or atr_value <= 0:
        return None

    risk_cfg = cfg.get("risk", default={}) or {}
    costs_cfg = cfg.get("costs", default={}) or {}
    theta_sl = float(risk_cfg.get("theta_sl_atr", 0.15))

    entry, cost = apply_entry_costs(
        direction, float(raw_entry), float(atr_value), asset_class, costs_cfg
    )

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
    Deterministic evaluation at 5M close timestamp `ts` (V7 decision engine).
    Hard blocks: data_quality, insufficient_bars, atr_invalid,
    no_directional_bias (incl. regime oppose), low_volatility.
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
    ts = bundle.m5.df.index[i5]

    news_blocked = bool(news_blackout)
    session_ok = _session_ok(ts, asset_class, cfg)
    session_valid = _session_valid_london_ny(ts)

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

    # V7 regime intelligence (H1 + H4 swing sequences)
    regime = _detect_regime(bundle.h1.df, bundle.h4.df, i1, i4)
    regime_aligned = _regime_aligns(regime, direction)
    if _regime_opposes(regime, direction):
        return _no_trade(
            symbol,
            ts,
            "no_directional_bias",
            ch,
            meta={
                "regime": regime,
                "direction": direction,
                "bias_4h": st4.bias,
                "bias_1h": st1.bias,
            },
        )

    structure_shift = bool(st1.bos_count_in_bias >= 1)
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

    atr_percentile, volatility_state, low_vol = _atr_percentile_state(atr_15, i15)
    if low_vol:
        return _no_trade(
            symbol,
            ts,
            "low_volatility",
            ch,
            meta={
                "atr_percentile": atr_percentile,
                "volatility_state": volatility_state,
                "atr": atr_v,
            },
        )

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
    fallback_entry = False

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

    # No valid POI → ATR RiskPlan fallback (score -1, meta.fallback=True)
    if selected is None or plan is None:
        selected = _price_anchor_poi(direction, price, atr_v, ts, i15)
        raw_entry = _entry_in_poi(direction, selected, price)
        plan = _make_plan(
            direction, raw_entry, selected, levels, atr_v, asset_class, cfg, sl_ref
        )
        fallback_entry = True

    if plan is None:
        fb_entry = float(price)
        if direction == "LONG":
            fallback_sl = float(price) - float(atr_v)
            fallback_tp1 = float(price) + 2.0 * float(atr_v)
            fallback_tp2 = float(price) + 3.0 * float(atr_v)
        else:
            fallback_sl = float(price) + float(atr_v)
            fallback_tp1 = float(price) - 2.0 * float(atr_v)
            fallback_tp2 = float(price) - 3.0 * float(atr_v)
        risk_distance = abs(fb_entry - fallback_sl)
        plan = RiskPlan(
            direction=direction,
            entry=fb_entry,
            sl=fallback_sl,
            tp1=fallback_tp1,
            tp2=fallback_tp2,
            rr1=2.0,
            rr2=3.0,
            risk_distance=float(risk_distance),
            tp1_level_id="fallback",
            tp2_level_id="fallback",
            cost_applied=0.0,
        )
        fallback_entry = True
        if selected is None:
            selected = _price_anchor_poi(direction, price, atr_v, ts, i15)

    overlap_theta = float(cfg.get("scoring", "overlap_theta", default=0.25))
    selected_overlap = (
        0.0
        if fallback_entry or not valid_pois
        else _poi_overlap_score(selected, valid_pois, overlap_theta)
    )

    rr = float(plan.rr1)
    sweep_present = sweep_valid
    entry_distance_from_last = _entry_distance_from_last(symbol, float(plan.entry))
    range_hi, range_lo = _recent_range(bundle.h1.df, i1)
    mid_range = _mid_range_blocked(price, range_hi, range_lo)
    cooldown_applied, cooldown_bars = _cooldown_blocked(symbol, i5)
    entry_near = _entry_too_close(symbol, float(plan.entry), atr_v)

    # --- Scoring (extend existing factors; V7 refinements) ---
    score = 0
    if htf_aligned:
        score += 2
    if structure_shift:
        score += 1
    if confirm_ok:
        score += 1
    if sweep_present:
        score += 1

    # Regime
    if regime == "range":
        score -= 1
    elif regime_aligned:
        score += 1

    # PD refined: aligned +1, misaligned -1
    if pd_ok:
        score += 1
    else:
        score -= 1

    # RR quality tiers (replace flat +2/+1)
    if rr >= 3.0:
        score += 2
    elif rr >= 2.0:
        score += 1
    # 1.5 <= rr < 2 → +0

    if fallback_entry:
        score -= 1

    # Session soft filter (expanded windows): outside → -1, not hard reject
    if not session_valid:
        score -= 1

    print(
        {
            "ts": ts,
            "score": score,
            "rr": rr,
            "regime": regime,
            "atr_percentile": atr_percentile,
            "volatility_state": volatility_state,
            "cooldown_applied": cooldown_applied,
            "session_valid": session_valid,
            "fallback": fallback_entry,
            "pd": pd_ok,
        }
    )

    common_meta = {
        "regime": regime,
        "atr_percentile": atr_percentile,
        "volatility_state": volatility_state,
        "cooldown_applied": cooldown_applied,
        "cooldown_bars": cooldown_bars,
        "entry_distance_from_last": entry_distance_from_last,
        "session_valid": session_valid,
        "fallback": fallback_entry,
        "pd_ok": pd_ok,
        "score": score,
        "rr": rr,
        "strategy_version": "V7",
    }

    if rr < RR_HARD_MIN:
        return _no_trade(symbol, ts, "rr_below_1_5", ch, meta=common_meta)

    if cooldown_applied:
        return _no_trade(symbol, ts, "cooldown", ch, meta=common_meta)

    if entry_near or mid_range:
        return _no_trade(
            symbol,
            ts,
            "entry_quality",
            ch,
            meta={
                **common_meta,
                "entry_near": entry_near,
                "mid_range": mid_range,
                "range_hi": range_hi,
                "range_lo": range_lo,
            },
        )

    # Tiers: A+ = score≥4 & RR≥2 & regime aligned; A = score≥2
    if score >= 4 and rr >= 2.0 and regime_aligned:
        setup_type: Literal["A+", "A", "B"] = "A+"
    elif score >= 2:
        setup_type = "A"
    else:
        return _no_trade(symbol, ts, "low_score", ch, meta=common_meta)

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
        "f_session_valid": float(session_valid),
        "f_news_blocked": float(news_blocked),
        "f_poi_count": float(len(valid_pois)),
        "f_structure_shift": float(structure_shift),
        "f_price_fallback": float(fallback_entry),
        "f_regime_aligned": float(regime_aligned),
        "f_atr_percentile": float(atr_percentile) if np.isfinite(atr_percentile) else 0.0,
        "f_cooldown_applied": float(cooldown_applied),
    }

    validated = ["liquidity_tp"]
    if structure_shift:
        validated.append("structure_shift")
    if not fallback_entry:
        validated.append("poi_fvg_or_ob")
    else:
        validated.append("poi_price_fallback")
    if rr >= 3.0:
        validated.append("rr_ge_3")
    elif rr >= 2.0:
        validated.append("rr_ge_2")
    elif rr >= 1.5:
        validated.append("rr_ge_1_5")
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
    if regime_aligned:
        validated.append("regime_aligned")
    if session_valid:
        validated.append("session_london_ny")
    elif session_ok:
        validated.append("session_ok")

    flags = {
        "LiquiditySweep": sweep_valid,
        "POI": not fallback_entry,
        "Confirm5M": confirm_ok,
        "StructureShift": structure_shift,
        "HTFAlignment": htf_aligned,
        "PremiumDiscount": pd_ok,
        "OverlapFVGOB": selected_overlap >= overlap_theta,
        "MajorLiquidity": bool(sweep is not None and sweep.level.major),
        "RegimeAligned": regime_aligned,
    }

    decision_type: Literal["SIGNAL_LONG", "SIGNAL_SHORT"] = (
        "SIGNAL_LONG" if direction == "LONG" else "SIGNAL_SHORT"
    )
    if sweep is not None:
        dedupe_sweep_id = sweep.level.level_id
        dedupe_sweep_time = str(sweep.ts)
    else:
        dedupe_sweep_id = f"NOSWEEP:{selected.poi_id}"
        dedupe_sweep_time = str(ts)

    _record_signal(symbol, i5, direction, float(plan.entry), float(score))

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
        sweep_id=dedupe_sweep_id,
        poi_id=selected.poi_id,
        config_hash=ch,
        features=features,
        meta={
            "sweep_time": dedupe_sweep_time,
            "sweep_level_id": (sweep.level.level_id if sweep is not None else None),
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
            "poi_price_fallback": fallback_entry,
            "fallback_entry": fallback_entry,
            "fallback": fallback_entry,
            "poi_count": len(valid_pois),
            "rr": rr,
            "rr_score_target": RR_SCORE_TARGET,
            "rr_hard_min": RR_HARD_MIN,
            "session_ok": session_ok,
            "session_valid": session_valid,
            "regime": regime,
            "regime_aligned": regime_aligned,
            "atr_percentile": atr_percentile,
            "volatility_state": volatility_state,
            "cooldown_applied": cooldown_applied,
            "cooldown_bars": cooldown_bars,
            "entry_distance_from_last": entry_distance_from_last,
            "news_blocked": news_blocked,
            "strategy_version": "V7",
        },
    )
