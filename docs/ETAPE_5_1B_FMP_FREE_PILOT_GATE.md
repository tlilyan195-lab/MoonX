# ÉTAPE 5.1B — FMP Free Economic Calendar pilot gate

- Generated (UTC): `2026-08-06T09:59:27Z`
- Key present: `True`
- Key fingerprint: `m4jo…9U4L (len=32)`

## Verdict

- **FMP_FREE_PILOT**: `FAIL_ENTITLEMENT_402`
- **3Y_HISTORY**: `NOT_CONFIRMED`
- **LOOKAHEAD_SAFETY**: `UNKNOWN`
- **PAYMENT_RECOMMENDATION**: `NO_AUTO`
- **NEXT**: `Free not entitled — report before any Starter (~$22) decision`

## Windows

- `pilot_2025q4_overlap` `2025-10-01`→`2025-12-29`: http=402 ok=False n_events=0
  - error: `Restricted Endpoint: This endpoint is not available under your current subscription please visit our subscription page to upgrade your plan at https://financialmodelingprep.com/`
- `history_probe_2024` `2024-01-01`→`2024-03-30`: http=402 ok=False n_events=0
  - error: `Restricted Endpoint: This endpoint is not available under your current subscription please visit our subscription page to upgrade your plan at https://financialmodelingprep.com/`
- `history_probe_2023` `2023-01-01`→`2023-03-30`: http=402 ok=False n_events=0
  - error: `Restricted Endpoint: This endpoint is not available under your current subscription please visit our subscription page to upgrade your plan at https://financialmodelingprep.com/`

## Notes

- Read-only pilot only — not the definitive NEWS connector.
- FMP docs: max **~90 days** per `from`/`to` request; page for longer spans.
- Free Basic entitlement for this endpoint is **unconfirmed** until live key (community reports of HTTP 402 on Free are possible — do not pay until proved).
- No PIT vintage → blackout must use scheduled `date` only (no actual-release timing).
- QuantGist remains `BLOCKED_AUTH` (not permanently dead; do not pay / no new keys now).
- Prior QuantGist diagnostics preserved under `docs/ETAPE_5_1B_QUANTGIST_*`.
