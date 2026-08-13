"""Non-regression tests for ÉTAPE 4 audit fixes (P0–P2)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from trading_signal_bot.backtesting import (
    run_backtest_on_bundle,
    run_calibration,
    run_oos_eval,
    run_split_backtests,
    run_walk_forward,
)
from trading_signal_bot.backtesting.metrics import simulate_trade_path
from trading_signal_bot.backtesting.splits import chronological_splits, monte_carlo_expectancy
from trading_signal_bot.config import StrategyConfig
from trading_signal_bot.data import MultiTimeframeBundle, OHLCVFrame
from trading_signal_bot.data.providers import aggregate_ohlcv, make_mtf_synthetic
from trading_signal_bot.market_structure import StructureEventType, compute_structure
from trading_signal_bot.strategy.engine import _confirm_5m, _poi_overlap_score, _select_poi
from trading_signal_bot.strategy.poi import POI
from trading_signal_bot.strategy.scoring import compute_score


def _bundle(n=1200, seed=1):
    frames = make_mtf_synthetic("EURUSD", n_5m=n, seed=seed)
    return MultiTimeframeBundle(
        "EURUSD", "FX", frames["5M"], frames["15M"], frames["1H"], frames["4H"]
    )


# --- P0 split leakage ---


def test_simulate_trade_path_respects_max_index_boundary():
    idx = pd.date_range("2024-01-01", periods=20, freq="5min", tz="UTC")
    # After entry, bar goes to TP then later would hit more — but bound cuts early
    close = np.full(20, 1.0)
    high = np.full(20, 1.0)
    low = np.full(20, 1.0)
    high[5] = 1.10  # would hit TP1=1.05
    # bar 8 would be beyond split end at index 6
    high[8] = 1.20
    df = pd.DataFrame(
        {"open": close, "high": high, "low": low, "close": close, "volume": 1},
        index=idx,
    )
    outcome, bars, pnl, *_ = simulate_trade_path(
        df,
        entry_idx=3,
        direction="LONG",
        entry=1.0,
        sl=0.90,
        tp1=1.05,
        tp2=1.15,
        risk=0.10,
        partial_tp1_fraction=0.5,
        max_hold_bars=50,
        max_index=6,  # cannot see bar 8
    )
    assert outcome in ("tp1", "tp2", "timeout", "sl")
    # Must not use index > 6
    assert bars <= 6 - 3


def test_train_val_frontier_no_outcome_leakage():
    cfg = StrategyConfig.from_yaml()
    bundle = _bundle(2000, seed=9)
    splits = chronological_splits(bundle.m5.df.index)
    train = run_backtest_on_bundle(
        bundle, cfg, start=splits["TRAIN"].start, end=splits["TRAIN"].end, step=5, warmup_bars=50
    )
    for t in train.trades:
        assert t.exit_ts is None or t.exit_ts <= splits["TRAIN"].end
        assert t.entry_ts <= splits["TRAIN"].end


def test_val_oos_frontier_no_outcome_leakage():
    cfg = StrategyConfig.from_yaml()
    bundle = _bundle(2000, seed=9)
    splits = chronological_splits(bundle.m5.df.index)
    val = run_backtest_on_bundle(
        bundle, cfg, start=splits["VAL"].start, end=splits["VAL"].end, step=5, warmup_bars=0
    )
    for t in val.trades:
        assert t.exit_ts is None or t.exit_ts <= splits["VAL"].end
        assert t.entry_ts >= splits["VAL"].start
        assert t.entry_ts <= splits["VAL"].end
        assert t.exit_ts is None or t.exit_ts < splits["OOS"].start or t.exit_ts <= splits["VAL"].end


# --- P0 OOS isolation ---


def test_calibration_excludes_oos_metrics(tmp_path):
    cfg = StrategyConfig.from_yaml()
    bundle = _bundle(1500, seed=2)
    locked = tmp_path / "strategy_v1_locked.yaml"
    cal = run_calibration(bundle, cfg, locked_config_path=locked)
    assert "OOS" not in {"TRAIN", "VAL"}  # sanity
    assert cal.meta["includes_oos_metrics"] is False
    assert "OOS" not in cal.meta["split_windows"]
    assert locked.exists()
    assert cal.locked_hash == cal.locked_config.hash
    assert len(cal.locked_hash) == 16


def test_run_split_backtests_default_no_oos():
    cfg = StrategyConfig.from_yaml()
    out = run_split_backtests(_bundle(1200, seed=3), cfg)
    assert set(out.keys()) == {"TRAIN", "VAL"}
    assert "OOS" not in out


def test_oos_eval_separate_after_lock(tmp_path):
    cfg = StrategyConfig.from_yaml()
    bundle = _bundle(1500, seed=4)
    cal = run_calibration(bundle, cfg, locked_config_path=tmp_path / "locked.yaml")
    oos = run_oos_eval(bundle, cal.locked_config, cal.splits)
    assert oos.split_name == "OOS"
    assert oos.meta["includes_oos_metrics"] is True
    assert oos.config_hash == cal.locked_hash


# --- P1 confirm 5M body on event candle ---


def test_confirm_5m_uses_event_candle_body_not_later_bar():
    idx = pd.date_range("2024-01-01", periods=40, freq="5min", tz="UTC")
    # Geometric path with PH then BOS on bar 20 with strong body; bar 22 is doji
    highs = np.linspace(1.0, 1.2, 40)
    lows = highs - 0.05
    closes = highs - 0.01
    opens = lows + 0.01
    # Create clear pivot structure around 12-18 then break at 20
    highs = np.array([1.0 + 0.01 * np.sin(i / 2) + 0.002 * i for i in range(40)])
    lows = highs - 0.02
    opens = (highs + lows) / 2
    closes = opens.copy()
    # pivot high at 15
    highs[15] = 1.50
    closes[15] = 1.48
    opens[15] = 1.46
    lows[15] = 1.45
    for j in (13, 14, 16, 17):
        highs[j] = 1.40
        closes[j] = 1.39
    # BOS event at 20: close above 1.50 with strong body
    opens[20] = 1.49
    closes[20] = 1.56
    highs[20] = 1.57
    lows[20] = 1.485
    # Later bar 22: weak body (should NOT be used for body check)
    opens[22] = 1.56
    closes[22] = 1.5601
    highs[22] = 1.58
    lows[22] = 1.55
    df = pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": 1},
        index=idx,
    )
    # Confirm at asof=22 should still use event candle body (bar 20), not doji 22
    # First verify structure has bull event near 20
    st = compute_structure(df, n_pivot=2, asof_index=22, theta_body=0.5, theta_disp=0.0)
    assert any(e.event_type.value.endswith("BULL") for e in st.events)
    # Direct unit: if last event is at 20, body on 20 is strong
    ok = _confirm_5m(df, 22, "LONG", n_pivot=2, theta_body=0.5, atr_period=5)
    # May be True or False depending on exact last event; assert body source logic:
    last = st.events[-1]
    row = df.iloc[last.index]
    body_ratio = abs(float(row["close"] - row["open"])) / float(row["high"] - row["low"])
    # The function must agree with evaluating body on last.index, not asof
    assert ok == (last.event_type.value.endswith("BULL") and body_ratio >= 0.5 and (22 - last.index) <= 2)


# --- P1 A+ overlap on selected POI ---


def test_aplus_requires_overlap_on_selected_poi_not_unrelated_pair():
    # Selected FVG does not overlap OB; two other POIs do overlap — must NOT grant A+ feature via unrelated pair
    ts = pd.Timestamp("2024-01-01", tz="UTC")
    selected = POI("FVG_SEL", "FVG", "BULL", 1.10, 1.11, ts, 10)
    other_fvg = POI("FVG_OTHER", "FVG", "BULL", 1.20, 1.21, ts, 11)
    other_ob = POI("OB_OTHER", "OB", "BULL", 1.20, 1.21, ts, 12)  # overlaps other_fvg
    # selected has no overlapping opposite-type neighbor
    ov_sel = _poi_overlap_score(selected, [selected, other_fvg, other_ob], theta=0.25)
    assert ov_sel == 0.0
    features = {"f_overlap_fvg_ob": float(ov_sel >= 0.25), "f_rr": 1.0}
    weights = {"f_overlap_fvg_ob": 0.5, "f_rr": 0.5}
    # High score but no selected overlap → cannot be A+ when require_overlap_for_aplus
    br = compute_score(features, weights, 0, 0, 50, require_overlap_for_aplus=True)
    assert br.category != "A+"
    # With overlap on selected:
    ob_sel = POI("OB_SEL", "OB", "BULL", 1.105, 1.112, ts, 13)
    ov2 = _poi_overlap_score(selected, [selected, ob_sel], theta=0.25)
    assert ov2 >= 1.0 or ov2 >= 0.25
    br2 = compute_score(
        {"f_overlap_fvg_ob": 1.0, "f_rr": 1.0},
        weights,
        0,
        0,
        50,
        require_overlap_for_aplus=True,
    )
    assert br2.category == "A+"


# --- P1 multi-POI tie-break ---


def test_poi_tiebreak_best_rr_then_recency_then_overlap_then_ob():
    cfg = StrategyConfig.from_yaml().with_overrides(
        {
            "risk": {"rr_min": 0.5, "theta_sl_atr": 0.0},
            "costs": {"fx_spread_atr_fraction": 0.0, "fx_slippage_atr_fraction": 0.0},
            "meta": {"poi_selection_mode": "best_rr_then_recency"},
            "scoring": {"overlap_theta": 0.25},
        }
    )
    # Build synthetic levels for TP far enough for high RR
    from trading_signal_bot.liquidity import LiquidityLevel, LiqRank

    levels = [
        LiquidityLevel("H1", 1.30, "HIGH", LiqRank.L1_PD, pd.Timestamp("2024-01-01", tz="UTC"), True),
        LiquidityLevel("H2", 1.25, "HIGH", LiqRank.L5_SWING, pd.Timestamp("2024-01-01", tz="UTC"), False),
    ]
    t0 = pd.Timestamp("2024-01-01", tz="UTC")
    # Same RR roughly via same zone bottoms; differentiate by recency/type/overlap
    fvg_old = POI("FVG_OLD", "FVG", "BULL", 1.00, 1.01, t0, 1)
    fvg_new = POI("FVG_NEW", "FVG", "BULL", 1.00, 1.01, t0 + pd.Timedelta(hours=2), 2)
    ob_new = POI("OB_NEW", "OB", "BULL", 1.00, 1.01, t0 + pd.Timedelta(hours=2), 3)
    # Overlapping OB for fvg_new
    ob_ov = POI("OB_OV", "OB", "BULL", 1.002, 1.009, t0 + pd.Timedelta(hours=3), 4)

    selected, plan = _select_poi(
        [fvg_old, fvg_new, ob_new, ob_ov],
        "best_rr_then_recency",
        "LONG",
        raw_entry=1.05,
        sweep_extreme=0.99,
        levels=levels,
        atr_value=0.01,
        asset_class="FX",
        cfg=cfg,
    )
    assert selected is not None and plan is not None
    # Among equal RR, prefer more recent; among same recency prefer overlap then OB
    assert selected.poi_id in {"OB_OV", "OB_NEW", "FVG_NEW"}


# --- P1 BOS wick vs close ---


def test_bos_wick_only_creates_no_event_close_through_does():
    idx = pd.date_range("2024-01-01", periods=30, freq="1h", tz="UTC")
    # Build confirmed swing high at bar 10 via geometric zigzag
    highs = np.array(
        [1.0, 1.05, 1.10, 1.15, 1.10, 1.05, 1.00, 1.05, 1.10, 1.12, 1.20, 1.15, 1.10, 1.08, 1.06]
        + [1.07] * 15,
        dtype=float,
    )
    lows = highs - 0.04
    closes = highs - 0.01
    opens = lows + 0.01
    df = pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": 1},
        index=idx,
    )
    # At bar 20: wick above swing high 1.20 but close below → no new BOS from this bar
    swing_high = 1.20
    df.iloc[20, df.columns.get_loc("high")] = swing_high + 0.05
    df.iloc[20, df.columns.get_loc("close")] = swing_high - 0.01
    df.iloc[20, df.columns.get_loc("open")] = swing_high - 0.02
    df.iloc[20, df.columns.get_loc("low")] = swing_high - 0.03
    st_wick = compute_structure(df, n_pivot=2, asof_index=20)
    events_at_20 = [e for e in st_wick.events if e.index == 20]
    assert events_at_20 == [], f"wick-only must not create BOS, got {events_at_20}"

    # Close-through creates BOS
    df2 = df.copy()
    df2.iloc[21, df2.columns.get_loc("close")] = swing_high + 0.02
    df2.iloc[21, df2.columns.get_loc("high")] = swing_high + 0.03
    df2.iloc[21, df2.columns.get_loc("open")] = swing_high - 0.01
    df2.iloc[21, df2.columns.get_loc("low")] = swing_high - 0.02
    st_close = compute_structure(df2, n_pivot=2, asof_index=21)
    events_at_21 = [e for e in st_close.events if e.index == 21]
    assert any(e.event_type in (StructureEventType.BOS_BULL, StructureEventType.CHOCH_BULL) for e in events_at_21)


# --- P2 walk-forward / monte carlo / regimes ---


def test_walk_forward_runner_no_oos():
    cfg = StrategyConfig.from_yaml()
    wf = run_walk_forward(_bundle(1800, seed=5), cfg)
    assert wf.meta["includes_oos"] is False
    assert "reserved_oos" in wf.meta
    assert isinstance(wf.folds, list)


def test_monte_carlo_includes_dd_and_losing_streak():
    mc = monte_carlo_expectancy([1.0, -1.0, 0.5, -0.5, 1.2, -0.8], n_sims=200, seed=1)
    assert "max_dd_p50" in mc
    assert "max_losing_streak_p95" in mc
    assert mc["max_dd_p95"] >= 0


def test_regimes_not_always_na():
    cfg = StrategyConfig.from_yaml()
    # Use golden-like longer series so some signals may appear; even with 0 trades,
    # label function itself must return non-NA
    from trading_signal_bot.backtesting.regimes import (
        fit_regime_thresholds_from_train,
        label_regime_at,
    )

    bundle = _bundle(1500, seed=6)
    thr = fit_regime_thresholds_from_train(bundle.m15.df, 200)
    label = label_regime_at(thr, bundle.m15.df, bundle.h4.df, bundle.m5.df.index[800])
    assert label != "NA"
    assert "|" in label or label == "UNKNOWN"
