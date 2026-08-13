#!/usr/bin/env python3
"""
ÉTAPE 5.1 — Pilot 3-month data snapshot + quality report.
NO optimization. NO OOS. NO strategy changes.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd

from trading_signal_bot.config import StrategyConfig
from trading_signal_bot.data import MultiTimeframeBundle
from trading_signal_bot.data.binance_futures import BinanceFuturesPublicProvider
from trading_signal_bot.data.dukascopy import DukascopyHistoricalProvider
from trading_signal_bot.data.finnhub_calendar import (
    FinnhubEconomicCalendarProvider,
    HistoricalCoverageError,
)
from trading_signal_bot.data.providers import aggregate_ohlcv
from trading_signal_bot.data.quality import evaluate_symbol_frame
from trading_signal_bot.data.snapshot import SnapshotBuilder, verify_snapshot_hashes
from trading_signal_bot.strategy.engine import evaluate


# Pilot window includes US DST fall-back 2025-11-02
PILOT_START = pd.Timestamp("2025-10-01 00:00:00+00:00")
PILOT_END = pd.Timestamp("2025-12-31 23:59:59+00:00")
SNAPSHOT_DIR = ROOT / "data" / "snapshots" / "pilot_3m_2025Q4"

FX_SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]
CRYPTO_SYMBOLS = ["BTCUSDT", "ETHUSDT"]


def git_commit() -> str | None:
    try:
        return (
            subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT)
            .decode()
            .strip()
        )
    except Exception:
        return None


def build_fx(provider: DukascopyHistoricalProvider, symbol: str):
    print(f"[FX] Downloading ticks→5M for {symbol} ...", flush=True)
    m5 = provider.fetch_ohlcv(symbol, "5M", PILOT_START, PILOT_END)
    meta = dict(provider.last_fetch_meta)
    m15 = aggregate_ohlcv(m5, "15M")
    h1 = aggregate_ohlcv(m5, "1H")
    h4 = aggregate_ohlcv(m5, "4H")
    return m5, {"15M": m15, "1H": h1, "4H": h4}, meta


def build_crypto(provider: BinanceFuturesPublicProvider, symbol: str):
    print(f"[CRYPTO] Downloading native TF for {symbol} ...", flush=True)
    frames = {}
    for tf in ("5M", "15M", "1H", "4H"):
        frames[tf] = provider.fetch_ohlcv(symbol, tf, PILOT_START, PILOT_END)  # type: ignore[arg-type]
        print(f"  {symbol} {tf}: {len(frames[tf].df)} bars", flush=True)
    # Also build agg from 5M for comparison
    agg = {
        "15M": aggregate_ohlcv(frames["5M"], "15M"),
        "1H": aggregate_ohlcv(frames["5M"], "1H"),
        "4H": aggregate_ohlcv(frames["5M"], "4H"),
    }
    return frames, agg


def reproducibility_check(bundle: MultiTimeframeBundle, cfg: StrategyConfig) -> dict:
    """Two identical evaluate passes on a mid timestamp — strategy rules untouched."""
    if bundle.m5.df.empty or len(bundle.m5.df) < 100:
        return {"ok": False, "reason": "insufficient_bars"}
    ts = bundle.m5.df.index[len(bundle.m5.df) // 2]
    a = evaluate(bundle, ts, cfg)
    b = evaluate(bundle, ts, cfg)
    same = (
        a.decision == b.decision
        and a.category == b.category
        and a.setup_score == b.setup_score
        and a.entry == b.entry
        and a.sl == b.sl
        and a.tp1 == b.tp1
        and a.poi_id == b.poi_id
    )
    return {
        "ok": same,
        "ts": str(ts),
        "decision": a.decision,
        "explanation": a.explanation,
        "runs_identical": same,
    }


def check_finnhub() -> dict:
    key = os.environ.get("FINNHUB_API_KEY", "")
    out: dict = {
        "provider": "finnhub_economic_calendar",
        "key_present": bool(key),
        "status": "skipped_no_key",
        "usable_for_historical_blackout": False,
        "message": "",
    }
    if not key:
        out["message"] = (
            "FINNHUB_API_KEY absent — calendar not fetched. "
            "Interchangeable provider ready; STOP before TRAIN/VAL if news gate required "
            "without a covering historical feed."
        )
        out["status"] = "no_key"
        return out
    try:
        cal = FinnhubEconomicCalendarProvider(api_key=key)
        df = cal.high_impact_events(PILOT_START, PILOT_END)
        out["status"] = "ok"
        out["n_events"] = int(len(df))
        out["columns"] = list(df.columns)
        out["impact_values_sample"] = sorted(df["impact"].dropna().unique().tolist())[:20]
        out["usable_for_historical_blackout"] = True
        out["message"] = "Finnhub returned calendar rows for pilot window."
    except HistoricalCoverageError as exc:
        out["status"] = "coverage_error"
        out["usable_for_historical_blackout"] = False
        out["message"] = str(exc)
    except Exception as exc:  # network/other
        out["status"] = "error"
        out["usable_for_historical_blackout"] = False
        out["message"] = f"{type(exc).__name__}: {exc}"
    return out


def main() -> int:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    duka = DukascopyHistoricalProvider(price_side="mid", max_workers=4, pause_s=0.05)
    bnc = BinanceFuturesPublicProvider()
    cfg = StrategyConfig.from_yaml()

    providers_map = {
        "FX": duka.provider_name,
        "XAU": duka.provider_name,
        "CRYPTO": bnc.provider_name,
        "NEWS": "finnhub_economic_calendar (optional/interchangeable)",
    }
    builder = SnapshotBuilder(
        root=SNAPSHOT_DIR,
        providers=providers_map,
        range_start=str(PILOT_START),
        range_end=str(PILOT_END),
        git_commit=git_commit(),
        aggregation={
            "FX_XAU": "ticks_mid_to_5M_then_aggregate_right_closed_right",
            "CRYPTO": "native_binance_klines + compare_vs_agg_from_5M",
            "price_convention_fx": "mid=(bid+ask)/2",
        },
    )

    file_hashes: dict[str, str] = {}
    symbol_reports: list[dict] = []
    rejected: list[str] = []
    repro_results: dict[str, dict] = {}
    provider_by_symbol: dict[str, str] = {}

    # --- FX / XAU ---
    for sym in FX_SYMBOLS:
        asset = "XAU" if sym == "XAUUSD" else "FX"
        provider_by_symbol[sym] = duka.provider_name
        try:
            m5, higher, meta = build_fx(duka, sym)
            for tf_frame in (m5, *higher.values()):
                fname = f"{tf_frame.symbol}_{tf_frame.timeframe}.parquet"
                file_hashes[fname] = builder.add_frame(tf_frame)
            report = evaluate_symbol_frame(
                m5,
                asset_class=asset,
                provider=duka.provider_name,
                period_start=PILOT_START,
                period_end=PILOT_END,
                frame_5m=m5,
                native_higher=None,  # Dukascopy: aggregation-only higher TF
            )
            report.extras["dukascopy_meta"] = meta
            report.aggregation_compare = {
                tf: {
                    "target": tf,
                    "native_available": False,
                    "agg_bars": len(higher[tf].df),
                    "note": "Higher TF built only by aggregation from 5M mid ticks",
                }
                for tf in ("15M", "1H", "4H")
            }
            # Soft reject empty XAU
            if sym == "XAUUSD" and (m5.df.empty or report.rejected):
                report.rejected = True
                report.reject_reason = report.reject_reason or "xau_quality_gate_failed"
                rejected.append(sym)
            symbol_reports.append(report.to_dict())
            if not report.rejected and not m5.df.empty:
                bundle = MultiTimeframeBundle(
                    sym, asset, m5, higher["15M"], higher["1H"], higher["4H"]  # type: ignore[arg-type]
                )
                repro_results[sym] = reproducibility_check(bundle, cfg)
            else:
                repro_results[sym] = {"ok": False, "reason": "rejected_or_empty"}
        except Exception as exc:
            rejected.append(sym)
            symbol_reports.append(
                {
                    "symbol": sym,
                    "provider": duka.provider_name,
                    "rejected": True,
                    "reject_reason": f"{type(exc).__name__}: {exc}",
                    "n_bars": 0,
                }
            )
            repro_results[sym] = {"ok": False, "reason": str(exc)}

    # --- CRYPTO ---
    for sym in CRYPTO_SYMBOLS:
        provider_by_symbol[sym] = bnc.provider_name
        try:
            frames, agg = build_crypto(bnc, sym)
            print(f"  source={bnc.last_source}", flush=True)
            for tf, fr in frames.items():
                fname = f"{fr.symbol}_{fr.timeframe}.parquet"
                file_hashes[fname] = builder.add_frame(fr, filename=fname)
            for tf, fr in agg.items():
                fname = f"{fr.symbol}_{tf}_AGG_FROM_5M.parquet"
                file_hashes[fname] = builder.add_frame(fr, filename=fname)

            report = evaluate_symbol_frame(
                frames["5M"],
                asset_class="CRYPTO",
                provider=bnc.provider_name,
                period_start=PILOT_START,
                period_end=PILOT_END,
                frame_5m=frames["5M"],
                native_higher={tf: frames[tf] for tf in ("15M", "1H", "4H")},
            )
            from trading_signal_bot.data.quality import compare_native_vs_aggregated

            report.aggregation_compare = {
                tf: compare_native_vs_aggregated(frames["5M"], frames[tf], tf)  # type: ignore[arg-type]
                for tf in ("15M", "1H", "4H")
            }
            # also attach vs dedicated agg frames
            report.extras["agg_from_5m_bars"] = {tf: len(agg[tf].df) for tf in agg}
            if report.rejected:
                rejected.append(sym)
            symbol_reports.append(report.to_dict())
            if not report.rejected:
                bundle = MultiTimeframeBundle(
                    sym,
                    "CRYPTO",
                    frames["5M"],
                    frames["15M"],
                    frames["1H"],
                    frames["4H"],
                )
                repro_results[sym] = reproducibility_check(bundle, cfg)
            else:
                repro_results[sym] = {"ok": False, "reason": "rejected"}
        except Exception as exc:
            rejected.append(sym)
            symbol_reports.append(
                {
                    "symbol": sym,
                    "provider": bnc.provider_name,
                    "rejected": True,
                    "reject_reason": f"{type(exc).__name__}: {exc}",
                    "n_bars": 0,
                }
            )
            repro_results[sym] = {"ok": False, "reason": str(exc)}

    news_report = check_finnhub()

    quality_report = {
        "pilot": True,
        "period_utc": {"start": str(PILOT_START), "end": str(PILOT_END)},
        "provider_by_symbol": provider_by_symbol,
        "symbols": symbol_reports,
        "rejected_symbols": sorted(set(rejected)),
        "reproducibility": repro_results,
        "news": news_report,
        "notes": [
            "No optimization performed.",
            "No OOS consulted.",
            "No SMC rule changes.",
            "Signals-only — no order endpoints.",
            "Await user validation before 3y download and TRAIN/VAL.",
        ],
    }

    snapshot_id, manifest_path = builder.finalize(
        file_hashes,
        quality_report,
        extras={"pilot_window": "2025-10-01/2025-12-31"},
    )
    hash_ok = verify_snapshot_hashes(SNAPSHOT_DIR)

    summary = {
        "data_snapshot_id": snapshot_id,
        "manifest_path": str(manifest_path),
        "snapshot_dir": str(SNAPSHOT_DIR),
        "provider_by_symbol": provider_by_symbol,
        "period_utc": {"start": str(PILOT_START), "end": str(PILOT_END)},
        "rejected_symbols": sorted(set(rejected)),
        "file_hash_verification": hash_ok,
        "all_hashes_ok": all(hash_ok.values()) if hash_ok else False,
        "reproducibility": repro_results,
        "news": news_report,
        "symbols_brief": [
            {
                "symbol": r.get("symbol"),
                "provider": r.get("provider"),
                "n_bars": r.get("n_bars"),
                "missing_pct_estimate": r.get("missing_pct_estimate"),
                "n_gaps": r.get("n_gaps"),
                "n_ohlc_anomalies": r.get("n_ohlc_anomalies"),
                "rejected": r.get("rejected"),
                "reject_reason": r.get("reject_reason"),
            }
            for r in symbol_reports
        ],
    }
    out_path = SNAPSHOT_DIR / "PILOT_QUALITY_SUMMARY.json"
    out_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(json.dumps(summary, indent=2, default=str))
    print(f"\nWrote {out_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
