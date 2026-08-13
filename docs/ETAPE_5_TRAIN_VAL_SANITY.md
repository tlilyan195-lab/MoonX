# ÉTAPE 5 — TRAIN/VAL sanity (D1+D2)

- data_snapshot_id: `sha256:dee051b2b8a16fa029c7cc1ad8a27a6e1b56db2281c2e84a17962b79b4095bd0`
- Snapshot dir: `data/snapshots/pilot_2025_10_d1d2` (2025-10-01→2025-10-31 UTC)
- D1: Dukascopy FX/XAU + Binance crypto
- D2 locked: `jblanked_mql5_calendar`
- News events in snapshot: `8` (high=1)
- TRAIN signals (all symbols): `0`
- VAL signals (all symbols): `0`
- OOS: **not run**
- Hyperparameter optimization: **not run**

## Verdict (sanity, not optimization)

- Signal frequency on this 1-month window: **0** TRAIN / **0** VAL across all symbols.
- FX/XAU: every probed bar → `data_quality:GAP` (bundle.quality() max-gap on session/weekend holes). This blocks strategy evaluation before news gate / SMC filters — known 5.1A follow-up; **no rule change in this step**.
- Crypto: passes quality; NO_TRADE dominated by `no_liquidity_sweep` / `htf_bias_misaligned` / `volatility_filter`.
- News blackout bars = 0 on mapped symbols: the only High in the partial seed is **CHF** (not in EUR/GBP/JPY/USD gate map). Missing weeks = no-event.

## Per symbol

- `EURUSD`: TRAIN n=0 (0.000/day); VAL n=0 (0.000/day); blackout_bars=0
  - no_trade_probe_TRAIN: `{'data_quality:GAP': 330}`
  - no_trade_probe_VAL: `{'data_quality:GAP': 110}`
- `GBPUSD`: TRAIN n=0 (0.000/day); VAL n=0 (0.000/day); blackout_bars=0
  - no_trade_probe_TRAIN: `{'data_quality:GAP': 330}`
  - no_trade_probe_VAL: `{'data_quality:GAP': 110}`
- `USDJPY`: TRAIN n=0 (0.000/day); VAL n=0 (0.000/day); blackout_bars=0
  - no_trade_probe_TRAIN: `{'data_quality:GAP': 330}`
  - no_trade_probe_VAL: `{'data_quality:GAP': 110}`
- `XAUUSD`: TRAIN n=0 (0.000/day); VAL n=0 (0.000/day); blackout_bars=0
  - no_trade_probe_TRAIN: `{'data_quality:GAP': 317}`
  - no_trade_probe_VAL: `{'data_quality:GAP': 106}`
- `BTCUSDT`: TRAIN n=0 (0.000/day); VAL n=0 (0.000/day); blackout_bars=0
  - no_trade_probe_TRAIN: `{'no_liquidity_sweep': 203, 'htf_bias_misaligned': 171, 'volatility_filter': 31, 'missing_bos_1h': 13, 'sweep_entry_distance': 8, 'not_in_premium': 8, 'no_valid_poi': 5, 'insufficient_bars': 4, 'no_5m_confirmation': 3, 'score_below_B': 1}`
  - no_trade_probe_VAL: `{'htf_bias_misaligned': 47, 'no_liquidity_sweep': 44, 'not_in_discount': 21, 'no_valid_poi': 20, 'no_5m_confirmation': 8, 'missing_bos_1h': 6, 'sweep_entry_distance': 3}`
- `ETHUSDT`: TRAIN n=0 (0.000/day); VAL n=0 (0.000/day); blackout_bars=0
  - no_trade_probe_TRAIN: `{'htf_bias_misaligned': 201, 'no_liquidity_sweep': 166, 'volatility_filter': 29, 'missing_bos_1h': 26, 'sweep_entry_distance': 6, 'no_5m_confirmation': 5, 'no_valid_poi': 5, 'insufficient_bars': 4, 'not_in_premium': 4, 'no_liquidity_tp_or_rr': 1}`
  - no_trade_probe_VAL: `{'htf_bias_misaligned': 72, 'no_liquidity_sweep': 35, 'not_in_premium': 15, 'missing_bos_1h': 12, 'no_valid_poi': 8, 'sweep_entry_distance': 4, 'no_5m_confirmation': 2, 'no_liquidity_tp_or_rr': 1}`

## D2 rules applied

- Scheduled Date only, TZ=UTC
- Gate = Impact High + Currency
- Ignore Actual/Outcome/Strength/Quality
- Missing Event_ID → fingerprint(Name|Date|Currency)
- Missing events → no-event (no synthetic fill)

## Artifacts

- `docs/ETAPE_5_D1D2_SNAPSHOT_SUMMARY.json`
- `docs/ETAPE_5_TRAIN_VAL_SANITY.json`
- `docs/ETAPE_5_D1D2_LOCK.md`
- Local snapshot (gitignored): `data/snapshots/pilot_2025_10_d1d2/`
