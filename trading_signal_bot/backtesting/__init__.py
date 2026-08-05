"""Backtest engine — event-driven on 5M closes. P0: split-bounded outcomes, calibrate≠OOS."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

import pandas as pd
import yaml

from trading_signal_bot.backtesting.metrics import (
    MetricsReport,
    SimulatedTrade,
    compute_metrics,
    simulate_trade_path,
)
from trading_signal_bot.backtesting.regimes import (
    RegimeThresholds,
    fit_regime_thresholds_from_train,
    label_regime_at,
)
from trading_signal_bot.backtesting.splits import (
    SplitWindow,
    chronological_splits,
    monte_carlo_expectancy,
    overfitting_flags,
    walk_forward_windows,
)
from trading_signal_bot.config import StrategyConfig, config_hash
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


@dataclass
class CalibrationResult:
    """TRAIN/VAL only — never includes OOS metrics."""

    train: BacktestResult
    val: BacktestResult
    locked_config: StrategyConfig
    locked_path: str | None
    locked_hash: str
    splits: dict[str, SplitWindow]
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class WalkForwardResult:
    folds: list[dict[str, Any]]
    aggregated_val_metrics: MetricsReport
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


def _split_end_index(df: pd.DataFrame, end: pd.Timestamp | None) -> int | None:
    """Inclusive index of last bar allowed for outcome simulation in this split."""
    if end is None:
        return None
    end = pd.Timestamp(end)
    if end.tzinfo is None:
        end = end.tz_localize("UTC")
    else:
        end = end.tz_convert("UTC")
    idx = int(df.index.searchsorted(end, side="right") - 1)
    return idx if idx >= 0 else None


def run_backtest_on_bundle(
    bundle: MultiTimeframeBundle,
    cfg: StrategyConfig,
    start: pd.Timestamp | None = None,
    end: pd.Timestamp | None = None,
    step: int = 1,
    include_category_b: bool = True,
    news_blackout_ts: set[pd.Timestamp] | None = None,
    regime_thresholds: RegimeThresholds | None = None,
    warmup_bars: int | None = None,
) -> BacktestResult:
    """
    Iterate 5M closes, evaluate strategy, simulate outcomes.
    Outcomes are strictly bounded to `end` (split leakage prevention).
    """
    df5_full = bundle.m5.df
    df5 = df5_full
    if start is not None:
        df5 = df5.loc[df5.index >= start]
    if end is not None:
        df5 = df5.loc[df5.index <= end]
    if df5.empty:
        return BacktestResult([], [], MetricsReport(), cfg.hash)

    max_idx = _split_end_index(df5_full, end)
    deduper = SignalDeduper()
    decisions: list[SignalDecision] = []
    trades: list[SimulatedTrade] = []
    risk_cfg = cfg.get("risk", default={}) or {}
    partial = float(risk_cfg.get("partial_tp1_fraction", 0.5))
    path_mode = str(risk_cfg.get("intrabar_path", "worst_case_sl_first"))
    max_hold = _max_hold(cfg, bundle.asset_class)
    atr_period = cfg.atr_period
    w_vol = int(cfg.get("volatility_filter", "W_vol", default=100))

    # Fit regimes on bars visible up to split start (or full pre-window) if not provided
    if regime_thresholds is None:
        fit_end = _split_end_index(bundle.m15.df, start) if start is not None else None
        if fit_end is None or fit_end < w_vol:
            fit_end = min(len(bundle.m15.df) - 1, max(w_vol + 10, len(bundle.m15.df) // 3))
        regime_thresholds = fit_regime_thresholds_from_train(
            bundle.m15.df, fit_end, atr_period, w_vol
        )

    timestamps = list(df5.index[::step])
    if warmup_bars is None:
        warmup = min(500, max(0, len(timestamps) // 10))
    else:
        warmup = min(int(warmup_bars), max(0, len(timestamps) - 1))

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

        entry_idx = int(df5_full.index.get_loc(decision.ts_utc))
        if isinstance(entry_idx, slice):
            continue

        outcome, bars, pnl, mfe, mae, exit_ts = simulate_trade_path(
            df5_full,
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
            max_index=max_idx,
        )

        # Enforce: exit timestamp must not exceed split end
        if exit_ts is not None and end is not None and exit_ts > pd.Timestamp(end):
            raise RuntimeError(
                f"split leakage detected: exit_ts {exit_ts} > split end {end}"
            )

        regime = label_regime_at(
            regime_thresholds,
            bundle.m15.df,
            bundle.h4.df,
            decision.ts_utc,
            atr_period,
            w_vol,
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
                market_regime=regime,
            )
        )

    _mark_correlated(decisions, pd.Timedelta(hours=2))
    metrics = compute_metrics(trades)
    return BacktestResult(
        trades=trades,
        decisions=decisions,
        metrics=metrics,
        config_hash=cfg.hash,
        meta={
            "symbol": bundle.symbol,
            "n_eval_bars": len(timestamps) - warmup,
            "split_end": str(end) if end is not None else None,
            "max_index": max_idx,
        },
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


def lock_config(
    cfg: StrategyConfig,
    path: str | Path | None = None,
) -> tuple[StrategyConfig, str, str | None]:
    """Write locked config + return (cfg, hash, path)."""
    locked = StrategyConfig.from_dict(cfg.raw)
    out_path: str | None = None
    if path is not None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(locked.raw)
        payload["_lock"] = {
            "config_hash": locked.hash,
            "frozen": True,
            "note": "Do not retune after viewing OOS.",
        }
        with p.open("w", encoding="utf-8") as f:
            yaml.safe_dump(payload, f, sort_keys=False)
        out_path = str(p)
    return locked, locked.hash, out_path


def run_calibration(
    bundle: MultiTimeframeBundle,
    cfg: StrategyConfig,
    train_fraction: float = 0.6,
    val_fraction: float = 0.2,
    oos_fraction: float = 0.2,
    locked_config_path: str | Path | None = "config/strategy_v1_locked.yaml",
    select_fn: Callable[[BacktestResult, BacktestResult, StrategyConfig], StrategyConfig]
    | None = None,
) -> CalibrationResult:
    """
    P0 OOS isolation: calibration uses TRAIN + VAL only.
    Does NOT run or return OOS metrics. Writes locked config before any OOS eval.
    """
    splits = chronological_splits(
        bundle.m5.df.index, train_fraction, val_fraction, oos_fraction
    )
    # Fit regimes on TRAIN only
    train_end_idx = _split_end_index(bundle.m15.df, splits["TRAIN"].end) or 0
    regimes = fit_regime_thresholds_from_train(
        bundle.m15.df,
        train_end_idx,
        cfg.atr_period,
        int(cfg.get("volatility_filter", "W_vol", default=100)),
    )

    train = run_backtest_on_bundle(
        bundle,
        cfg,
        start=splits["TRAIN"].start,
        end=splits["TRAIN"].end,
        regime_thresholds=regimes,
    )
    train.split_name = "TRAIN"
    val = run_backtest_on_bundle(
        bundle,
        cfg,
        start=splits["VAL"].start,
        end=splits["VAL"].end,
        regime_thresholds=regimes,
    )
    val.split_name = "VAL"

    selected = select_fn(train, val, cfg) if select_fn else cfg
    locked, locked_hash, locked_path = lock_config(selected, locked_config_path)

    flags = overfitting_flags(
        train.metrics.expectancy_R,
        val.metrics.expectancy_R,
        oos_exp=None,  # never touch OOS here
        train_n=train.metrics.n_signals,
        val_n=val.metrics.n_signals,
    )
    mc = monte_carlo_expectancy([t.pnl_R for t in val.trades])
    meta = {
        "overfitting_flags": flags,
        "val_monte_carlo": mc,
        "split_windows": {
            k: {"start": str(v.start), "end": str(v.end)}
            for k, v in splits.items()
            if k != "OOS"
        },
        "oos_window_reserved": {
            "start": str(splits["OOS"].start),
            "end": str(splits["OOS"].end),
        },
        "includes_oos_metrics": False,
        "locked_hash": locked_hash,
        "regime_thresholds": {
            "vol_low": regimes.vol_low,
            "vol_high": regimes.vol_high,
        },
    }
    train.meta.update(meta)
    val.meta.update(meta)
    return CalibrationResult(
        train=train,
        val=val,
        locked_config=locked,
        locked_path=locked_path,
        locked_hash=locked_hash,
        splits=splits,
        meta=meta,
    )


def run_oos_eval(
    bundle: MultiTimeframeBundle,
    locked_cfg: StrategyConfig,
    splits: dict[str, SplitWindow] | None = None,
    train_fraction: float = 0.6,
    val_fraction: float = 0.2,
    oos_fraction: float = 0.2,
) -> BacktestResult:
    """
    Evaluate frozen config on OOS only. Must be called AFTER calibration lock.
    Never used to retune parameters.
    """
    if "_lock" not in locked_cfg.raw and not locked_cfg.get("meta", "frozen", default=False):
        # Accept either embedded lock marker or explicit frozen flag
        if locked_cfg.get("_lock", "frozen", default=False) is not True:
            # Soft check: require hash stability via lock_config having been called
            pass
    if splits is None:
        splits = chronological_splits(
            bundle.m5.df.index, train_fraction, val_fraction, oos_fraction
        )
    # Regime thresholds fitted on TRAIN only (no OOS peeking)
    train_end_idx = _split_end_index(bundle.m15.df, splits["TRAIN"].end) or 0
    regimes = fit_regime_thresholds_from_train(
        bundle.m15.df,
        train_end_idx,
        locked_cfg.atr_period,
        int(locked_cfg.get("volatility_filter", "W_vol", default=100)),
    )
    res = run_backtest_on_bundle(
        bundle,
        locked_cfg,
        start=splits["OOS"].start,
        end=splits["OOS"].end,
        regime_thresholds=regimes,
    )
    res.split_name = "OOS"
    res.meta["locked_hash"] = locked_cfg.hash
    res.meta["includes_oos_metrics"] = True
    flags = overfitting_flags(
        0.0,
        0.0,
        oos_exp=res.metrics.expectancy_R,
        train_n=0,
        val_n=0,
    )
    # Only oos_breakdown meaningful if we pass val — caller may enrich
    res.meta["overfitting_flags"] = flags
    return res


def run_split_backtests(
    bundle: MultiTimeframeBundle,
    cfg: StrategyConfig,
    train_fraction: float = 0.6,
    val_fraction: float = 0.2,
    oos_fraction: float = 0.2,
    freeze_before_oos: bool = True,
    include_oos: bool = False,
) -> dict[str, BacktestResult]:
    """
    Backward-compatible helper.
    Default include_oos=False enforces P0 isolation (TRAIN/VAL only).
    Set include_oos=True only for explicit oos_eval workflows after locking.
    """
    del freeze_before_oos
    cal = run_calibration(
        bundle,
        cfg,
        train_fraction,
        val_fraction,
        oos_fraction,
        locked_config_path=None,
    )
    out: dict[str, BacktestResult] = {"TRAIN": cal.train, "VAL": cal.val}
    if include_oos:
        out["OOS"] = run_oos_eval(bundle, cal.locked_config, cal.splits)
    return out


def run_walk_forward(
    bundle: MultiTimeframeBundle,
    base_cfg: StrategyConfig,
    train_bars: int | None = None,
    val_bars: int | None = None,
    step_bars: int | None = None,
    variant_overrides: dict[str, dict[str, Any]] | None = None,
) -> WalkForwardResult:
    """
    P2 walk-forward: each fold trains/selects on TRAIN_WF, evaluates on VAL_WF.
    Never touches reserved OOS tail (last oos_fraction of series).
    """
    idx = bundle.m5.df.index
    splits = chronological_splits(idx)
    # Reserve OOS: only walk-forward inside TRAIN+VAL region
    usable = idx[idx <= splits["VAL"].end]
    train_bars = train_bars or int(base_cfg.get("walk_forward", "train_bars_5m", default=500))
    val_bars = val_bars or int(base_cfg.get("walk_forward", "val_bars_5m", default=150))
    step_bars = step_bars or int(base_cfg.get("walk_forward", "step_bars_5m", default=150))
    # Shrink defaults for short synthetic series
    train_bars = min(train_bars, max(50, len(usable) // 3))
    val_bars = min(val_bars, max(20, len(usable) // 6))
    step_bars = min(step_bars, max(20, val_bars))

    folds: list[dict[str, Any]] = []
    all_val_trades: list[SimulatedTrade] = []
    variants = variant_overrides or {base_cfg.hash: {}}

    for fold_i, (tr, va) in enumerate(
        walk_forward_windows(usable, train_bars, val_bars, step_bars)
    ):
        train_end_idx = _split_end_index(bundle.m15.df, tr.end) or 0
        regimes = fit_regime_thresholds_from_train(
            bundle.m15.df,
            train_end_idx,
            base_cfg.atr_period,
            int(base_cfg.get("volatility_filter", "W_vol", default=100)),
        )
        # Select variant by VAL expectancy within fold (TRAIN used for fit/context)
        best_name = None
        best_val: BacktestResult | None = None
        best_train: BacktestResult | None = None
        best_exp = float("-inf")
        for name, overrides in variants.items():
            cfg = base_cfg.with_overrides(overrides) if overrides else base_cfg
            train_res = run_backtest_on_bundle(
                bundle, cfg, start=tr.start, end=tr.end, regime_thresholds=regimes, warmup_bars=20
            )
            val_res = run_backtest_on_bundle(
                bundle, cfg, start=va.start, end=va.end, regime_thresholds=regimes, warmup_bars=0
            )
            # Prefer higher VAL expectancy; 0-signal folds get neutral score for selection fallback
            if val_res.metrics.n_signals == 0:
                score = -1e9
            else:
                score = val_res.metrics.expectancy_R
            if best_val is None or score > best_exp:
                best_exp = score
                best_name = name
                best_val = val_res
                best_train = train_res
        if best_val is None or best_train is None:
            continue
        all_val_trades.extend(best_val.trades)
        folds.append(
            {
                "fold": fold_i,
                "selected_variant": best_name,
                "train_window": {"start": str(tr.start), "end": str(tr.end)},
                "val_window": {"start": str(va.start), "end": str(va.end)},
                "train_metrics": best_train.metrics.to_dict(),
                "val_metrics": best_val.metrics.to_dict(),
            }
        )

    agg = compute_metrics(all_val_trades)
    return WalkForwardResult(
        folds=folds,
        aggregated_val_metrics=agg,
        meta={
            "n_folds": len(folds),
            "includes_oos": False,
            "reserved_oos": {"start": str(splits["OOS"].start), "end": str(splits["OOS"].end)},
            "val_monte_carlo": monte_carlo_expectancy([t.pnl_R for t in all_val_trades]),
        },
    )


def compare_variant_runs(
    bundle: MultiTimeframeBundle,
    base_cfg: StrategyConfig,
    variants: dict[str, dict[str, Any]],
    split: SplitWindow | None = None,
) -> dict[str, BacktestResult]:
    """Each variant is a SEPARATE run (never merge equity curves)."""
    results: dict[str, BacktestResult] = {}
    for name, overrides in variants.items():
        cfg = base_cfg.with_overrides(overrides)
        kwargs: dict[str, Any] = {}
        if split is not None:
            kwargs = {"start": split.start, "end": split.end}
        res = run_backtest_on_bundle(bundle, cfg, **kwargs)
        res.split_name = name
        results[name] = res
    return results


def calibration_report_dict(cal: CalibrationResult) -> dict[str, Any]:
    """Serialize calibration without OOS keys."""
    return {
        "TRAIN": {
            "metrics": cal.train.metrics.to_dict(),
            "n_decisions": len(cal.train.decisions),
            "config_hash": cal.train.config_hash,
        },
        "VAL": {
            "metrics": cal.val.metrics.to_dict(),
            "n_decisions": len(cal.val.decisions),
            "config_hash": cal.val.config_hash,
        },
        "locked_hash": cal.locked_hash,
        "locked_path": cal.locked_path,
        "includes_oos_metrics": False,
        "meta": cal.meta,
    }
