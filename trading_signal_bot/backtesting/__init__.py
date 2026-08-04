"""Backtest engine — event-driven on 5M closes."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable

import pandas as pd

from trading_signal_bot.backtesting.metrics import (
    MetricsReport,
    SimulatedTrade,
    compute_metrics,
    simulate_trade_path,
)
from trading_signal_bot.backtesting.splits import (
    SplitWindow,
    chronological_splits,
    monte_carlo_expectancy,
    overfitting_flags,
)
from trading_signal_bot.config import StrategyConfig
from trading_signal_bot.data import AssetClass, MultiTimeframeBundle
from trading_signal_bot.signals import SignalDecision, SignalDeduper
from trading_signal_bot.strategy.engine import evaluate


CORRELATED_PAIRS = {frozenset({"EURUSD", "GBPUSD"})}


@dataclass
class BacktestResult:
    trades: list[SimulatedTrade]
    decisions: list[SignalDecision]
    metrics: MetricsReport
    config_hash: str
    split_name: str = "FULL"
    meta: dict[str, Any] = field(default_factory=dict)


def _asset_class(symbol: str) -> AssetClass:
    if symbol in ("BTCUSDT", "ETHUSDT"):
        return "CRYPTO"
    if symbol == "XAUUSD":
        return "XAU"
    return "FX"


def _max_hold(cfg: StrategyConfig, asset_class: AssetClass) -> int:
    if asset_class == "CRYPTO":
        return int(cfg.get("risk", "max_hold_bars_5m_crypto", default=96))
    return int(cfg.get("risk", "max_hold_bars_5m", default=48))


def _mark_correlated(decisions: list[SignalDecision], window: pd.Timedelta) -> None:
    by_ts: dict[pd.Timestamp, list[SignalDecision]] = {}
    for d in decisions:
        if d.decision == "NO_TRADE" or d.direction is None:
            continue
        by_ts.setdefault(d.ts_utc, []).append(d)
    # also nearby timestamps
    alertable = [d for d in decisions if d.decision != "NO_TRADE"]
    for i, a in enumerate(alertable):
        for b in alertable[i + 1 :]:
            if abs((a.ts_utc - b.ts_utc).total_seconds()) > window.total_seconds():
                continue
            pair = frozenset({a.symbol, b.symbol})
            if pair in CORRELATED_PAIRS and a.direction == b.direction:
                a.correlated_pair = True
                b.correlated_pair = True
                a.meta["CORRELATED_PAIR"] = True
                b.meta["CORRELATED_PAIR"] = True
                a.meta["correlation_warning"] = (
                    "EURUSD/GBPUSD same-direction setups — keep both (C1)"
                )
                b.meta["correlation_warning"] = a.meta["correlation_warning"]


def run_backtest_on_bundle(
    bundle: MultiTimeframeBundle,
    cfg: StrategyConfig,
    start: pd.Timestamp | None = None,
    end: pd.Timestamp | None = None,
    step: int = 1,
    include_category_b: bool = True,
    news_blackout_ts: set[pd.Timestamp] | None = None,
) -> BacktestResult:
    """
    Iterate 5M closes, evaluate strategy, simulate outcomes.
    Variants (FVG mitigation, POI mode, etc.) must be separate runs via cfg overrides.
    """
    df5 = bundle.m5.df
    if start is not None:
        df5 = df5.loc[df5.index >= start]
    if end is not None:
        df5 = df5.loc[df5.index <= end]
    if df5.empty:
        return BacktestResult([], [], MetricsReport(), cfg.hash)

    deduper = SignalDeduper()
    decisions: list[SignalDecision] = []
    trades: list[SimulatedTrade] = []
    risk_cfg = cfg.get("risk", default={}) or {}
    partial = float(risk_cfg.get("partial_tp1_fraction", 0.5))
    path_mode = str(risk_cfg.get("intrabar_path", "worst_case_sl_first"))
    max_hold = _max_hold(cfg, bundle.asset_class)

    # Warmup: skip first bars until HTF has history
    timestamps = list(df5.index[::step])
    warmup = min(500, max(0, len(timestamps) // 10))

    for ts in timestamps[warmup:]:
        blackout = news_blackout_ts is not None and ts in news_blackout_ts
        decision = evaluate(bundle, ts, cfg, news_blackout=blackout)
        if decision.decision == "NO_TRADE":
            continue
        if decision.category == "B" and not include_category_b:
            continue
        if deduper.is_duplicate(decision):
            decision.meta["suppressed_duplicate"] = True
            decisions.append(decision)
            continue
        decisions.append(decision)

        entry_idx = int(bundle.m5.df.index.get_loc(decision.ts_utc))
        if isinstance(entry_idx, slice):
            continue
        outcome, bars, pnl, mfe, mae, exit_ts = simulate_trade_path(
            bundle.m5.df,
            entry_idx,
            decision.direction or "LONG",
            float(decision.entry),
            float(decision.sl),
            float(decision.tp1),
            decision.tp2,
            float(decision.meta.get("risk_distance") or abs(decision.entry - decision.sl)),
            partial,
            max_hold,
            path_mode,
        )
        trades.append(
            SimulatedTrade(
                signal_id=str(uuid.uuid4()),
                symbol=decision.symbol,
                direction=decision.direction or "",
                category=decision.category,
                entry_ts=decision.ts_utc,
                entry=float(decision.entry),
                sl=float(decision.sl),
                tp1=float(decision.tp1),
                tp2=decision.tp2,
                rr1=float(decision.rr1 or 0.0),
                risk=float(decision.meta.get("risk_distance") or 0.0),
                partial_tp1_fraction=partial,
                outcome=outcome,
                exit_ts=exit_ts,
                pnl_R=pnl,
                mfe_R=mfe,
                mae_R=mae,
                bars_held=bars,
                weekday=int(decision.ts_utc.weekday()),
                setup_score=decision.setup_score,
                session_label="CRYPTO" if bundle.asset_class == "CRYPTO" else "FX",
            )
        )

    _mark_correlated(decisions, pd.Timedelta(hours=2))
    metrics = compute_metrics(trades)
    return BacktestResult(
        trades=trades,
        decisions=decisions,
        metrics=metrics,
        config_hash=cfg.hash,
        meta={"symbol": bundle.symbol, "n_eval_bars": len(timestamps) - warmup},
    )


def run_backtest_multi(
    bundles: Iterable[MultiTimeframeBundle],
    cfg: StrategyConfig,
    **kwargs: Any,
) -> BacktestResult:
    all_trades: list[SimulatedTrade] = []
    all_decisions: list[SignalDecision] = []
    for bundle in bundles:
        res = run_backtest_on_bundle(bundle, cfg, **kwargs)
        all_trades.extend(res.trades)
        all_decisions.extend(res.decisions)
    metrics = compute_metrics(all_trades)
    return BacktestResult(all_trades, all_decisions, metrics, cfg.hash, meta={"multi": True})


def run_split_backtests(
    bundle: MultiTimeframeBundle,
    cfg: StrategyConfig,
    train_fraction: float = 0.6,
    val_fraction: float = 0.2,
    oos_fraction: float = 0.2,
    freeze_before_oos: bool = True,
) -> dict[str, BacktestResult]:
    """
    TRAIN/VAL/OOS chronological evaluation.
    freeze_before_oos: documentation flag — caller must not retune using OOS.
    """
    del freeze_before_oos  # enforced by process, not code mutation
    splits = chronological_splits(
        bundle.m5.df.index, train_fraction, val_fraction, oos_fraction
    )
    out: dict[str, BacktestResult] = {}
    for name, window in splits.items():
        res = run_backtest_on_bundle(bundle, cfg, start=window.start, end=window.end)
        res.split_name = name
        out[name] = res

    train_exp = out["TRAIN"].metrics.expectancy_R
    val_exp = out["VAL"].metrics.expectancy_R
    oos_exp = out["OOS"].metrics.expectancy_R
    flags = overfitting_flags(
        train_exp,
        val_exp,
        oos_exp,
        train_n=out["TRAIN"].metrics.n_signals,
        val_n=out["VAL"].metrics.n_signals,
    )
    mc = monte_carlo_expectancy([t.pnl_R for t in out["VAL"].trades])
    for res in out.values():
        res.meta["overfitting_flags"] = flags
        res.meta["val_monte_carlo"] = mc
        res.meta["split_windows"] = {
            k: {"start": str(v.start), "end": str(v.end)} for k, v in splits.items()
        }
    return out


def compare_variant_runs(
    bundle: MultiTimeframeBundle,
    base_cfg: StrategyConfig,
    variants: dict[str, dict[str, Any]],
    split: SplitWindow | None = None,
) -> dict[str, BacktestResult]:
    """
    Each variant is a SEPARATE run (never merge equity curves).
    Selection must use TRAIN/VAL only.
    """
    results: dict[str, BacktestResult] = {}
    for name, overrides in variants.items():
        cfg = base_cfg.with_overrides(overrides)
        kwargs = {}
        if split is not None:
            kwargs = {"start": split.start, "end": split.end}
        res = run_backtest_on_bundle(bundle, cfg, **kwargs)
        res.split_name = name
        results[name] = res
    return results
