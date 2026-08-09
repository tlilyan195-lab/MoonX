# ÉTAPE 5 — D1/D2 lock + TRAIN/VAL sanity

## Locked providers (V1)

| Layer | Provider | Notes |
|---|---|---|
| **D1 market** | Dukascopy (FX/XAU mid) + Binance Futures (crypto) | Pilot OHLC |
| **D2 news** | **JBlanked** `mql5/calendar/range` | Locked after Free `PASS_SAMPLE` |

## D2 NEWS GATE rules (frozen)

1. Scheduled `Date` only → normalize as **UTC** (`tz_localize`, no guesswork)
2. Gate membership: **Impact == High** AND **Currency** ∈ symbol map
3. Ignore for timing/membership: `Actual`, `Outcome`, `Strength`, `Quality`
4. Missing / zero `Event_ID` → `fingerprint(Name|Date|Currency)`
5. Missing events → **no-event** (empty blackout; never synthetic fill)

News map (unchanged): EURUSD→EUR+USD, GBPUSD→GBP+USD, USDJPY→USD+JPY, XAUUSD→USD High, crypto→no macro gate.

Blackout window: `news.major_window_minutes` (60) around High events on 5M closes.

## Build snapshot

```bash
# Optional: seed partial week from pushed Free sample (real rows only)
python3 scripts/seed_jblanked_news_from_sample.py

# Preferred: live Free fetch (1 req/day) into cache, or pass existing cache
# export JBLANKED_API_KEY=...   # in .env only

python3 scripts/build_d1d2_snapshot.py \
  --news-cache data/cache/jblanked/events_pilot.parquet
```

Outputs under `data/snapshots/pilot_3m_2025Q4_d1d2/` (gitignored):
- `{SYM}_{TF}.parquet` (D1)
- `news_jblanked_events.parquet` (D2)
- `manifest.json` with **`data_snapshot_id`** hashing all files including news

## Snapshot built (this run)

- Dir: `data/snapshots/pilot_2025_10_d1d2/` (gitignored)
- `data_snapshot_id`: `sha256:dee051b2b8a16fa029c7cc1ad8a27a6e1b56db2281c2e84a17962b79b4095bd0`
- Period: 2025-10-01 → 2025-10-31 UTC (1-month pilot; full Q4 download rate-limited in cloud)
- D2 news: 8 events from Free PASS sample seed (1 High = CHF → no FX blackout on mapped pairs)
- Summary: `docs/ETAPE_5_D1D2_SNAPSHOT_SUMMARY.json`

## TRAIN/VAL sanity (no OOS, no hyperparam search)

```bash
python3 scripts/run_train_val_sanity.py --snapshot-dir data/snapshots/pilot_3m_2025Q4_d1d2
```

Report: `TRAIN_VAL_SANITY.json` + `docs/ETAPE_5_TRAIN_VAL_SANITY.md`

Goal: frequency, category/direction distribution, basic expectancy/PF sanity — **not** maximize win rate.

## Code

- `trading_signal_bot/data/jblanked_calendar.py`
- `trading_signal_bot/data/news_blackout.py`
- `scripts/build_d1d2_snapshot.py`
- `scripts/run_train_val_sanity.py`
- `tests/test_jblanked_d2_news_gate.py`
