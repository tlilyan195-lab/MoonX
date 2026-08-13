#!/usr/bin/env python3
"""CLI entrypoint — signals-only bot (no order execution)."""

from __future__ import annotations

import argparse
import hashlib
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
from trading_signal_bot.live_safe import (  # noqa: E402
    LiveSafeConfig,
    check_oos_live_ready,
    filter_symbol_performance,
    run_paper_live,
)

_COMMANDS = ("backtest", "calibrate", "oos-eval", "walk-forward", "paper_live")


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


def _m5_close_fingerprint(bundle: MultiTimeframeBundle) -> tuple[str, list[float]]:
    """SHA256 of M5 closes + first 3 closes for per-symbol data identity checks."""
    closes = bundle.m5.df["close"].astype(float).to_numpy()
    digest = hashlib.sha256(closes.tobytes()).hexdigest()[:16]
    first3 = [float(x) for x in closes[:3]]
    return digest, first3


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
    live_safe = bool(getattr(args, "live_safe", False))
    live_cfg = LiveSafeConfig()
    print("BACKTEST INCLUDE OOS:", include_oos)
    print("LIVE SAFE:", live_safe)
    if live_safe:
        print("V9 LIVE SAFE MODE ENABLED")

    symbols = [
        s.strip()
        for s in (
            args.symbols
            if hasattr(args, "symbols") and args.symbols
            else args.symbol
        ).split(",")
        if s.strip()
    ]
    if not symbols:
        symbols = [str(args.symbol)]

    by_symbol: dict = {}
    symbol_filter_report: dict = {}
    allowed_symbols: list[str] = []
    seen_data_hashes: dict[str, str] = {}
    live_ready_flags: dict[str, bool] = {}

    for symbol in symbols:
        print(f"===== {symbol} =====")
        bundle = _bundle_from_synthetic(symbol, args.bars, args.seed)
        # Per-symbol data identity: independent OHLC series required
        data_hash, first3 = _m5_close_fingerprint(bundle)
        print(f"DATA {symbol}: hash={data_hash} first3_closes={first3}")
        if data_hash in seen_data_hashes:
            other = seen_data_hashes[data_hash]
            raise AssertionError(
                f"Identical OHLC series for {symbol} and {other} "
                f"(hash={data_hash}). Symbol routing/data loading is broken."
            )
        seen_data_hashes[data_hash] = symbol
        assert bundle.symbol == symbol, f"bundle.symbol={bundle.symbol!r} != {symbol!r}"
        if args.splits:
            results = run_split_backtests(
                bundle,
                cfg,
                include_oos=include_oos,
                live_safe=live_safe,
                live_cfg=live_cfg,
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

            # V9 symbol performance filter (always when splits; LIVE SAFE pipeline)
            val_block = payload.get("VAL") or {}
            train_block = payload.get("TRAIN") or {}
            filt = filter_symbol_performance(
                symbol,
                val_block.get("metrics") or {},
                (val_block.get("meta") or {}).get("val_monte_carlo") or {},
                cfg=live_cfg,
                train_metrics=train_block.get("metrics") or {},
            )
            symbol_filter_report[symbol] = {
                "allowed": filt.allowed,
                "reasons": filt.reasons,
                "val_expectancy_r": filt.val_expectancy_r,
                "p_exp_le_0": filt.p_exp_le_0,
                "max_drawdown_r": filt.max_drawdown_r,
            }
            print(
                f"SYMBOL FILTER {symbol}: "
                f"{'PASS' if filt.allowed else 'REJECT'} {filt.reasons or ''}"
            )
            if filt.allowed:
                allowed_symbols.append(symbol)
            else:
                payload["symbol_filter_rejected"] = True
                if live_safe:
                    # Strict: when --live-safe, rejected symbols stay excluded
                    payload["live_safe_symbol_rejected"] = True

            # OOS live-ready enforcement
            if include_oos and "OOS" in payload:
                ready, oos_reasons = check_oos_live_ready(
                    payload["OOS"].get("metrics") or {}, cfg=live_cfg
                )
                live_ready_flags[symbol] = ready
                payload["live_ready"] = ready
                payload["live_ready_reasons"] = oos_reasons
                if not ready:
                    print("❌ STRATEGY NOT LIVE READY")
                    print(f"   {symbol}: {oos_reasons}")
                else:
                    print(f"✅ LIVE READY: {symbol}")
        else:
            res = run_backtest_on_bundle(
                bundle, cfg, live_safe=live_safe, live_cfg=live_cfg
            )
            payload = {
                "metrics": res.metrics.to_dict(),
                "n_decisions": len(res.decisions),
                "config_hash": res.config_hash,
                "meta": res.meta,
                "includes_oos_metrics": False,
            }
            allowed_symbols.append(symbol)
        by_symbol[symbol] = payload

    summary = _build_summary(by_symbol)
    summary["symbol_filter"] = symbol_filter_report
    summary["allowed_symbols"] = allowed_symbols
    summary["live_safe"] = live_safe
    if live_ready_flags:
        summary["live_ready"] = live_ready_flags

    # Keep only symbols that passed the performance filter when splits were used
    if args.splits:
        filtered = {
            sym: payload
            for sym, payload in by_symbol.items()
            if sym in allowed_symbols
        }
        rejected = [s for s in by_symbol if s not in allowed_symbols]
        if rejected:
            print(f"SYMBOLS EXCLUDED BY FILTER: {rejected}")
        report_symbols = filtered if filtered else by_symbol
    else:
        report_symbols = by_symbol

    if compact:
        compact_rows = [
            _compact_symbol_view(sym, payload) for sym, payload in report_symbols.items()
        ]
        out = {
            "summary": summary,
            "symbols": compact_rows,
            "excluded_symbols": [s for s in by_symbol if s not in allowed_symbols]
            if args.splits
            else [],
        }
        print(json.dumps(out, indent=2, default=str))
        return 0

    if len(report_symbols) == 1 and not args.splits:
        print(json.dumps(next(iter(report_symbols.values())), indent=2, default=str))
    else:
        print(
            json.dumps(
                {
                    "summary": summary,
                    "symbols": report_symbols,
                    "excluded_symbols": {
                        s: by_symbol[s] for s in by_symbol if s not in allowed_symbols
                    }
                    if args.splits
                    else {},
                },
                indent=2,
                default=str,
            )
        )
    return 0


def cmd_paper_live(args: argparse.Namespace) -> int:
    """V9 paper trading mode — causal simulation with LIVE SAFE filters."""
    cfg = StrategyConfig.from_yaml(args.config)
    live_cfg = LiveSafeConfig()
    # paper_live is always LIVE SAFE; --live-safe is accepted for CLI compatibility
    live_safe = bool(getattr(args, "live_safe", True))
    if live_safe:
        print("V9 LIVE SAFE MODE ENABLED")
    symbol = str(args.symbol)
    print(f"PAPER LIVE: {symbol} capital={args.capital}")
    bundle = _bundle_from_synthetic(symbol, args.bars, args.seed)

    # Always fit TRAIN regime stats + symbol performance gate (no OOS peeking)
    splits = run_split_backtests(
        bundle, cfg, include_oos=False, live_safe=False, live_cfg=live_cfg
    )
    by_regime = splits["TRAIN"].metrics.by_regime
    val = splits["VAL"]
    filt = filter_symbol_performance(
        symbol,
        val.metrics.to_dict(),
        (val.meta or {}).get("val_monte_carlo") or {},
        cfg=live_cfg,
        train_metrics=splits["TRAIN"].metrics.to_dict(),
    )
    print(
        f"SYMBOL FILTER {symbol}: "
        f"{'PASS' if filt.allowed else 'REJECT'} {filt.reasons or ''}"
    )
    if not filt.allowed:
        print("❌ STRATEGY NOT LIVE READY")
        print(json.dumps({"symbol_filter": filt.__dict__}, indent=2, default=str))
        return 1

    result = run_paper_live(
        bundle,
        cfg,
        live_cfg=live_cfg,
        by_regime=by_regime,
        capital=float(args.capital),
        warmup_bars=args.warmup,
    )
    print(
        json.dumps(
            {
                "mode": "paper_live",
                "symbol": symbol,
                "live_safe": live_safe,
                "n_trades": len(result.trades),
                "n_rejected": len(result.rejected),
                "risk_summary": {
                    k: v
                    for k, v in result.risk_summary.items()
                    if k != "trades"
                },
                "trades": [
                    {
                        "entry": t.entry,
                        "SL": t.sl,
                        "TP": t.tp,
                        "RR": t.rr,
                        "timestamp": t.timestamp,
                        "direction": t.direction,
                        "score": t.score,
                        "confidence_score": t.confidence_score,
                        "regime": t.regime,
                        "decision_reason": t.decision_reason,
                        "outcome": t.outcome,
                        "pnl_R": t.pnl_r,
                        "risk_pct": t.risk_pct,
                    }
                    for t in result.trades
                ],
                "messages": result.messages,
            },
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
    parser.add_argument(
        "--symbols",
        default="",
        help="(global) Comma-separated symbols; prefer: backtest --symbols ...",
    )

    # Shared V9 flag — attached to backtest + paper_live via parents=
    live_safe_parent = argparse.ArgumentParser(add_help=False)
    live_safe_parent.add_argument(
        "--live-safe",
        action="store_true",
        help="Enable V9 Live Safe risk & filtering layer",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # backtest — home for --symbols, --include-oos, --live-safe
    b = sub.add_parser(
        "backtest",
        parents=[live_safe_parent],
        help="Run backtest engine",
    )
    b.add_argument("--symbol", default="EURUSD", help="Single symbol")
    b.add_argument(
        "--symbols",
        default="",
        help="Comma-separated symbols (overrides --symbol)",
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

    p = sub.add_parser(
        "paper_live",
        parents=[live_safe_parent],
        help="V9 paper trading mode (causal, LIVE SAFE)",
    )
    p.add_argument("--symbol", default="EURUSD")
    p.add_argument("--bars", type=int, default=3000)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--capital", type=float, default=100000.0)
    p.add_argument("--warmup", type=int, default=500)
    p.add_argument("--config", default="config/strategy_v1.yaml")
    p.set_defaults(func=cmd_paper_live)

    return parser


def _normalize_argv(argv: list[str] | None) -> list[str] | None:
    """
    Force subcommand-first order so flags like --live-safe / --symbols are
    attached to the backtest subparser even if placed before the command.
    Example:  --live-safe backtest  →  backtest --live-safe
    """
    raw = list(sys.argv[1:] if argv is None else argv)
    cmd_idx = next((i for i, a in enumerate(raw) if a in _COMMANDS), None)
    if cmd_idx is None or cmd_idx == 0:
        return None if argv is None else raw
    cmd = raw[cmd_idx]
    return [cmd, *raw[:cmd_idx], *raw[cmd_idx + 1 :]]


def _backtest_has_live_safe(parser: argparse.ArgumentParser) -> bool:
    choices = parser._subparsers._group_actions[0].choices  # type: ignore[attr-defined]
    bt = choices["backtest"]
    return "--live-safe" in bt._option_string_actions


def main(argv: list[str] | None = None) -> int:
    print("USING FILE:", __file__)
    parser = build_parser()
    if not _backtest_has_live_safe(parser):
        raise RuntimeError("--live-safe failed to register on backtest subparser")
    print("V9 LIVE SAFE CLI READY")
    normalized = _normalize_argv(argv)
    args = parser.parse_args(normalized)
    # If --symbols was passed globally before subcommand, forward to backtest
    if (
        getattr(args, "command", None) == "backtest"
        and not str(getattr(args, "symbols", "") or "").strip()
    ):
        root_ns, _ = parser.parse_known_args(normalized)
        if str(getattr(root_ns, "symbols", "") or "").strip():
            args.symbols = root_ns.symbols
    # Manual fallback: if argv contained --live-safe, force the attribute
    raw = list(sys.argv[1:] if argv is None else argv)
    if "--live-safe" in raw:
        args.live_safe = True
    print("ARGS:", vars(args))
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
