"""Deterministic OHLC fixtures guaranteeing SIGNAL_LONG / SIGNAL_SHORT."""

from __future__ import annotations

import numpy as np
import pandas as pd

from trading_signal_bot.config import StrategyConfig
from trading_signal_bot.data import MultiTimeframeBundle, OHLCVFrame
from trading_signal_bot.data.providers import aggregate_ohlcv
from trading_signal_bot.strategy.engine import evaluate


def golden_config(*, direction: str = "LONG") -> StrategyConfig:
    base = StrategyConfig.from_yaml()
    # Force PD zone for the intended direction while still executing the PD code path.
    if direction == "LONG":
        pd = {"discount_max": 100.0, "premium_min": 101.0}  # any finite pos → DISCOUNT
    else:
        pd = {"discount_max": -101.0, "premium_min": -100.0}  # any finite pos → PREMIUM
    return base.with_overrides(
        {
            "sessions": {"fx_default": []},
            "volatility_filter": {"V_min": 0.0, "V_max": 100.0, "W_vol": 5},
            "risk": {"rr_min": 1.0, "theta_sl_atr": 0.05, "max_hold_bars_5m": 120},
            "costs": {
                "fx_spread_atr_fraction": 0.0,
                "fx_slippage_atr_fraction": 0.0,
                "crypto_taker_fee_bps": 0.0,
                "crypto_slippage_atr_fraction": 0.0,
            },
            "premium_discount": pd,
            "confirmation": {
                "X_max_bars_15m_after_sweep": 60,
                "theta_body_5m": 0.15,
                "require_5m": True,
            },
            "liquidity": {
                "D_max_atr": 100.0,
                "epsilon_eq_atr": 0.35,
                "min_equal_separation_bars": 2,
            },
            "fvg": {"theta_fvg_atr": 0.001},
            "order_block": {"W_ob": 40},
            "scoring": {
                "S_B": 0,
                "S_A": 0,
                "S_Aplus": 100,
                "overlap_theta": 0.1,
                "weights": {
                    "f_overlap_fvg_ob": 0.01,
                    "f_deep_pd": 0.01,
                    "f_ote": 0.01,
                    "f_maj_liq": 0.01,
                    "f_mtf_fvg": 0.01,
                    "f_disp": 0.01,
                    "f_rr": 0.94,
                },
            },
            "meta": {
                "require_overlap_for_aplus": True,
                "poi_selection_mode": "best_rr_then_recency",
            },
            "pivots": {"N_4H": 2, "N_1H": 2, "N_15M": 2, "N_5M": 2},
            "structure": {"theta_body": 0.15, "theta_disp": 0.01, "L_bos": 500},
        }
    )


def _expand_bar(o: float, h: float, l: float, c: float, n: int = 48) -> list[tuple[float, float, float, float]]:
    """Path inside one HTF bar: open -> extreme -> close, n 5M bars."""
    out = []
    # go toward opposite extreme first then to close
    if c >= o:  # bullish HTF bar: open -> low -> high -> close
        mid1 = n // 3
        mid2 = 2 * n // 3
        for i in range(n):
            if i < mid1:
                px = o + (l - o) * (i + 1) / mid1
            elif i < mid2:
                px = l + (h - l) * (i - mid1 + 1) / (mid2 - mid1)
            else:
                px = h + (c - h) * (i - mid2 + 1) / (n - mid2)
            oo = o if i == 0 else out[-1][3]
            hi = max(oo, px, h if i >= mid1 else oo) 
            lo = min(oo, px, l if i < mid2 else oo)
            # keep within envelope
            hi = min(max(hi, oo, px), h + 1e-12)
            lo = max(min(lo, oo, px), l - 1e-12)
            out.append((oo, hi, lo, px))
    else:
        mid1 = n // 3
        mid2 = 2 * n // 3
        for i in range(n):
            if i < mid1:
                px = o + (h - o) * (i + 1) / mid1
            elif i < mid2:
                px = h + (l - h) * (i - mid1 + 1) / (mid2 - mid1)
            else:
                px = l + (c - l) * (i - mid2 + 1) / (n - mid2)
            oo = o if i == 0 else out[-1][3]
            hi = min(max(oo, px), h + 1e-12)
            lo = max(min(oo, px), l - 1e-12)
            out.append((oo, hi, lo, px))
    # Force exact OHLC envelope on the set
    return out


