# ÉTAPE 5.1B — FMP Free pilot analysis

- Sample: `docs/ETAPE_5_1B_FMP_FREE_PILOT_SAMPLE.json` (2026-08-06T09:59:27Z)
- Key present: `True` fingerprint `m4jo…9U4L (len=32)`

## Verdict (confirmed)

- FMP_FREE_PILOT = **FAIL_ENTITLEMENT_402**
- PROVIDER_STATUS = **BLOCKED_ENTITLEMENT** / Free non validé (not permanently dead if paid later)
- 3Y_HISTORY = **NOT_CONFIRMED**
- LOOKAHEAD_SAFETY = **UNKNOWN**
- PAYMENT_RECOMMENDATION = **NO_AUTO**
- n_events_total = **0**

## Evidence
- `pilot_2025q4_overlap` `2025-10-01`→`2025-12-29`: http=402 payment_required=True n_events=0
- `history_probe_2024` `2024-01-01`→`2024-03-30`: http=402 payment_required=True n_events=0
- `history_probe_2023` `2023-01-01`→`2023-03-30`: http=402 payment_required=True n_events=0

Error (all windows): `Restricted Endpoint: This endpoint is not available under your current subscription…`

## Interpretation

- Auth succeeded enough to hit entitlement (402 ≠ revoked key).
- Free Basic does **not** entitle Economic Calendar — matches prior risk note.
- No schema/fields/history/look-ahead can be validated from this sample.
- **Do not** auto-upgrade to FMP Starter (~$22/mo).
- QuantGist remains `BLOCKED_AUTH`. FMP becomes `BLOCKED_ENTITLEMENT`.
- Next no-pay pilot: **JBlanked** Calendar API (MQL5 source).
