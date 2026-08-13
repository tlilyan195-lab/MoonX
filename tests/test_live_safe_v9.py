"""Unit tests for V9 LIVE SAFE filters and risk (no strategy scoring changes)."""

from __future__ import annotations

import pandas as pd

from trading_signal_bot.live_safe.filters import (
    LiveSafeConfig,
    apply_trade_filters,
    check_oos_live_ready,
    compute_confidence_score,
    filter_symbol_performance,
)
from trading_signal_bot.live_safe.risk import LiveRiskManager
from trading_signal_bot.signals import SignalDecision


def _signal(**kwargs) -> SignalDecision:
    base = dict(
        decision="SIGNAL_LONG",
        symbol="EURUSD",
        ts_utc=pd.Timestamp("2024-01-01", tz="UTC"),
        direction="LONG",
        category="A+",
        setup_score=4.0,
        entry=1.1,
        sl=1.09,
        tp1=1.12,
        rr1=2.5,
        meta={"regime": "trend_up", "volatility_state": "low", "score": 4, "rr": 2.5},
    )
    base.update(kwargs)
    return SignalDecision(**base)


def test_symbol_filter_rejects_weak_val():
    cfg = LiveSafeConfig()
    # BTC-like weak VAL
    bad = filter_symbol_performance(
        "BTCUSDT",
        {"expectancy_R": 0.1, "max_drawdown_R": 5.0},
        {"p_exp_le_0": 0.55},
        cfg=cfg,
    )
    assert not bad.allowed
    assert any("expectancy" in r for r in bad.reasons)

    good = filter_symbol_performance(
        "EURUSD",
        {"expectancy_R": 0.45, "max_drawdown_R": 8.0},
        {"p_exp_le_0": 0.2},
        cfg=cfg,
    )
    assert good.allowed


def test_symbol_filter_drawdown_and_mc():
    cfg = LiveSafeConfig()
    dd = filter_symbol_performance(
        "XAUUSD",
        {"expectancy_R": 0.5, "max_drawdown_R": 16.0},
        {"p_exp_le_0": 0.1},
        cfg=cfg,
    )
    assert not dd.allowed
    mc = filter_symbol_performance(
        "XAUUSD",
        {"expectancy_R": 0.5, "max_drawdown_R": 5.0},
        {"p_exp_le_0": 0.5},
        cfg=cfg,
    )
    assert not mc.allowed


def test_symbol_filter_rejects_negative_train():
    cfg = LiveSafeConfig()
    # BTC-like: VAL looks ok but TRAIN is deeply negative
    bad = filter_symbol_performance(
        "BTCUSDT",
        {"expectancy_R": 0.35, "max_drawdown_R": 4.0, "n_signals": 20},
        {"p_exp_le_0": 0.2},
        cfg=cfg,
        train_metrics={"expectancy_R": -0.67, "max_drawdown_R": 8.0, "n_signals": 21},
    )
    assert not bad.allowed
    assert any("TRAIN_expectancy" in r for r in bad.reasons)


def test_oos_live_ready_gate():
    ok, _ = check_oos_live_ready({"expectancy_R": 0.2, "n_signals": 40})
    assert ok
    bad, reasons = check_oos_live_ready({"expectancy_R": -0.1, "n_signals": 40})
    assert not bad
    assert any("expectancy" in r for r in reasons)
    sparse, reasons2 = check_oos_live_ready({"expectancy_R": 0.5, "n_signals": 10})
    assert not sparse
    assert any("n_signals" in r for r in reasons2)


def test_confidence_requires_aplus_and_score():
    # Category A blocked
    a = apply_trade_filters(_signal(category="A", setup_score=3), market_regime="LOW_VOL|TRENDING")
    assert not a.allowed
    assert any("A+" in r for r in a.reasons)

    # A+ with strong regime bonuses passes
    ap = apply_trade_filters(
        _signal(category="A+", setup_score=2, rr1=2.5),
        market_regime="LOW_VOL|TRENDING",
        by_regime={"LOW_VOL|TRENDING": {"expectancy_R": 0.8, "win_rate": 0.6}},
    )
    assert ap.allowed
    assert ap.confidence_score >= 3


def test_regime_high_vol_range_block():
    filt = apply_trade_filters(
        _signal(),
        market_regime="HIGH_VOL|RANGE",
        by_regime={"HIGH_VOL|RANGE": {"n": 5.0, "expectancy_R": 0.2, "win_rate": 0.4}},
    )
    assert not filt.allowed
    assert any("HIGH_VOL|RANGE" in r for r in filt.reasons)


def test_regime_unknown_stats_do_not_auto_block():
    filt = apply_trade_filters(
        _signal(category="A+", setup_score=4, rr1=2.5),
        market_regime="HIGH_VOL|RANGE",
        by_regime=None,
    )
    # No known regime expectancy → do not regime-block; A+ + score may still pass
    assert not any("regime_block" in r for r in filt.reasons)


def test_risk_drawdown_and_streak_pause():
    mgr = LiveRiskManager(capital=100_000.0)
    assert mgr.gate().allowed
    assert mgr.current_risk_pct() == 0.5

    # Build drawdown > 10R
    mgr.equity_r = -11.0
    mgr.peak_r = 0.0
    assert mgr.current_risk_pct() == 0.25

    mgr.equity_r = -21.0
    g = mgr.gate()
    assert not g.allowed
    assert mgr.stopped or "STOP" in g.reason

    mgr2 = LiveRiskManager(capital=100_000.0)
    for _ in range(6):
        mgr2.on_trade_closed(-1.0)
    assert mgr2.pause_remaining == 10
    blocked = mgr2.on_trade_attempt()
    assert not blocked.allowed


def test_confidence_score_bonuses():
    d = _signal(setup_score=1, rr1=2.5, meta={"regime": "trend_up", "volatility_state": "low", "score": 1})
    score, notes = compute_confidence_score(
        d,
        "LOW_VOL|TRENDING",
        regime_winrate=0.6,
    )
    assert score >= 4  # base 1 + 4 bonuses
    assert any("LOW_VOL" in n for n in notes)
