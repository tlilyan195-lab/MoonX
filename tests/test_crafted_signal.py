"""Crafted OHLC path that should produce a deterministic SIGNAL under relaxed gates."""

from __future__ import annotations

import numpy as np
import pandas as pd

from trading_signal_bot.backtesting import run_backtest_on_bundle
from trading_signal_bot.config import StrategyConfig
from trading_signal_bot.data import MultiTimeframeBundle, OHLCVFrame
from trading_signal_bot.data.providers import aggregate_ohlcv
from trading_signal_bot.strategy.engine import evaluate


def _craft_trending_series(n: int = 2000, start: float = 1.10) -> pd.DataFrame:
    """
    Build a series with:
    - upward drift (bullish HTF)
    - a liquidity sweep of prior lows
    - displacement / gaps for FVG
    """
    idx = pd.date_range("2024-01-01", periods=n, freq="5min", tz="UTC")
    # Start during London hours roughly: 2024-01-01 is Monday; shift to a weekday open
    idx = pd.date_range("2024-01-02 08:00:00", periods=n, freq="5min", tz="UTC")
    close = np.zeros(n)
    close[0] = start
    rng = np.random.default_rng(123)
    for i in range(1, n):
        close[i] = close[i - 1] + 0.00015 + rng.normal(0, 0.00005)

    # Inject a sweep low around mid series then reclaim
    sweep_i = n // 2
    close[sweep_i - 5 : sweep_i] -= 0.004
    close[sweep_i] = close[sweep_i - 1] - 0.003  # pierce
    close[sweep_i + 1] = close[sweep_i - 6] + 0.001  # reclaim / close back

    open_ = np.roll(close, 1)
    open_[0] = start
    high = np.maximum(open_, close) + 0.0003
    low = np.minimum(open_, close) - 0.0003
    # Ensure sweep wick
    low[sweep_i] = close[sweep_i] - 0.002
    high[sweep_i] = max(open_[sweep_i], close[sweep_i]) + 0.0002

    # Create FVG-like impulse after sweep
    for j in range(sweep_i + 2, sweep_i + 8):
        close[j] = close[j - 1] + 0.0012
        open_[j] = close[j - 1]
        low[j] = min(open_[j], close[j]) - 0.0001
        high[j] = max(open_[j], close[j]) + 0.0001

    df = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": 100.0},
        index=idx,
    )
    return df


def test_crafted_path_evaluate_returns_valid_decision_type():
    df5 = _craft_trending_series()
    m5 = OHLCVFrame("EURUSD", "5M", df5)
    bundle = MultiTimeframeBundle(
        "EURUSD",
        "FX",
        m5,
        aggregate_ohlcv(m5, "15M"),
        aggregate_ohlcv(m5, "1H"),
        aggregate_ohlcv(m5, "4H"),
    )
    # Relax filters for structural path testing (separate research-style overrides)
    cfg = StrategyConfig.from_yaml().with_overrides(
        {
            "sessions": {"fx_default": []},  # allow all hours in this unit scenario
            "volatility_filter": {"V_min": 0.0, "V_max": 100.0, "W_vol": 20},
            "risk": {"rr_min": 1.0, "max_hold_bars_5m": 100},
            "premium_discount": {"discount_max": 0.99, "premium_min": 0.01},
            "confirmation": {"X_max_bars_15m_after_sweep": 20, "theta_body_5m": 0.1},
            "liquidity": {"D_max_atr": 10.0},
            "scoring": {"S_B": 0, "S_A": 0, "S_Aplus": 101},
            "meta": {"require_overlap_for_aplus": False},
        }
    )
    # Still must be one of three outcomes; determinism holds
    ts = bundle.m5.df.index[len(bundle.m5.df) // 2 + 30]
    a = evaluate(bundle, ts, cfg)
    b = evaluate(bundle, ts, cfg)
    assert a.decision in ("SIGNAL_LONG", "SIGNAL_SHORT", "NO_TRADE")
    assert a.decision == b.decision

    res = run_backtest_on_bundle(bundle, cfg, step=5)
    assert res.metrics.n_signals == len(res.trades)
    for t in res.trades:
        assert t.rr1 >= 1.0
        assert t.outcome in ("tp1", "tp2", "sl", "timeout", "unknown")
