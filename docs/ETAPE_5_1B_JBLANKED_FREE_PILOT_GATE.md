# ÉTAPE 5.1B — JBlanked Free Calendar pilot gate

- Generated (UTC): `2026-08-09T21:39:25Z`
- Key present: `True`
- Key fingerprint: `FUrV…UppJ (len=32)`
- Free limit: **1 request/day** (docs) — this gate uses one range call by default.

## Verdict

- **JBLANKED_FREE_PILOT**: `PASS_SAMPLE`
- **3Y_HISTORY**: `DOC_ONLY_NEED_MULTI_DAY_OR_CREDITS`
- **LOOKAHEAD_SAFETY**: `PARTIAL_SCHEDULED_TIME_ONLY`
- **PAYMENT_RECOMMENDATION**: `NO`
- **NEXT**: `Analyze schema/coverage; Free=1 req/day so 3y paging is slow without credits — impact_fields=True currency_fields=True`

## Windows

- `pilot_week_2025q4` `2025-10-01`→`2025-10-07`: http=200 ok=True n_events=39
  - impacts: `{'Low': 20, 'High': 11, 'Medium': 7, 'None': 1}`
  - currencies: `{'EUR': 12, 'USD': 10, 'JPY': 7, 'CHF': 4, 'AUD': 4, 'CAD': 2}`
  - target_high_impact: `8`

## Notes

- Read-only pilot only — not the definitive NEWS connector.
- Prefer MQL5 source path (documented official calendar feed via JBlanked).
- No PIT vintage → blackout uses scheduled `Date` only.
- FMP = `BLOCKED_ENTITLEMENT` (402). QuantGist = `BLOCKED_AUTH`.
- Do not buy JBlanked credits until Free sample proves field fit.
