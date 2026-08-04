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
    run_backtest_on_bundle,
    run_split_backtests,
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
    cfg = StrategyConfig.from_yaml(args.config)
    bundle = _bundle_from_synthetic(args.symbol, args.bars, args.seed)
    if args.splits:
        results = run_split_backtests(bundle, cfg)
        payload = {
            name: {
                "metrics": res.metrics.to_dict(),
                "n_decisions": len(res.decisions),
                "config_hash": res.config_hash,
                "meta": res.meta,
            }
            for name, res in results.items()
        }
    else:
        res = run_backtest_on_bundle(bundle, cfg)
        payload = {
            "metrics": res.metrics.to_dict(),
            "n_decisions": len(res.decisions),
            "config_hash": res.config_hash,
            "meta": res.meta,
        }
    print(json.dumps(payload, indent=2, default=str))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Trading Signal Bot (signals-only). Never places orders."
    )
    p.add_argument(
        "--mode",
        choices=["backtest", "calibrate", "oos_eval", "paper", "live_signals"],
        default="backtest",
    )
    p.add_argument("--config", default="config/strategy_v1.yaml")
    sub = p.add_subparsers(dest="command")

    b = sub.add_parser("backtest", help="Run backtest engine (ÉTAPE 4)")
    b.add_argument("--symbol", default="EURUSD")
    b.add_argument("--bars", type=int, default=3000)
    b.add_argument("--seed", type=int, default=42)
    b.add_argument("--splits", action="store_true", help="Run TRAIN/VAL/OOS splits")
    b.add_argument("--config", default="config/strategy_v1.yaml")
    b.set_defaults(func=cmd_backtest)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        # convenience: python main.py --mode backtest ...
        if args.mode == "backtest":
            return cmd_backtest(
                argparse.Namespace(
                    symbol="EURUSD",
                    bars=3000,
                    seed=42,
                    splits=False,
                    config=args.config,
                )
            )
        parser.print_help()
        print(
            "\nNote: paper/live/notifications not enabled in ÉTAPE 4 "
            "(backtest engine only)."
        )
        return 0
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
