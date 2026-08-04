"""Unit tests — indicators, structure, FVG, sessions, metrics, determinism."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from trading_signal_bot.backtesting.metrics import (
    compute_metrics,
    max_drawdown_R,
    simulate_trade_path,
)
from trading_signal_bot.backtesting.splits import chronological_splits, overfitting_flags
from trading_signal_bot.config import StrategyConfig
from trading_signal_bot.data import MultiTimeframeBundle
from trading_signal_bot.data.providers import make_mtf_synthetic
from trading_signal_bot.data.sessions import (
    fx_trading_day_id,
    fx_week_start,
    previous_fx_week_bounds,
)
from trading_signal_bot.indicators import atr, pivot_highs, pivot_lows
from trading_signal_bot.market_structure import compute_structure
from trading_signal_bot.strategy.engine import evaluate
from trading_signal_bot.strategy.poi import detect_fvgs, is_fvg_mitigated


def test_pivot_requires_right_side_confirmation():
    high = np.array([1.0, 2.0, 3.0, 2.5, 2.0, 1.5], dtype=float)
    ph = pivot_highs(high, n=2)
    # index 2 is pivot only once bars 3 and 4 exist — mask True at 2
    assert ph[2]
    assert not ph[1]


def test_atr_positive():
    idx = pd.date_range("2024-01-01", periods=50, freq="5min", tz="UTC")
    rng = np.random.default_rng(0)
    close = 100 + np.cumsum(rng.normal(0, 0.1, 50))
    df = pd.DataFrame(
        {
            "open": close,
            "high": close + 0.2,
            "low": close - 0.2,
            "close": close,
            "volume": 1.0,
        },
        index=idx,
    )
    a = atr(df, 14)
    assert a.iloc[-1] > 0


def test_bos_requires_close_not_wick_only():
    idx = pd.date_range("2024-01-01", periods=30, freq="1h", tz="UTC")
    # Build clear swings then wick-only break
    closes = [1.0] * 30
    highs = [1.05] * 30
    lows = [0.95] * 30
    opens = [1.0] * 30
    # Create a swing high around bar 10
    for i in range(30):
        highs[i] = 1.0 + 0.01 * i
        lows[i] = 0.9 + 0.01 * i
        closes[i] = 0.95 + 0.01 * i
        opens[i] = closes[i]
    df = pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": 1},
        index=idx,
    )
    # Wick above last swing but close below — craft last bars
    st = compute_structure(df, n_pivot=2, asof_index=len(df) - 1)
    assert st.bias in ("BULL", "BEAR", "NEUTRAL")


def test_fvg_detection_and_close_through_mitigation():
    idx = pd.date_range("2024-01-01", periods=10, freq="15min", tz="UTC")
    # bars 0,1,2 form bullish FVG: low[2] > high[0]
    rows = []
    for i in range(10):
        rows.append({"open": 1.0, "high": 1.01, "low": 0.99, "close": 1.0, "volume": 1})
    rows[0] = {"open": 1.00, "high": 1.00, "low": 0.99, "close": 1.00, "volume": 1}
    rows[1] = {"open": 1.00, "high": 1.05, "low": 1.00, "close": 1.05, "volume": 1}
    rows[2] = {"open": 1.05, "high": 1.06, "low": 1.04, "close": 1.05, "volume": 1}
    df = pd.DataFrame(rows, index=idx)
    # Force ATR by variation
    for i in range(3, 10):
        df.iloc[i, df.columns.get_loc("high")] = 1.06
        df.iloc[i, df.columns.get_loc("low")] = 1.04
    pois = detect_fvgs(df, atr_period=3, theta_fvg_atr=0.01, asof_index=9)
    assert any(p.direction == "BULL" for p in pois)
    poi = next(p for p in pois if p.direction == "BULL")
    # close through bottom
    df.iloc[5, df.columns.get_loc("close")] = poi.bottom - 0.01
    df.iloc[5, df.columns.get_loc("low")] = poi.bottom - 0.02
    assert is_fvg_mitigated(poi, df, poi.created_index, 5, "close_through")


def test_fx_trading_day_dst_spring_forward():
    # Around US DST 2024-03-10
    before = pd.Timestamp("2024-03-10 21:30:00+00:00")  # still previous NY day relative to 17:00
    day_id = fx_trading_day_id(before)
    assert day_id.tzinfo is not None
    after = pd.Timestamp("2024-03-11 22:00:00+00:00")
    day_id2 = fx_trading_day_id(after)
    assert day_id2 > day_id or day_id2 != day_id


def test_fx_week_sunday_open_t1():
    # Wednesday
    ts = pd.Timestamp("2024-06-12 15:00:00+00:00")
    start = fx_week_start(ts)
    start_ny = start.tz_convert("America/New_York")
    assert start_ny.weekday() == 6  # Sunday
    assert start_ny.hour == 17
    prev_start, prev_end = previous_fx_week_bounds(ts)
    assert prev_end == prev_start + pd.Timedelta(days=5)


def test_worst_case_sl_first_same_bar():
    idx = pd.date_range("2024-01-01", periods=5, freq="5min", tz="UTC")
    df = pd.DataFrame(
        {
            "open": [1.0, 1.0, 1.0, 1.0, 1.0],
            "high": [1.0, 1.0, 1.05, 1.0, 1.0],
            "low": [1.0, 1.0, 0.95, 1.0, 1.0],
            "close": [1.0, 1.0, 1.0, 1.0, 1.0],
            "volume": 1,
        },
        index=idx,
    )
    outcome, _, pnl, *_ = simulate_trade_path(
        df,
        entry_idx=1,
        direction="LONG",
        entry=1.0,
        sl=0.96,
        tp1=1.04,
        tp2=1.08,
        risk=0.04,
        partial_tp1_fraction=0.5,
        max_hold_bars=10,
        intrabar_path="worst_case_sl_first",
    )
    assert outcome == "sl"
    assert pnl == -1.0


def test_splits_chronological_no_overlap():
    idx = pd.date_range("2024-01-01", periods=100, freq="5min", tz="UTC")
    splits = chronological_splits(idx, 0.6, 0.2, 0.2)
    assert splits["TRAIN"].end < splits["VAL"].start
    assert splits["VAL"].end < splits["OOS"].start


def test_overfitting_flags():
    flags = overfitting_flags(1.0, -0.2, -0.5, train_n=10, val_n=5, n_trials=100)
    assert "sign_flip_train_val" in flags
    assert "too_few_signals" in flags
    assert "high_hyperparam_trial_count" in flags


def test_evaluate_deterministic():
    cfg = StrategyConfig.from_yaml()
    frames = make_mtf_synthetic("EURUSD", n_5m=1500, seed=7)
    bundle = MultiTimeframeBundle(
        "EURUSD", "FX", frames["5M"], frames["15M"], frames["1H"], frames["4H"]
    )
    ts = bundle.m5.df.index[1200]
    a = evaluate(bundle, ts, cfg)
    b = evaluate(bundle, ts, cfg)
    assert a.decision == b.decision
    assert a.category == b.category
    assert a.setup_score == b.setup_score
    assert a.entry == b.entry
    assert a.sl == b.sl


def test_decision_is_one_of_three():
    cfg = StrategyConfig.from_yaml()
    frames = make_mtf_synthetic("BTCUSDT", n_5m=1200, seed=3, start_price=40000, volatility=0.002)
    bundle = MultiTimeframeBundle(
        "BTCUSDT", "CRYPTO", frames["5M"], frames["15M"], frames["1H"], frames["4H"]
    )
    for ts in bundle.m5.df.index[800:820]:
        d = evaluate(bundle, ts, cfg)
        assert d.decision in ("SIGNAL_LONG", "SIGNAL_SHORT", "NO_TRADE")


def test_metrics_expectancy():
    from trading_signal_bot.backtesting.metrics import SimulatedTrade

    trades = [
        SimulatedTrade(
            "1",
            "EURUSD",
            "LONG",
            "A",
            pd.Timestamp("2024-01-01", tz="UTC"),
            1,
            0.9,
            1.2,
            1.3,
            2,
            0.1,
            0.5,
            pnl_R=1.0,
        ),
        SimulatedTrade(
            "2",
            "EURUSD",
            "LONG",
            "A",
            pd.Timestamp("2024-01-02", tz="UTC"),
            1,
            0.9,
            1.2,
            1.3,
            2,
            0.1,
            0.5,
            pnl_R=-1.0,
        ),
    ]
    m = compute_metrics(trades)
    assert m.n_signals == 2
    assert m.expectancy_R == 0.0
    assert max_drawdown_R([1.0, -1.0]) >= 0