def _build_from_4h_swings(bull: bool, n_swings: int = 12) -> pd.DataFrame:
    """
    Build explicit 4H zigzag OHLC (pivots guaranteed), expand to 5M.
    Bull: HH/HL sequence with close breaks of prior swing highs.
    """
    bars_4h: list[tuple[float, float, float, float]] = []
    if bull:
        # Start and create rising swing structure
        # Each swing: 3 down-ish bars then 4 up bars breaking prior high
        price = 1.1000
        last_low = price - 0.002
        last_high = price + 0.002
        for s in range(n_swings):
            # pullback leg (2 bars)
            for _ in range(2):
                o = price
                c = price - 0.0015
                l = c - 0.0008
                h = o + 0.0004
                bars_4h.append((o, h, l, c))
                price = c
            last_low = min(last_low, price)
            # impulse leg (3 bars) breaking prior high
            for j in range(3):
                o = price
                c = price + 0.0025
                h = c + 0.0006
                l = o - 0.0003
                # ensure break of last_high on last impulse bar of swing
                if j == 2:
                    c = max(c, last_high + 0.0010)
                    h = c + 0.0005
                bars_4h.append((o, h, l, c))
                price = c
            last_high = max(last_high, price)
    else:
        price = 1.1200
        last_high = price + 0.002
        last_low = price - 0.002
        for s in range(n_swings):
            for _ in range(2):
                o = price
                c = price + 0.0015
                h = c + 0.0008
                l = o - 0.0004
                bars_4h.append((o, h, l, c))
                price = c
            last_high = max(last_high, price)
            for j in range(3):
                o = price
                c = price - 0.0025
                l = c - 0.0006
                h = o + 0.0003
                if j == 2:
                    c = min(c, last_low - 0.0010)
                    l = c - 0.0005
                bars_4h.append((o, h, l, c))
                price = c
            last_low = min(last_low, price)

    rows = []
    for o, h, l, c in bars_4h:
        rows.extend(_expand_bar(o, h, l, c, n=48))
    idx = pd.date_range("2024-01-08 00:00:00", periods=len(rows), freq="5min", tz="UTC")
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)
    df["volume"] = 100.0
    # consistency
    df["high"] = df[["open", "high", "close"]].max(axis=1)
    df["low"] = df[["open", "low", "close"]].min(axis=1)
    return df


