"""Integration tests — backtest engine, splits, variant isolation."""

from __future__ import annotations

from trading_signal_bot.backtesting import (
    compare_variant_runs,
    run_backtest_on_bundle,
    run_split_backtests,
)
from trading_signal_bot.config import StrategyConfig
from trading_signal_bot.data import MultiTimeframeBundle
from trading_signal_bot.data.providers import make_mtf_synthetic


def _bundle(symbol: str = "EURUSD", seed: int = 11, n: int = 2500) -> MultiTimeframeBundle:
    frames = make_mtf_synthetic(symbol, n_5m=n, seed=seed)
    asset = "CRYPTO" if symbol.endswith("USDT") else "FX"
    return MultiTimeframeBundle(
        symbol, asset, frames["5M"], frames["15M"], frames["1H"], frames["4H"]  # type: ignore[arg-type]
    )


def test_backtest_runs_and_returns_metrics():
    cfg = StrategyConfig.from_yaml()
    res = run_backtest_on_bundle(_bundle(), cfg, step=3)
    assert res.config_hash == cfg.hash
    assert res.metrics.n_signals == len(res.trades)
    # May be zero signals on random walk — still valid NO_TRADE-heavy outcome
    assert res.metrics.win_rate >= 0.0
    assert res.metrics.win_rate <= 1.0


def test_train_val_oos_separate():
    cfg = StrategyConfig.from_yaml()
    results = run_split_backtests(_bundle(n=3000), cfg)
    assert set(results) == {"TRAIN", "VAL", "OOS"}
    # Ensure meta carries anti-overfit diagnostics
    assert "overfitting_flags" in results["VAL"].meta
    assert "val_monte_carlo" in results["VAL"].meta


def test_variant_runs_are_separate_hashes():
    cfg = StrategyConfig.from_yaml()
    variants = {
        "close_through": {"fvg": {"mitigation_mode": "close_through"}},
        "touch": {"fvg": {"mitigation_mode": "touch"}},
        "poi_recent": {"meta": {"poi_selection_mode": "most_recent_valid"}},
    }
    results = compare_variant_runs(_bundle(n=2000), cfg, variants)
    hashes = {r.config_hash for r in results.values()}
    # Different overrides should yield different config hashes
    assert len(hashes) >= 2


def test_e2_no_synthetic_tp_means_no_forced_trade():
    """Sanity: engine never invents TP — absence of signals is acceptable."""
    cfg = StrategyConfig.from_yaml()
    # Extremely strict RR makes liquidity TP unlikely
    cfg2 = cfg.with_overrides({"risk": {"rr_min": 50.0}})
    res = run_backtest_on_bundle(_bundle(n=1500), cfg2, step=5)
    for d in res.decisions:
        if d.decision != "NO_TRADE":
            assert d.rr1 is not None and d.rr1 >= 50.0
