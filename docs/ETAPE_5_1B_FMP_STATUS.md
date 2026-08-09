# ÉTAPE 5.1B — FMP status (post Free pilot)

## Decision

1. Confirm repo sample: **FAIL_ENTITLEMENT_402** on all windows.
2. **PAYMENT_RECOMMENDATION = NO_AUTO** — do not pay FMP Starter now.
3. Classify as **BLOCKED_ENTITLEMENT** / Free non validé (paid path may work later).
4. Keep all diagnostics (`ETAPE_5_1B_FMP_*`).
5. No strategy rule changes. No OOS. No real orders.

## Evidence retained

- `docs/ETAPE_5_1B_FMP_FREE_PILOT_SAMPLE.json`
- `docs/ETAPE_5_1B_FMP_FREE_PILOT_GATE.md`
- `docs/ETAPE_5_1B_FMP_FREE_PILOT_ANALYSIS.md`

## Next

- Continue ÉTAPE 5.1 with **JBlanked** free Calendar API read-only pilot.
- FMP may be re-evaluated later only if a paid decision is explicitly approved.
