# ÉTAPE 5.1B — JBlanked Free Calendar pilot gate

- Generated (UTC): `2026-08-09T21:28:54Z`
- Key present: `False`
- Key fingerprint: `None`
- Free limit: **1 request/day** (docs) — this gate uses one range call by default.

## Verdict

- **JBLANKED_FREE_PILOT**: `FAIL_NO_KEY`
- **3Y_HISTORY**: `NOT_TESTED`
- **LOOKAHEAD_SAFETY**: `DOC_PARTIAL`
- **PAYMENT_RECOMMENDATION**: `NO`
- **NEXT**: `Add JBLANKED_API_KEY to .env then re-run gate (1 free req/day)`

## Windows

- No live probes (missing `JBLANKED_API_KEY`).

## Notes

- Read-only pilot only — not the definitive NEWS connector.
- Prefer MQL5 source path (documented official calendar feed via JBlanked).
- No PIT vintage → blackout uses scheduled `Date` only.
- FMP = `BLOCKED_ENTITLEMENT` (402). QuantGist = `BLOCKED_AUTH`.
- Do not buy JBlanked credits until Free sample proves field fit.
