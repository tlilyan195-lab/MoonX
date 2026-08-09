#!/usr/bin/env python3
"""
TRAIN/VAL sanity on D1+D2 snapshot — no OOS, no hyperparameter optimization.

Validates signal frequency, distribution, and basic performance sanity.
Does not maximize win rate. Does not touch OOS split.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd

from trading_signal_bot.backtesting import run_backtest_on_bundle
from trading_signal_bot.backtesting.regimes import fit_regime_thresholds_from_train
from trading_signal_bot.backtesting.splits import chronological_splits
from trading_signal_bot.config import StrategyConfig
from trading_signal_bot.data import MultiTimeframeBundle
from trading_signal_bot.data.jblanked_calendar import load_events_parquet
from trading_signal_bot.data.news_blackout import blackout_set_for_symbol
from trading_signal_bot.data.snapshot import load_snapshot_manifest, read_frame_parquet

DEFAULT_SNAPSHOT = ROOT / "data" / "snapshots" / "pilot_3m_2025Q4_d1d2"
FX_SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]
CRYPTO_SYMBOLS = ["BTCUSDT", "ETHUSDT"]


def _asset_class(symbol: str) -> str:
    if symbol == "XAUUSD":
        return "XAU"
    if symbol.endswith("USDT"):
        return "CRYPTO"
    return "FX"


def load_bundle(snap: Path, symbol: str) -> MultiTimeframeBundle | None:
    try:
        m5 = read_frame_parquet(symbol, "5M", snap / f"{symbol}_5M.parquet")
        m15 = read_frame_parquet(symbol, "15M", snap / f"{symbol}_15M.parquet")
        h1 = read_frame_parquet(symbol, "1H", snap / f"{symbol}_1H.parquet")
        h4 = read_frame_parquet(symbol, "4H", snap / f"{symbol}_4H.parquet")
    except FileNotFoundError:
        return None
    if m5.df.empty:
        return None
    return MultiTimeframeBundle(symbol, _asset_class(symbol), m5, m15, h1, h4)  # type: ignore[arg-type]


def _metrics_brief(res) -> dict[str, Any]:
    m = res.metrics
    cats = Counter(d.category for d in res.decisions if d.decision != "NO_TRADE")
    dirs = Counter(d.direction for d in res.decisions if d.direction)
    return {
        "n_signals": int(m.n_signals),
        "n_decisions_alertable": int(sum(cats.values())),
        "category_dist": dict(cats),
        "direction_dist": dict(dirs),
        "expectancy_R": float(m.expectancy_R) if m.n_signals else None,
        "profit_factor": float(m.profit_factor) if getattr(m, "profit_factor", None) is not None else None,
        "win_rate": float(m.win_rate) if m.n_signals else None,
        "max_drawdown_R": float(m.max_drawdown_R) if m.n_signals else None,
        "note": "win_rate reported for sanity only — not an optimization target",
    }


def run_symbol(
    bundle: MultiTimeframeBundle,
    cfg: StrategyConfig,
    events: pd.DataFrame,
    step: int,
) -> dict[str, Any]:
    splits = chronological_splits(
        bundle.m5.df.index,
        float(cfg.get("splits", "train_fraction", default=0.6)),
        float(cfg.get("splits", "val_fraction", default=0.2)),
        float(cfg.get("splits", "oos_fraction", default=0.2)),
    )
    news_cfg = cfg.get("news", default={}) or {}
    window = int(news_cfg.get("window_minutes", 30))
    major = int(news_cfg.get("major_window_minutes", 60))
    blackout = blackout_set_for_symbol(
        events,
        bundle.symbol,
        bundle.m5.df.index,
        window_minutes=window,
        major_window_minutes=major,
    )

    train_end_idx = int(bundle.m15.df.index.searchsorted(splits["TRAIN"].end, side="right") - 1)
    train_end_idx = max(train_end_idx, 0)
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
        step=step,
        regime_thresholds=regimes,
        news_blackout_ts=blackout,
    )
    train.split_name = "TRAIN"
    val = run_backtest_on_bundle(
        bundle,
        cfg,
        start=splits["VAL"].start,
        end=splits["VAL"].end,
        step=step,
        regime_thresholds=regimes,
        news_blackout_ts=blackout,
    )
    val.split_name = "VAL"

    # Frequency: signals / days in split
    def _freq(res, split_name: str) -> dict[str, Any]:
        sp = splits[split_name]
        days = max((sp.end - sp.start).total_seconds() / 86400.0, 1e-9)
        n = res.metrics.n_signals
        n_blackout_bars = sum(1 for ts in bundle.m5.df.index if ts in blackout and sp.start <= ts <= sp.end)
        return {
            "signals": int(n),
            "signals_per_day": float(n) / days,
            "split_days": float(days),
            "blackout_bars_in_split": int(n_blackout_bars),
        }

    return {
        "symbol": bundle.symbol,
        "asset_class": bundle.asset_class,
        "n_5m_bars": int(len(bundle.m5.df)),
        "n_blackout_bars_total": int(len(blackout)),
        "splits": {
            "TRAIN": {"start": str(splits["TRAIN"].start), "end": str(splits["TRAIN"].end)},
            "VAL": {"start": str(splits["VAL"].start), "end": str(splits["VAL"].end)},
            "OOS_RESERVED_NOT_RUN": {
                "start": str(splits["OOS"].start),
                "end": str(splits["OOS"].end),
            },
        },
        "frequency": {"TRAIN": _freq(train, "TRAIN"), "VAL": _freq(val, "VAL")},
        "TRAIN": _metrics_brief(train),
        "VAL": _metrics_brief(val),
        "includes_oos": False,
        "hyperparameter_optimization": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot-dir", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--step", type=int, default=1, help="5M bar stride (1=full)")
    parser.add_argument(
        "--symbols",
        default=",".join(FX_SYMBOLS + CRYPTO_SYMBOLS),
        help="Comma-separated symbols",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Report JSON path (default: snapshot_dir/TRAIN_VAL_SANITY.json)",
    )
    args = parser.parse_args()
    snap: Path = args.snapshot_dir
    if not (snap / "manifest.json").is_file():
        print(f"FAIL: missing manifest in {snap}", file=sys.stderr)
        return 1

    manifest = load_snapshot_manifest(snap)
    cfg = StrategyConfig.from_yaml()
    news_path = snap / "news_jblanked_events.parquet"
    events = load_events_parquet(news_path)

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    per_symbol: list[dict[str, Any]] = []
    for sym in symbols:
        bundle = load_bundle(snap, sym)
        if bundle is None:
            per_symbol.append({"symbol": sym, "skipped": True, "reason": "missing_or_empty_ohlc"})
            continue
        print(f"[TRAIN/VAL] {sym} bars={len(bundle.m5.df)} ...", flush=True)
        per_symbol.append(run_symbol(bundle, cfg, events, step=max(1, args.step)))

    # Aggregate sanity
    tot_train = sum(r.get("TRAIN", {}).get("n_signals", 0) for r in per_symbol if not r.get("skipped"))
    tot_val = sum(r.get("VAL", {}).get("n_signals", 0) for r in per_symbol if not r.get("skipped"))
    report = {
        "goal": "Validate signal frequency, distribution, basic performance sanity — not maximize win rate",
        "data_snapshot_id": manifest.get("data_snapshot_id"),
        "snapshot_dir": str(snap),
        "d2_provider": (manifest.get("providers") or {}).get("D2_NEWS"),
        "n_news_events_in_snapshot": int(len(events)),
        "n_news_high": int((events["impact"].astype(str).str.lower() == "high").sum()) if len(events) else 0,
        "constraints": {
            "oos_run": False,
            "hyperparameter_optimization": False,
            "news_gate": "Impact High + Currency; scheduled Date UTC only",
            "ignore_fields": ["Actual", "Outcome", "Strength", "Quality"],
        },
        "aggregate": {
            "TRAIN_n_signals": int(tot_train),
            "VAL_n_signals": int(tot_val),
        },
        "symbols": per_symbol,
    }
    out = args.out or (snap / "TRAIN_VAL_SANITY.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")

    # Also write a short markdown summary into docs/
    md_path = ROOT / "docs" / "ETAPE_5_TRAIN_VAL_SANITY.md"
    lines = [
        "# ÉTAPE 5 — TRAIN/VAL sanity (D1+D2)",
        "",
        f"- data_snapshot_id: `{report['data_snapshot_id']}`",
        f"- D2: `{report['d2_provider']}`",
        f"- News events in snapshot: `{report['n_news_events_in_snapshot']}` (high={report['n_news_high']})",
        f"- TRAIN signals (all symbols): `{tot_train}`",
        f"- VAL signals (all symbols): `{tot_val}`",
        "- OOS: **not run**",
        "- Hyperparameter optimization: **not run**",
        "",
        "## Per symbol",
        "",
    ]
    for r in per_symbol:
        if r.get("skipped"):
            lines.append(f"- `{r['symbol']}`: skipped ({r.get('reason')})")
            continue
        lines.append(
            f"- `{r['symbol']}`: TRAIN n={r['TRAIN']['n_signals']} "
            f"({r['frequency']['TRAIN']['signals_per_day']:.3f}/day) "
            f"expR={r['TRAIN']['expectancy_R']}; "
            f"VAL n={r['VAL']['n_signals']} "
            f"({r['frequency']['VAL']['signals_per_day']:.3f}/day) "
            f"expR={r['VAL']['expectancy_R']}; "
            f"blackout_bars={r['n_blackout_bars_total']}"
        )
    lines.extend(["", f"Full JSON: `{out}`", ""])
    md_path.write_text("\n".join(lines), encoding="utf-8")

    print(json.dumps({"out": str(out), "md": str(md_path), "aggregate": report["aggregate"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
