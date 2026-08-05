# ÉTAPE 5.1 — Pilot quality report (3 months)

**Status:** awaiting user validation before 3y download / TRAIN/VAL.  
**No optimization. No OOS. No SMC rule changes. Signals-only.**

## Snapshot

| Field | Value |
|---|---|
| Period UTC | 2025-10-01 → 2025-12-31 |
| Directory | `data/snapshots/pilot_3m_2025Q4/` (local, gitignored) |
| `data_snapshot_id` | `sha256:ab93d7e8fcc2a3d3faeef156d5cfb22618c44312423bfbe2fbc8721cb06467db` |
| File hash verification | **OK** (all parquet hashes match manifest) |
| Rejected symbols | **none** |

## Provider by symbol

| Symbol | Provider | Notes |
|---|---|---|
| EURUSD | `dukascopy_historical_ticks` | Mid=(bid+ask)/2; ticks→5M→agg |
| GBPUSD | `dukascopy_historical_ticks` | idem |
| USDJPY | `dukascopy_historical_ticks` | idem |
| XAUUSD | `dukascopy_historical_ticks` | idem; more gaps than FX majors |
| BTCUSDT | `binance_usdm_futures_public` | via **data.binance.vision** daily zips (fapi geo-blocked in this env) |
| ETHUSDT | `binance_usdm_futures_public` | idem |

## Bars / missing / gaps / OHLC

| Symbol | 5M bars | Missing % est. | Gaps | OHLC anomalies | UTC close-time | DST PDH check |
|---|---:|---:|---:|---:|---|---|
| EURUSD | 18672 | 1.24% | 20 | 0 | OK | OK |
| GBPUSD | 18673 | 1.24% | 15 | 0 | OK | OK |
| USDJPY | 18700 | 1.09% | 7 | 0 | OK | OK |
| XAUUSD | 17732 | 6.21% | 60 | 0 | OK | OK |
| BTCUSDT | 26495 | 0.00% | 0 | 0 | OK | n/a |
| ETHUSDT | 26495 | 0.00% | 0 | 0 | OK | n/a |

FX price convention: **MID**. Higher TF = aggregation from 5M (`label=right, closed=right`).  
Crypto: native TF + comparison vs agg-from-5M → **max_close_rel_diff = 0** on overlapping bars (15M/1H/4H).

## Reproducibility (two identical `evaluate()` runs)

All 6 symbols: **runs_identical = true**.

## News / Finnhub

| Field | Value |
|---|---|
| Key present | **No** (`FINNHUB_API_KEY` absent) |
| Historical blackout usable | **False** |
| Action | **STOP before TRAIN/VAL** if news gate required — do not invent events |

## Caveats for next step

1. Dukascopy feed is flaky (connection resets); retries + local bi5 cache used.
2. Binance USD-M live `fapi` is geo-restricted here; historical Vision archive used (still public, no key, no trading).
3. FX `MultiTimeframeBundle.quality()` may flag session gaps as `GAP` during evaluate — separate from snapshot quality; revisit gap policy before TRAIN/VAL if needed.
4. XAUUSD not rejected (missing &lt; 40%) but gap count is higher — confirm keep vs disable before 3y.

## Commands

```bash
python -m pytest -q tests/test_data_providers.py
python scripts/build_pilot_snapshot.py
```