def _plant_5m_bos(df: pd.DataFrame, confirm: int, bull: bool, theta_body: float = 0.5) -> None:
    """
    Plant a confirmed 5M swing then a close-break BOS/CHoCH on `confirm`
    with body/range >= theta_body on the event candle itself.
    N_pivot=2 ⇒ swing at confirm-3 must be extremum vs confirm-5..confirm-1,
    confirmed when confirm-1 exists; break on `confirm`.
    """
    # Build flat-ish base then swing
    base = float(df.iloc[confirm - 8]["close"])
    if bull:
        # pivot high at confirm-3
        ph = base + 0.0010
        for i, px in {
            confirm - 7: base - 0.0006,
            confirm - 6: base - 0.0002,
            confirm - 5: base + 0.0002,
            confirm - 4: base + 0.0006,
            confirm - 3: ph,  # pivot high
            confirm - 2: base + 0.0004,
            confirm - 1: base + 0.0002,  # right side lower → confirms PH
        }.items():
            o = px - 0.00005
            df.iloc[i] = [o, px + 0.00005, o - 0.00005, px, 100.0]
        # Also need a pivot low earlier so structure has both swings
        pl_i = confirm - 10
        pl = base - 0.0015
        df.iloc[pl_i] = [pl + 0.0002, pl + 0.0004, pl, pl + 0.0001, 100.0]
        df.iloc[pl_i - 1] = [pl + 0.0005, pl + 0.0007, pl + 0.0003, pl + 0.0004, 100.0]
        df.iloc[pl_i + 1] = [pl + 0.0003, pl + 0.0005, pl + 0.0001, pl + 0.0004, 100.0]
        # BOS candle: close above PH with strong body
        o = ph - 0.0001
        c = ph + 0.0012
        h = c + 0.00005
        l = o - 0.00005
        # body/range check
        assert abs(c - o) / (h - l) >= theta_body
        df.iloc[confirm] = [o, h, l, c, 100.0]
    else:
        pl = base - 0.0010
        for i, px in {
            confirm - 7: base + 0.0006,
            confirm - 6: base + 0.0002,
            confirm - 5: base - 0.0002,
            confirm - 4: base - 0.0006,
            confirm - 3: pl,
            confirm - 2: base - 0.0004,
            confirm - 1: base - 0.0002,
        }.items():
            o = px + 0.00005
            df.iloc[i] = [o, o + 0.00005, px - 0.00005, px, 100.0]
        ph_i = confirm - 10
        ph = base + 0.0015
        df.iloc[ph_i] = [ph - 0.0002, ph, ph - 0.0004, ph - 0.0001, 100.0]
        df.iloc[ph_i - 1] = [ph - 0.0005, ph - 0.0003, ph - 0.0007, ph - 0.0004, 100.0]
        df.iloc[ph_i + 1] = [ph - 0.0003, ph - 0.0001, ph - 0.0005, ph - 0.0004, 100.0]
        o = pl + 0.0001
        c = pl - 0.0012
        h = o + 0.00005
        l = c - 0.00005
        assert abs(c - o) / (h - l) >= theta_body
        df.iloc[confirm] = [o, h, l, c, 100.0]

    df["high"] = df[["open", "high", "close"]].max(axis=1)
    df["low"] = df[["open", "low", "close"]].min(axis=1)


def _inject_long(df: pd.DataFrame, anchor: int) -> int:
    plant = anchor - 80
    lvl = float(df["low"].iloc[plant : plant + 15].min()) - 0.0004
    df.iloc[plant, df.columns.get_loc("low")] = lvl
    df.iloc[plant + 12, df.columns.get_loc("low")] = lvl + 0.00002

    sweep = anchor
    df.iloc[sweep, df.columns.get_loc("low")] = lvl - 0.0015
    df.iloc[sweep, df.columns.get_loc("close")] = lvl + 0.0007
    df.iloc[sweep, df.columns.get_loc("open")] = lvl + 0.0002
    df.iloc[sweep, df.columns.get_loc("high")] = lvl + 0.0009

    base = float(df.iloc[sweep]["close"])
    for k in range(1, 12):
        i = sweep + k
        o = base + 0.0009 * (k - 1)
        c = base + 0.0009 * k
        df.iloc[i] = [o, c + 0.00005, o - 0.00005, c, 100.0]

    for k in range(12, 16):
        i = sweep + k
        o = float(df.iloc[i - 1]["close"])
        c = o - 0.0004
        df.iloc[i] = [o, o + 0.0001, c - 0.0001, c, 100.0]

    df.iloc[anchor - 250 : anchor - 230, df.columns.get_loc("high")] = base + 0.025

    confirm = sweep + 20
    _plant_5m_bos(df, confirm, bull=True, theta_body=0.5)
    df["high"] = df[["open", "high", "close"]].max(axis=1)
    df["low"] = df[["open", "low", "close"]].min(axis=1)
    return confirm


def _inject_short(df: pd.DataFrame, anchor: int) -> int:
    plant = anchor - 80
    lvl = float(df["high"].iloc[plant : plant + 15].max()) + 0.0004
    df.iloc[plant, df.columns.get_loc("high")] = lvl
    df.iloc[plant + 12, df.columns.get_loc("high")] = lvl - 0.00002

    sweep = anchor
    df.iloc[sweep, df.columns.get_loc("high")] = lvl + 0.0015
    df.iloc[sweep, df.columns.get_loc("close")] = lvl - 0.0007
    df.iloc[sweep, df.columns.get_loc("open")] = lvl - 0.0002
    df.iloc[sweep, df.columns.get_loc("low")] = lvl - 0.0009

    base = float(df.iloc[sweep]["close"])
    for k in range(1, 12):
        i = sweep + k
        o = base - 0.0009 * (k - 1)
        c = base - 0.0009 * k
        df.iloc[i] = [o, o + 0.00005, c - 0.00005, c, 100.0]

    for k in range(12, 16):
        i = sweep + k
        o = float(df.iloc[i - 1]["close"])
        c = o + 0.0004
        df.iloc[i] = [o, c + 0.0001, o - 0.0001, c, 100.0]

    df.iloc[anchor - 250 : anchor - 230, df.columns.get_loc("low")] = base - 0.025

    confirm = sweep + 20
    _plant_5m_bos(df, confirm, bull=False, theta_body=0.5)
    df["high"] = df[["open", "high", "close"]].max(axis=1)
    df["low"] = df[["open", "low", "close"]].min(axis=1)
    return confirm


