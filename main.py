#!/usr/bin/env python3
"""CLI entrypoint — signals-only bot (no order execution)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trading_signal_bot.backtesting import (  # noqa: E402
    calibration_report_dict,
    run_backtest_on_bundle,
    run_calibration,
    run_oos_eval,
    run_split_backtests,
    run_walk_forward,
)
from trading_signal_bot.config import StrategyConfig  # noqa: E402
from trading_signal_bot.data import MultiTimeframeBundle  # noqa: E402
from trading_signal_bot.data.providers import make_mtf_synthetic  # noqa: E402

_COMMANDS = ("backtest", "calibrate", "oos-eval", "walk-forward")


def _bundle_from_synthetic(symbol: str, n_5m: int, seed: int) -> MultiTimeframeBundle:
    frames = make_mtf_synthetic(symbol, n_5m=n_5m, seed=seed)
    asset = "CRYPTO" if symbol.endswith("USDT") else ("XAU" if symbol.startswith("XAU") else "FX")
    return MultiTimeframeBundle(
        symbol=symbol,
        asset_class=asset,  # type: ignore[arg-type]
        m5=frames["5M"],
        m15=frames["15M"],
        h1=frames["1H"],
        h4=frames["4H"],
    )


def _split_names(payload: dict) -> list[str]:
    return [k for k in ("TRAIN", "VAL", "OOS") if k in payload]


def _n_signals_from_block(block: dict) -> int:
    metrics = block.get("metrics") or {}
    if "n_signals" in metrics:
        return int(metrics.get("n_signals") or 0)
    return int(block.get("n_decisions") or 0)


def _symbol_signal_count(payload: dict) -> int:
    splits = _split_names(payload)
    if splits:
        return sum(_n_signals_from_block(payload[name]) for name in splits)
    return _n_signals_from_block(payload)


def _compact_symbol_view(symbol: str, payload: dict) -> dict:
    """Compact per-symbol metrics for quick V7→V8 analysis."""
    splits = _split_names(payload)
    if splits:
        n_signals = {name: _n_signals_from_block(payload[name]) for name in splits}
        expectancy = {
            name: (payload[name].get("metrics") or {}).get("expectancy_R")
            for name in splits
        }
        winrate = {
            name: (payload[name].get("metrics") or {}).get("win_rate")
            for name in splits
        }
    else:
        metrics = payload.get("metrics") or {}
        n_signals = {"ALL": int(metrics.get("n_signals") or payload.get("n_decisions") or 0)}
        expectancy = {"ALL": metrics.get("expectancy_R")}
        winrate = {"ALL": metrics.get("win_rate")}
    return {
        "symbol": symbol,
        "n_signals": n_signals,
        "expectancy": expectancy,
        "winrate": winrate,
        "includes_oos_metrics": bool(payload.get("includes_oos_metrics")),
    }


def _build_summary(by_symbol: dict) -> dict:
    total_signals = 0
    symbols_with_signals = 0
    for payload in by_symbol.values():
        n = _symbol_signal_count(payload)
        total_signals += n
        if n > 0:
            symbols_with_signals += 1
    return {
        "total_symbols": len(by_symbol),
        "symbols_with_signals": symbols_with_signals,
        "total_signals": total_signals,
    }


def cmd_backtest(args: argparse.Namespace) -> int:
    cfg = StrategyConfig.from_yaml(args.config)
    include_oos = bool(getattr(args, "include_oos", False))
    compact = bool(getattr(args, "compact", False))
    print("BACKTEST INCLUDE OOS:", include_oos)

    # --symbols (comma-separated) overrides --symbol fallback
    symbols = [
        s.strip()
        for s in str(getattr(args, "symbols", None) or args.symbol).split(",")
        if s.strip()
    ]
    if not symbols:
        symbols = [str(args.symbol)]

    by_symbol: dict = {}
    for symbol in symbols:
        print(f"===== {symbol} =====")
        bundle = _bundle_from_synthetic(symbol, args.bars, args.seed)
        if args.splits:
            results = run_split_backtests(
                bundle,
                cfg,
                include_oos=include_oos,
            )
            payload: dict = {
                name: {
                    "metrics": res.metrics.to_dict(),
                    "n_decisions": len(res.decisions),
                    "config_hash": res.config_hash,
                    "meta": {k: v for k, v in res.meta.items() if k != "val_monte_carlo"}
                    | {"val_monte_carlo": res.meta.get("val_monte_carlo")},
                }
                for name, res in results.items()
            }
            payload["includes_oos_metrics"] = include_oos and ("OOS" in results)
        else:
            res = run_backtest_on_bundle(bundle, cfg)
            payload = {
                "metrics": res.metrics.to_dict(),
                "n_decisions": len(res.decisions),
                "config_hash": res.config_hash,
                "meta": res.meta,
                "includes_oos_metrics": False,
            }
        by_symbol[symbol] = payload

    summary = _build_summary(by_symbol)

    if compact:
        compact_rows = [
            _compact_symbol_view(sym, payload) for sym, payload in by_symbol.items()
        ]
        out = {"summary": summary, "symbols": compact_rows}
        print(json.dumps(out, indent=2, default=str))
        return 0

    if len(by_symbol) == 1:
        # Single-symbol: keep flat payload (JSON-compatible, unchanged shape)
        print(json.dumps(next(iter(by_symbol.values())), indent=2, default=str))
    else:
        print(
            json.dumps(
                {"summary": summary, "symbols": by_symbol},
                indent=2,
                default=str,
            )
        )
    return 0


def cmd_calibrate(args: argparse.Namespace) -> int:
    cfg = StrategyConfig.from_yaml(args.config)
    bundle = _bundle_from_synthetic(args.symbol, args.bars, args.seed)
    cal = run_calibration(
        bundle,
        cfg,
        locked_config_path=args.locked_out,
    )
    print(json.dumps(calibration_report_dict(cal), indent=2, default=str))
    return 0


def cmd_oos_eval(args: argparse.Namespace) -> int:
    locked = StrategyConfig.from_yaml(args.locked_config)
    bundle = _bundle_from_synthetic(args.symbol, args.bars, args.seed)
    res = run_oos_eval(bundle, locked)
    print(
        json.dumps(
            {
                "split": "OOS",
                "metrics": res.metrics.to_dict(),
                "n_decisions": len(res.decisions),
                "locked_hash": locked.hash,
                "includes_oos_metrics": True,
            },
            indent=2,
            default=str,
        )
    )
    return 0


def cmd_walk_forward(args: argparse.Namespace) -> int:
    cfg = StrategyConfig.from_yaml(args.config)
    bundle = _bundle_from_synthetic(args.symbol, args.bars, args.seed)
    wf = run_walk_forward(bundle, cfg)
    print(
        json.dumps(
            {
                "n_folds": wf.meta["n_folds"],
                "includes_oos": wf.meta["includes_oos"],
                "aggregated_val_metrics": wf.aggregated_val_metrics.to_dict(),
                "folds": wf.folds,
                "meta": wf.meta,
            },
            indent=2,
            default=str,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    """ONLY parser factory in this project entrypoint."""
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Trading Signal Bot (signals-only). Never places orders.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # backtest — ONLY home for --symbols and --include-oos
    b = sub.add_parser("backtest", help="Run backtest engine")
    b.add_argument("--symbol", default="EURUSD", help="Single symbol")
    b.add_argument(
        "--symbols",
        dest="symbols",
        default="",
        metavar="SYMBOLS",
        help="Comma-separated symbols (overrides --symbol). Example: XAUUSD,EURUSD,GBPUSD,USDJPY",
    )
    b.add_argument("--bars", type=int, default=3000)
    b.add_argument("--seed", type=int, default=42)
    b.add_argument(
        "--splits",
        action="store_true",
        help="Run TRAIN/VAL calibration splits (OOS excluded by default)",
    )
    b.add_argument(
        "--include-oos",
        dest="include_oos",
        action="store_true",
        help="Include OOS results in split backtests",
    )
    b.add_argument(
        "--compact",
        dest="compact",
        action="store_true",
        help="Print compact per-symbol summary (n_signals / expectancy / winrate)",
    )
    b.add_argument("--config", default="config/strategy_v1.yaml")
    b.set_defaults(func=cmd_backtest)

    c = sub.add_parser("calibrate", help="TRAIN/VAL calibration + lock config (no OOS)")
    c.add_argument("--symbol", default="EURUSD")
    c.add_argument("--bars", type=int, default=3000)
    c.add_argument("--seed", type=int, default=42)
    c.add_argument("--config", default="config/strategy_v1.yaml")
    c.add_argument("--locked-out", default="config/strategy_v1_locked.yaml")
    c.set_defaults(func=cmd_calibrate)

    o = sub.add_parser("oos-eval", help="Evaluate locked config on OOS only")
    o.add_argument("--symbol", default="EURUSD")
    o.add_argument("--bars", type=int, default=3000)
    o.add_argument("--seed", type=int, default=42)
    o.add_argument("--locked-config", default="config/strategy_v1_locked.yaml")
    o.set_defaults(func=cmd_oos_eval)

    w = sub.add_parser("walk-forward", help="Walk-forward TRAIN→VAL folds (no OOS)")
    w.add_argument("--symbol", default="EURUSD")
    w.add_argument("--bars", type=int, default=3000)
    w.add_argument("--seed", type=int, default=42)
    w.add_argument("--config", default="config/strategy_v1.yaml")
    w.set_defaults(func=cmd_walk_forward)

    return parser


def _normalize_argv(argv: list[str] | None) -> list[str] | None:
    """
    Force subcommand-first order so flags like --symbols are attached to
    the backtest subparser even if the user put them before the command.
    Example:  --symbols EURUSD backtest  →  backtest --symbols EURUSD
    """
    raw = list(sys.argv[1:] if argv is None else argv)
    cmd_idx = next((i for i, a in enumerate(raw) if a in _COMMANDS), None)
    if cmd_idx is None or cmd_idx == 0:
        return None if argv is None else raw
    cmd = raw[cmd_idx]
    return [cmd, *raw[:cmd_idx], *raw[cmd_idx + 1 :]]


def main(argv: list[str] | None = None) -> int:
    print("USING FILE:", __file__)
    parser = build_parser()
    normalized = _normalize_argv(argv)
    args = parser.parse_args(normalized)
    print("ARGS:", vars(args))
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
