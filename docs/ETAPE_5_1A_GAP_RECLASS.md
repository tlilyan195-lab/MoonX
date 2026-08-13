# ÉTAPE 5.1A — Gap reclassification + news coverage

**STOP before 5.1B.** No 3y download. No TRAIN/VAL. No OOS. No SMC changes. No interpolation.

Pilot window: `2025-10-01 → 2025-12-31` UTC · snapshot `data/snapshots/pilot_3m_2025Q4/`

## 1. Corrected FX/XAU/Crypto gap table (tradable calendar)

| Symbol | expected_trading_bars | observed_bars | scheduled_closed_bars | unexpected_missing_bars | unexpected_missing_pct | unexpected_gap_count |
|---|---:|---:|---:|---:|---:|---:|
| EURUSD | 18601 | 18672 | 7871 | 96 | 0.5161% | 8 |
| GBPUSD | 18601 | 18673 | 7871 | 98 | 0.5269% | 10 |
| USDJPY | 18601 | 18700 | 7871 | 96 | 0.5161% | 8 |
| XAUUSD | 17605 | 17732 | 8867 | 88 | 0.4999% | 9 |
| BTCUSDT | 26495 | 26495 | 0 | 0 | 0.0000% | 0 |
| ETHUSDT | 26495 | 26495 | 0 | 0 | 0.0000% | 0 |

`missing_pct` for the quality gate is **`unexpected_missing_pct`** (denominator = expected tradable bars only).

### Gap classification (what the old 6.21% / 60 gaps were)

| Class | Meaning |
|---|---|
| `weekend_market_close` | Fri 17:00 NY → Sun open (FX 17:05 NY / XAU 18:05 NY) |
| `daily_trading_break` | XAU Mon–Thu 17:00–18:00 NY (= 21–22 UTC summer / 22–23 UTC winter) |
| `us_holiday_official_closed` | XAU US federal holiday afternoon 13:00–17:00 NY (+ break) |
| `full_holiday_session_closed` | Christmas Day / New Year (FX+XAU); XAU Christmas Eve & day-after-Thanksgiving early close |
| `unexpected_missing` | Hole inside an expected tradable slot — **not filled** |

Residual FX/XAU unexpected bars are almost entirely the **winter Friday 21:05–22:00 UTC** hour: Dukascopy historical ticks stop at 21:00 UTC every Friday while live settlement in winter is 22:00 UTC (17:00 NY). Counted as true archive missing (~0.5%), not interpolated.

## 2. unexpected_missing_pct (all assets)

| Symbol | unexpected_missing_pct |
|---|---:|
| EURUSD | **0.5161%** |
| GBPUSD | **0.5269%** |
| USDJPY | **0.5161%** |
| XAUUSD | **0.4999%** |
| BTCUSDT | **0.0000%** |
| ETHUSDT | **0.0000%** |

## 3. XAU verdict: **ACCEPT**

After applying the Dukascopy XAU calendar (Sunday open 18:00 NY, daily break, US holiday windows, Christmas/NY dark sessions, Christmas Eve & Black Friday early closes), pilot `unexpected_missing_pct` falls from the naive **6.21% / 60 gaps** to **0.50% / 9 gaps**. Residual holes match the same winter-Friday archive hour as FX majors — not material for V1. **No candles created or interpolated.**

## 4. Finnhub historical availability: **REJECT**

Not solely because `FINNHUB_API_KEY` is missing:

- Finnhub public pricing leaves **Economic Calendar** and **Historical Economic Data** off the Free plan (All-In-One only).
- V1 needs ≥3 years with publication timestamp, country/currency, event, impact/importance.
- Free entitlements do not provide that multi-year economic-calendar history; obtaining a free key would not validate the 3y blackout rebuild.

## 5. Alternative news provider

**Trading Economics Calendar API** (`trading_economics_calendar_api`)

| Need | TE field |
|---|---|
| timestamp | `Date` (UTC) |
| country/currency | `Country` |
| event | `Event` |
| impact | `Importance` (1/2/3) |

Docs: https://docs.tradingeconomics.com/economic_calendar/schema/  
Paid API — do **not** scrape HTML. Validate 3y EUR/GBP/JPY/USD high-importance coverage with a trial key before TRAIN/VAL.

## 6. Additional automated tests

```text
46 passed (full suite)
```

New coverage in `tests/test_etape_5_1a.py`:

- XAU daily break summer/winter + weekend/Christmas classification
- Gap analysis excludes scheduled XAU break (0 unexpected)
- `data_snapshot_id` changes when data file / period / provider / timestamp convention / aggregation rule change
- Same snapshot + config + commit → identical engine outputs (golden SIGNAL_LONG)
- Finnhub REJECT cites pricing/coverage, not only missing key; proposes Trading Economics

## 7. Commands

```bash
python3 -m pytest -q
python3 scripts/reclassify_pilot_gaps_5_1a.py
```

Machine-readable: `data/snapshots/pilot_3m_2025Q4/GAP_RECLASS_5_1A.json` (gitignored with snapshot).

Awaiting validation before ÉTAPE 5.1B.
