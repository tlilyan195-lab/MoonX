#!/usr/bin/env python3
"""
ÉTAPE 5 — Build combined D1+D2 data snapshot (data_snapshot_id).

D1 market: Dukascopy FX/XAU + Binance crypto (pilot window).
D2 news: JBlanked (locked) — scheduled Date UTC, High+Currency gate fields only.

No OOS. No optimization. No strategy rule changes.
No synthetic news fill (missing → no-event).
"""

from __future__ import annotations

import argparse
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
from trading_signal_bot.data.finnhub_calendar import HistoricalCoverageError
from trading_signal_bot.data.jblanked_calendar import (
    PROVIDER_NAME as NEWS_PROVIDER,
    JBlankedEconomicCalendarProvider,
    load_events_parquet,
    save_events_parquet,
)
from trading_signal_bot.data.providers import aggregate_ohlcv
from trading_signal_bot.data.quality import evaluate_symbol_frame
from trading_signal_bot.data.snapshot import SnapshotBuilder, verify_snapshot_hashes
from trading_signal_bot.strategy.engine import evaluate

PILOT_START = pd.Timestamp("2025-10-01 00:00:00+00:00")
PILOT_END = pd.Timestamp("2025-12-31 23:59:59+00:00")
DEFAULT_SNAPSHOT = ROOT / "data" / "snapshots" / "pilot_3m_2025Q4_d1d2"
FX_SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]
CRYPTO_SYMBOLS = ["BTCUSDT", "ETHUSDT"]


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def git_commit() -> str | None:
    try:
        return (
            subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT)
            .decode()
            .strip()
        )
    except Exception:
        return None


def build_fx(provider: DukascopyHistoricalProvider, symbol: str, start, end):
    print(f"[D1/FX] {symbol} ticks→5M ...", flush=True)
    m5 = provider.fetch_ohlcv(symbol, "5M", start, end)
    meta = dict(provider.last_fetch_meta)
    higher = {
        "15M": aggregate_ohlcv(m5, "15M"),
        "1H": aggregate_ohlcv(m5, "1H"),
        "4H": aggregate_ohlcv(m5, "4H"),
    }
    return m5, higher, meta


def build_crypto(provider: BinanceFuturesPublicProvider, symbol: str, start, end):
    print(f"[D1/CRYPTO] {symbol} native TF ...", flush=True)
    frames = {
        tf: provider.fetch_ohlcv(symbol, tf, start, end)  # type: ignore[arg-type]
        for tf in ("5M", "15M", "1H", "4H")
    }
    for tf, fr in frames.items():
        print(f"  {symbol} {tf}: {len(fr.df)} bars", flush=True)
    return frames


def reproducibility_check(bundle: MultiTimeframeBundle, cfg: StrategyConfig) -> dict:
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
    )
    return {"ok": same, "ts": str(ts), "decision": a.decision, "runs_identical": same}


