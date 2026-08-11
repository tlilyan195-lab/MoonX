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


def cmd_backtest(args: argparse.Namespace) -> int:
    print("BACKTEST ARGS:", vars(args))
    cfg = StrategyConfig.from_yaml(args.config)
    include_oos = bool(args.include_oos)
    print("BACKTEST INCLUDE OOS:", include_oos)

    symbols = [s.strip() for s in str(args.symbols or "").split(",") if s.strip()]
    if not symbols:
        symbols = [str(args.symbol)]

    by_symbol: dict = {}
    for symbol in symbols:
        bundle = _bundle_from_synthetic(symbol, args.bars, args.seed)
        if args.splits:
            results = run_split_backtests(
                bundle,
                cfg,
                include_oos=include_oos,
            )
            payload = {
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
            }
            payload["includes_oos_metrics"] = False
        by_symbol[symbol] = payload

    if len(by_symbol) == 1:
        print(json.dumps(next(iter(by_symbol.values())), indent=2, default=str))
    else:
        print(json.dumps({"symbols": by_symbol}, indent=2, default=str))
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
    """Single source of truth for CLI parsing — no other parsers."""
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Trading Signal Bot (signals-only). Never places orders.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # --- backtest (ONLY place for --symbols / --include-oos) ---
    backtest = subparsers.add_parser(
        "backtest",
        help="Run backtest engine (ÉTAPE 4)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    backtest.add_argument("--symbol", default="EURUSD", help="Single symbol")
    backtest.add_argument(
        "--symbols",
        default="",
        help="Comma-separated symbols (overrides --symbol). Example: XAUUSD,EURUSD,GBPUSD,USDJPY",
    )
    backtest.add_argument("--bars", type=int, default=3000, help="Synthetic 5M bars")
    backtest.add_argument("--seed", type=int, default=42, help="Synthetic data seed")
    backtest.add_argument(
        "--splits",
        action="store_true",
        help="Run TRAIN/VAL calibration splits (OOS excluded by default)",
    )
    backtest.add_argument(
        "--include-oos",
        action="store_true",
        dest="include_oos",
        help="Include OOS results in split backtests",
    )
    backtest.add_argument(
        "--config",
        default="config/strategy_v1.yaml",
        help="Strategy config YAML path",
    )
    backtest.set_defaults(func=cmd_backtest)

    calibrate = subparsers.add_parser(
        "calibrate",
        help="TRAIN/VAL calibration + lock config (no OOS)",
    )
    calibrate.add_argument("--symbol", default="EURUSD")
    calibrate.add_argument("--bars", type=int, default=3000)
    calibrate.add_argument("--seed", type=int, default=42)
    calibrate.add_argument("--config", default="config/strategy_v1.yaml")
    calibrate.add_argument("--locked-out", default="config/strategy_v1_locked.yaml")
    calibrate.set_defaults(func=cmd_calibrate)

    oos_eval = subparsers.add_parser(
        "oos-eval",
        help="Evaluate locked config on OOS only",
    )
    oos_eval.add_argument("--symbol", default="EURUSD")
    oos_eval.add_argument("--bars", type=int, default=3000)
    oos_eval.add_argument("--seed", type=int, default=42)
    oos_eval.add_argument("--locked-config", default="config/strategy_v1_locked.yaml")
    oos_eval.set_defaults(func=cmd_oos_eval)

    walk_forward = subparsers.add_parser(
        "walk-forward",
        help="Walk-forward TRAIN→VAL folds (no OOS)",
    )
    walk_forward.add_argument("--symbol", default="EURUSD")
    walk_forward.add_argument("--bars", type=int, default=3000)
    walk_forward.add_argument("--seed", type=int, default=42)
    walk_forward.add_argument("--config", default="config/strategy_v1.yaml")
    walk_forward.set_defaults(func=cmd_walk_forward)

    return parser


def main(argv: list[str] | None = None) -> int:
    print("LOADED main.py FROM:", __file__)
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
