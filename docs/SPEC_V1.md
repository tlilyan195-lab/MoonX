# Trading Signal Bot V1 — Locked CDC summary (ÉTAPE 3→4)

Signals-only. No BUY/SELL/ORDER/TRANSFER/WITHDRAWAL permissions.

## Locked decisions

| ID | Decision |
|----|----------|
| T1 | PWH/PWL FX = Sun 17:00 NY → Fri 17:00 NY (`America/New_York`, DST-aware) |
| PDH/PDL FX | Trading day 17:00 NY → 17:00 NY |
| S1 | A+ requires FVG+OB overlap (`require_overlap_for_aplus=true`) |
| E1 | `max_hold_bars_5m=N` calibrable TRAIN/VAL; separate crypto param; freeze before OOS |
| E2 | No synthetic TP — NO_TRADE if no liquidity TP with RR≥RR_min |
| E3 | Equal H/L on 1H, `ε_eq=0.1*ATR` initial research hyperparam |
| C1 | EURUSD+GBPUSD keep both + `CORRELATED_PAIR` warning |
| FVG mitigation default | `close_through` (touch / 50% fill = separate runs) |
| POI selection default | `best_rr_then_recency` (`most_recent_valid` = separate run) |
| CHoCH reversal | OFF alerts V1 |
| Alerts | A+ and A only; B log-only |
| D1/D2 | Providers open — interfaces only until quality comparison |

## Anti-overfitting / OOS isolation (P0)

1. Chronological TRAIN / VAL / OOS
2. `run_calibration()` uses TRAIN+VAL only and writes locked config + hash
3. `run_oos_eval()` is a separate step after lock — never used to retune
4. Trade outcomes are bounded to the active split end (`max_index`) — no TRAIN→VAL/OOS leakage
5. Variant experiments = separate runs
6. Primary selection: expectancy_R under PF/DD/min_signals constraints — not win rate alone

## ÉTAPE 4 scope

Backtest engine + strategy evaluate() + metrics + splits + walk-forward + regimes + synthetic/golden fixtures.
Paper/Telegram/live providers = later steps. No real market data in ÉTAPE 4.