def _bundle(df: pd.DataFrame) -> MultiTimeframeBundle:
    m5 = OHLCVFrame("EURUSD", "5M", df)
    return MultiTimeframeBundle(
        "EURUSD",
        "FX",
        m5,
        aggregate_ohlcv(m5, "15M"),
        aggregate_ohlcv(m5, "1H"),
        aggregate_ohlcv(m5, "4H"),
    )


def _find_signal(bundle, cfg, direction, lo, hi) -> pd.Timestamp:
    want = "SIGNAL_LONG" if direction == "LONG" else "SIGNAL_SHORT"
    idx = bundle.m5.df.index
    reasons: dict[str, int] = {}
    for i in range(lo, min(hi, len(idx))):
        d = evaluate(bundle, idx[i], cfg)
        if d.decision == want:
            return idx[i]
        reasons[d.explanation or d.decision] = reasons.get(d.explanation or d.decision, 0) + 1
    raise AssertionError(
        f"Golden fixture failed to produce {want} in [{lo},{hi}). "
        f"Top reasons: {sorted(reasons.items(), key=lambda x: -x[1])[:12]}"
    )


def build_golden_long_bundle():
    df = _build_from_4h_swings(bull=True, n_swings=14)
    anchor = int(len(df) * 0.82)
    confirm = _inject_long(df, anchor)
    cfg = golden_config(direction="LONG")
    bundle = _bundle(df)
    # Verify HTF bias first for clearer errors
    from trading_signal_bot.market_structure import compute_structure
    from trading_signal_bot.strategy.engine import _asof_index

    ts0 = df.index[confirm]
    i1 = _asof_index(bundle.h1.df, ts0)
    i4 = _asof_index(bundle.h4.df, ts0)
    st1 = compute_structure(bundle.h1.df, 2, asof_index=i1)
    st4 = compute_structure(bundle.h4.df, 2, asof_index=i4)
    if st4.bias != "BULL" or st1.bias != "BULL":
        raise AssertionError(
            f"Golden LONG HTF not bullish: 4H={st4.bias}/{len(st4.events)} "
            f"1H={st1.bias}/{len(st1.events)} bos1={st1.bos_count_in_bias}"
        )
    ts = _find_signal(bundle, cfg, "LONG", confirm - 10, confirm + 50)
    return bundle, ts, cfg


def build_golden_short_bundle():
    df = _build_from_4h_swings(bull=False, n_swings=14)
    anchor = int(len(df) * 0.82)
    confirm = _inject_short(df, anchor)
    cfg = golden_config(direction="SHORT")
    bundle = _bundle(df)
    from trading_signal_bot.market_structure import compute_structure
    from trading_signal_bot.strategy.engine import _asof_index

    ts0 = df.index[confirm]
    i1 = _asof_index(bundle.h1.df, ts0)
    i4 = _asof_index(bundle.h4.df, ts0)
    st1 = compute_structure(bundle.h1.df, 2, asof_index=i1)
    st4 = compute_structure(bundle.h4.df, 2, asof_index=i4)
    if st4.bias != "BEAR" or st1.bias != "BEAR":
        raise AssertionError(
            f"Golden SHORT HTF not bearish: 4H={st4.bias}/{len(st4.events)} "
            f"1H={st1.bias}/{len(st1.events)} bos1={st1.bos_count_in_bias}"
        )
    ts = _find_signal(bundle, cfg, "SHORT", confirm - 10, confirm + 50)
    return bundle, ts, cfg
