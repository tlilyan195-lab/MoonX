# ÉTAPE 5.1B — FMP Free Economic Calendar pilot gate

- Generated (UTC): `2026-08-06T09:48:58Z`
- Key present: `False`
- Key fingerprint: `None`

## Verdict

- **FMP_FREE_PILOT**: `FAIL_NO_KEY`
- **3Y_HISTORY**: `NOT_TESTED`
- **LOOKAHEAD_SAFETY**: `DOC_PARTIAL`
- **PAYMENT_RECOMMENDATION**: `NO`
- **NEXT**: `Add FMP_API_KEY to .env then re-run gate`

## Windows

- No live probes (missing `FMP_API_KEY`).

## Notes

- Read-only pilot only — not the definitive NEWS connector.
- FMP docs: max **~90 days** per `from`/`to` request; page for longer spans.
- Free Basic entitlement for this endpoint is **unconfirmed** until live key (community reports of HTTP 402 on Free are possible — do not pay until proved).
- No PIT vintage → blackout must use scheduled `date` only (no actual-release timing).
- QuantGist remains `BLOCKED_AUTH` (not permanently dead; do not pay / no new keys now).
- Prior QuantGist diagnostics preserved under `docs/ETAPE_5_1B_QUANTGIST_*`.
