#!/usr/bin/env python3
"""
ÉTAPE 5.1A — Reclassify pilot FX/XAU gaps with Dukascopy trading calendars.
NO download of 3y. NO TRAIN/VAL. NO OOS. NO SMC changes. NO interpolation.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd

from trading_signal_bot.data.news_coverage import assess_finnhub_historical_coverage
from trading_signal_bot.data.quality import evaluate_symbol_frame
from trading_signal_bot.data.snapshot import read_frame_parquet

PILOT_START = pd.Timestamp("2025-10-01 00:00:00+00:00")
PILOT_END = pd.Timestamp("2025-12-31 23:59:59+00:00")
SNAPSHOT_DIR = ROOT / "data" / "snapshots" / "pilot_3m_2025Q4"

SYMBOLS = {
    "EURUSD": ("FX", "dukascopy_historical_ticks"),
    "GBPUSD": ("FX", "dukascopy_historical_ticks"),
    "USDJPY": ("FX", "dukascopy_historical_ticks"),
    "XAUUSD": ("XAU", "dukascopy_historical_ticks"),
    "BTCUSDT": ("CRYPTO", "binance_usdm_futures_public"),
    "ETHUSDT": ("CRYPTO", "binance_usdm_futures_public"),
}

# Soft threshold for XAU V1 after calendar correction
XAU_UNEXPECTED_PCT_REJECT = 2.0


def main() -> int:
    rows = []
    reports = []
    for symbol, (asset_class, provider) in SYMBOLS.items():
        path = SNAPSHOT_DIR / f"{symbol}_5M.parquet"
        if not path.exists():
            print(f"MISSING {path}", flush=True)
            continue
        frame = read_frame_parquet(symbol, "5M", path)
        rep = evaluate_symbol_frame(
            frame,
            asset_class=asset_class,
            provider=provider,
            period_start=PILOT_START,
            period_end=PILOT_END,
            reject_missing_pct_above=XAU_UNEXPECTED_PCT_REJECT,
        )
        reports.append(rep.to_dict())
        rows.append(
            {
                "symbol": symbol,
                "asset_class": asset_class,
                "expected_trading_bars": rep.expected_trading_bars,
                "observed_bars": rep.n_bars,
                "scheduled_closed_bars": rep.scheduled_closed_bars,
                "unexpected_missing_bars": rep.unexpected_missing_bars,
                "unexpected_missing_pct": round(rep.unexpected_missing_pct, 4),
                "unexpected_gap_count": rep.unexpected_gap_count,
                "rejected": rep.rejected,
                "reject_reason": rep.reject_reason,
            }
        )
        print(
            f"{symbol}: expected={rep.expected_trading_bars} observed={rep.n_bars} "
            f"scheduled_closed={rep.scheduled_closed_bars} "
            f"unexpected_missing={rep.unexpected_missing_bars} "
            f"({rep.unexpected_missing_pct:.4f}%) gaps={rep.unexpected_gap_count}",
            flush=True,
        )

    xau = next((r for r in rows if r["symbol"] == "XAUUSD"), None)
    if xau is None:
        xau_verdict = {"verdict": "REJECT", "reason": "no_xau_frame"}
    elif xau["unexpected_missing_pct"] <= XAU_UNEXPECTED_PCT_REJECT and xau["unexpected_gap_count"] < 30:
        xau_verdict = {
            "verdict": "ACCEPT",
            "reason": (
                f"After Dukascopy XAU calendar (daily break + US holiday windows + "
                f"Christmas/NY dark sessions), unexpected_missing_pct="
                f"{xau['unexpected_missing_pct']:.4f}% "
                f"(gaps={xau['unexpected_gap_count']}) — residual holes are not material for V1."
            ),
            "threshold_pct": XAU_UNEXPECTED_PCT_REJECT,
        }
    else:
        xau_verdict = {
            "verdict": "REJECT",
            "reason": (
                f"Residual unexpected_missing_pct={xau['unexpected_missing_pct']:.4f}% "
                f"and/or unexpected_gap_count={xau['unexpected_gap_count']} remain material "
                f"after applying Dukascopy XAU scheduled closures."
            ),
            "threshold_pct": XAU_UNEXPECTED_PCT_REJECT,
        }

    news = assess_finnhub_historical_coverage()

    out = {
        "period_utc": {"start": str(PILOT_START), "end": str(PILOT_END)},
        "calendar": "dukascopy_fx_xau_v1",
        "no_interpolation": True,
        "symbols": rows,
        "xau_verdict": xau_verdict,
        "news_finnhub": news.to_dict(),
        "detail_reports": reports,
    }
    out_path = SNAPSHOT_DIR / "GAP_RECLASS_5_1A.json"
    out_path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    # Canonical narrative report is maintained in docs/ETAPE_5_1A_GAP_RECLASS.md
    brief = ROOT / "docs" / "ETAPE_5_1A_GAP_RECLASS_AUTO.md"
    _write_markdown(brief, out)
    print(f"Wrote {out_path}", flush=True)
    print(f"Wrote {brief}", flush=True)
    print(f"XAU verdict: {xau_verdict['verdict']}", flush=True)
    print(f"Finnhub verdict: {news.verdict}", flush=True)
    return 0


def _write_markdown(path: Path, out: dict) -> None:
    lines = [
        "# ÉTAPE 5.1A — Gap reclassification + news coverage",
        "",
        "**STOP before 5.1B.** No 3y download. No TRAIN/VAL. No OOS. No SMC changes.",
        "",
        "## Corrected FX/XAU/Crypto gap table (tradable calendar)",
        "",
        "| Symbol | expected_trading_bars | observed_bars | scheduled_closed_bars | unexpected_missing_bars | unexpected_missing_pct | unexpected_gap_count |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in out["symbols"]:
        lines.append(
            f"| {r['symbol']} | {r['expected_trading_bars']} | {r['observed_bars']} | "
            f"{r['scheduled_closed_bars']} | {r['unexpected_missing_bars']} | "
            f"{r['unexpected_missing_pct']:.4f}% | {r['unexpected_gap_count']} |"
        )
    xv = out["xau_verdict"]
    lines += [
        "",
        "## XAU verdict",
        "",
        f"**{xv['verdict']}** — {xv.get('reason', '')}",
        "",
        "## Finnhub historical availability",
        "",
    ]
    news = out["news_finnhub"]
    lines.append(f"**Verdict: {news['verdict']}** (usable_for_3y_blackout={news['usable_for_3y_blackout']})")
    lines.append("")
    for reason in news.get("reasons", []):
        lines.append(f"- {reason}")
    alt = news.get("alternative")
    if alt:
        lines += [
            "",
            "## Alternative news provider",
            "",
            f"- Provider: `{alt['provider']}`",
            f"- Why: {alt['why']}",
            f"- Docs: {alt['docs']}",
            f"- Field map: `{alt['required_fields_mapping']}`",
            f"- Notes: {alt['notes']}",
        ]
    lines += [
        "",
        "## Calendar rules applied",
        "",
        "- FX: Dukascopy week Sun 17:05 NY → Fri 17:00 NY (UTC hour shifts with US DST).",
        "- XAU: Sun 18:05 NY → Fri 17:00 NY; daily break Mon–Thu 17:00–18:00 NY; "
        "US holiday 13:00–18:00 NY; Christmas/NY dark; Christmas Eve & day-after-Thanksgiving early close.",
        "- Crypto: 24/7 expected grid.",
        "- No candle fill/interpolation.",
        "",
        "Machine-readable: `data/snapshots/pilot_3m_2025Q4/GAP_RECLASS_5_1A.json`",
        "",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
