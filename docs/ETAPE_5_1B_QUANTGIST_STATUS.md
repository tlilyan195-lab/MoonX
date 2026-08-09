# ÉTAPE 5.1B — QuantGist status (post-validation)

## Decision (user)

1. Do **not** pay QuantGist.
2. Do **not** regenerate other keys for now.
3. Classify as **BLOCKED_AUTH** / provider non validé — **not** permanently unusable.
4. Keep all results and diagnostics.
5. Do not modify any strategy rules.
6. No OOS access.
7. No real orders.

## Evidence retained

- `docs/ETAPE_5_1B_QUANTGIST_FREE_PILOT_SAMPLE.json` — all calendar/events → HTTP 401
- `docs/ETAPE_5_1B_QUANTGIST_FREE_PILOT_ANALYSIS.md`
- `docs/ETAPE_5_1B_QUANTGIST_FREE_PILOT_GATE.md` (if present)
- Scripts: `scripts/quantgist_free_pilot_gate.py`, `scripts/analyze_quantgist_free_pilot.py`

## Live outcome summary

- `calendar_range` = 401
- `history_probe_2024` = 401
- `events_backtest_safe` = 401
- `n_events_total` = 0
- Error class: `authentication_failed` / API key not found or has been revoked

## Next

- Continue ÉTAPE 5.1 with **FMP Free** as the next read-only pilot.
- QuantGist may be re-evaluated later separately.