def fetch_or_load_news(
    start: pd.Timestamp,
    end: pd.Timestamp,
    cache_path: Path,
    allow_empty: bool,
) -> tuple[pd.DataFrame, dict]:
    meta: dict = {
        "provider": NEWS_PROVIDER,
        "provider_locked_d2": True,
        "timezone": "UTC",
        "gate": "impact_high_plus_currency",
        "scheduled_date_only": True,
        "ignore_fields": ["Actual", "Outcome", "Strength", "Quality"],
        "missing_event_policy": "no_event_no_synthetic_fill",
        "source": None,
    }
    if cache_path.is_file():
        df = load_events_parquet(cache_path)
        # Keep all impacts in snapshot store; gate filters at runtime
        meta["source"] = "cache_parquet"
        meta["cache_path"] = str(cache_path)
        meta["n_events"] = int(len(df))
        meta["n_high"] = int((df["impact"].astype(str).str.lower() == "high").sum()) if len(df) else 0
        print(f"[D2] Loaded news cache: {len(df)} events from {cache_path}", flush=True)
        return df, meta

    key = os.environ.get("JBLANKED_API_KEY", "").strip()
    if not key:
        meta["source"] = "missing"
        meta["n_events"] = 0
        meta["error"] = "JBLANKED_API_KEY absent and no --news-cache"
        if allow_empty:
            print("[D2] No key/cache — storing empty news (no-event policy).", flush=True)
            return load_events_parquet(""), meta
        raise SystemExit(
            "D2 news required: set JBLANKED_API_KEY or pass --news-cache PATH. "
            "Refusing to invent events."
        )

    provider = JBlankedEconomicCalendarProvider(api_key=key)
    try:
        # Store full normalized calendar (all impacts); gate applies High at use-time.
        df = provider.fetch_events(start, end, high_impact_only=False)
        meta["source"] = "live_api"
        meta.update(provider.last_fetch_meta)
        meta["n_events"] = int(len(df))
        meta["n_high"] = int((df["impact"].astype(str).str.lower() == "high").sum()) if len(df) else 0
        save_events_parquet(df, cache_path)
        print(f"[D2] Fetched {len(df)} events; cached → {cache_path}", flush=True)
        return df, meta
    except HistoricalCoverageError as exc:
        meta["source"] = "error"
        meta["error"] = str(exc)
        if allow_empty:
            print(f"[D2] Fetch failed ({exc}); empty news under allow-empty.", flush=True)
            return load_events_parquet(""), meta
        raise SystemExit(f"D2 news fetch failed: {exc}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description="Build D1+D2 snapshot")
    parser.add_argument("--snapshot-dir", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--start", default=str(PILOT_START.date()))
    parser.add_argument("--end", default=str(PILOT_END.date()))
    parser.add_argument(
        "--news-cache",
        type=Path,
        default=None,
        help="Optional parquet of normalized JBlanked events (offline)",
    )
    parser.add_argument(
        "--allow-empty-news",
        action="store_true",
        help="Allow empty D2 (no-event) if key/cache missing — for market-only dry runs",
    )
    parser.add_argument("--skip-market", action="store_true", help="Reuse existing OHLC parquet in dir")
    parser.add_argument("--fx-only", action="store_true")
    args = parser.parse_args()

    _load_dotenv(ROOT / ".env")
    start = pd.Timestamp(args.start, tz="UTC")
    end = pd.Timestamp(args.end, tz="UTC") + pd.Timedelta(hours=23, minutes=59, seconds=59)
    snap_dir: Path = args.snapshot_dir
    snap_dir.mkdir(parents=True, exist_ok=True)
    news_cache = args.news_cache or (ROOT / "data" / "cache" / "jblanked" / "events_pilot.parquet")

    cfg = StrategyConfig.from_yaml()
    duka = DukascopyHistoricalProvider(price_side="mid", max_workers=4, pause_s=0.05)
    bnc = BinanceFuturesPublicProvider()

    providers_map = {
        "D1_FX": duka.provider_name,
        "D1_XAU": duka.provider_name,
        "D1_CRYPTO": bnc.provider_name,
        "D2_NEWS": NEWS_PROVIDER,
        "D2_LOCKED": True,
        "D2_RULES": {
            "timezone": "UTC",
            "scheduled_date_only": True,
            "gate": "Impact High + Currency",
            "ignore": ["Actual", "Outcome", "Strength", "Quality"],
            "missing_event_id": "fingerprint(Name|Date|Currency)",
            "missing_events": "no_event",
        },
    }
    builder = SnapshotBuilder(
        root=snap_dir,
        providers=providers_map,
        range_start=str(start),
        range_end=str(end),
        git_commit=git_commit(),
        aggregation={
            "FX_XAU": "ticks_mid_to_5M_then_aggregate_right_closed_right",
            "CRYPTO": "native_binance_klines",
            "price_convention_fx": "mid=(bid+ask)/2",
            "news_timezone": "UTC",
        },
    )

    file_hashes: dict[str, str] = {}
    symbol_reports: list[dict] = []
    rejected: list[str] = []
    repro: dict[str, dict] = {}
    provider_by_symbol: dict[str, str] = {}

    if not args.skip_market:
        for sym in FX_SYMBOLS:
            asset = "XAU" if sym == "XAUUSD" else "FX"
            provider_by_symbol[sym] = duka.provider_name
            try:
                m5, higher, meta = build_fx(duka, sym, start, end)
                for fr in (m5, *higher.values()):
                    file_hashes[f"{fr.symbol}_{fr.timeframe}.parquet"] = builder.add_frame(fr)
                report = evaluate_symbol_frame(
                    m5,
                    asset_class=asset,
                    provider=duka.provider_name,
                    period_start=start,
                    period_end=end,
                    frame_5m=m5,
                    native_higher=None,
                )
                report.extras["dukascopy_meta"] = meta
                if report.rejected or m5.df.empty:
                    rejected.append(sym)
                symbol_reports.append(report.to_dict())
                if not report.rejected and not m5.df.empty:
                    bundle = MultiTimeframeBundle(
                        sym, asset, m5, higher["15M"], higher["1H"], higher["4H"]  # type: ignore[arg-type]
                    )
                    repro[sym] = reproducibility_check(bundle, cfg)
            except Exception as exc:  # noqa: BLE001
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

        if not args.fx_only:
            for sym in CRYPTO_SYMBOLS:
                provider_by_symbol[sym] = bnc.provider_name
                try:
                    frames = build_crypto(bnc, sym, start, end)
                    for fr in frames.values():
                        file_hashes[f"{fr.symbol}_{fr.timeframe}.parquet"] = builder.add_frame(fr)
                    report = evaluate_symbol_frame(
                        frames["5M"],
                        asset_class="CRYPTO",
                        provider=bnc.provider_name,
                        period_start=start,
                        period_end=end,
                        frame_5m=frames["5M"],
                        native_higher={tf: frames[tf] for tf in ("15M", "1H", "4H")},
                    )
                    if report.rejected:
                        rejected.append(sym)
                    symbol_reports.append(report.to_dict())
                    if not report.rejected:
                        bundle = MultiTimeframeBundle(
                            sym, "CRYPTO", frames["5M"], frames["15M"], frames["1H"], frames["4H"]
                        )
                        repro[sym] = reproducibility_check(bundle, cfg)
                except Exception as exc:  # noqa: BLE001
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
    else:
        # Hash existing market files
        for p in sorted(snap_dir.glob("*.parquet")):
            if p.name.startswith("news_"):
                continue
            from trading_signal_bot.data.snapshot import _file_sha256

            file_hashes[p.name] = _file_sha256(p)

    news_df, news_meta = fetch_or_load_news(start, end, news_cache, args.allow_empty_news)
    news_name = "news_jblanked_events.parquet"
    news_path = snap_dir / news_name
    save_events_parquet(news_df, news_path)
    from trading_signal_bot.data.snapshot import _file_sha256

    file_hashes[news_name] = _file_sha256(news_path)

    quality_report = {
        "pilot": True,
        "d1_d2_locked": True,
        "period_utc": {"start": str(start), "end": str(end)},
        "provider_by_symbol": provider_by_symbol,
        "symbols": symbol_reports,
        "rejected_symbols": sorted(set(rejected)),
        "reproducibility": repro,
        "news_d2": news_meta,
        "notes": [
            "D2 locked: jblanked_mql5_calendar",
            "News TZ frozen UTC; scheduled Date only",
            "Gate: Impact High + Currency; no Actual/Outcome/Strength/Quality",
            "Missing events = no-event (no synthetic fill)",
            "No optimization. No OOS.",
        ],
    }
    snapshot_id, manifest_path = builder.finalize(
        file_hashes,
        quality_report,
        extras={
            "d1": "dukascopy_fx_xau + binance_crypto",
            "d2": NEWS_PROVIDER,
            "news_file": news_name,
        },
    )
    hash_ok = verify_snapshot_hashes(snap_dir)
    summary = {
        "data_snapshot_id": snapshot_id,
        "manifest_path": str(manifest_path),
        "snapshot_dir": str(snap_dir),
        "period_utc": {"start": str(start), "end": str(end)},
        "rejected_symbols": sorted(set(rejected)),
        "all_hashes_ok": all(hash_ok.values()) if hash_ok else False,
        "news_d2": news_meta,
        "providers": providers_map,
    }
    (snap_dir / "D1D2_SNAPSHOT_SUMMARY.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
