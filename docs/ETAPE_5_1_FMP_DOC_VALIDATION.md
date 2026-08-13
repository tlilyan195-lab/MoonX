# ÉTAPE 5.1 — FMP Economic Calendar (documentary validation)

Read-only documentary check before / alongside the Free pilot gate.
**No definitive connector. No strategy changes. No OOS. No real orders.**

## Product

| Item | Value |
|---|---|
| Provider | Financial Modeling Prep (FMP) |
| Endpoint | `https://financialmodelingprep.com/stable/economic-calendar` |
| Docs | https://site.financialmodelingprep.com/developer/docs/stable/economics-calendar |
| Pricing | https://site.financialmodelingprep.com/developer/docs/pricing |
| Auth | `apikey` query param |

## Cost (site, Personal Use, as of doc pull)

| Plan | Price | Notes |
|---|---|---|
| Basic (Free) | **$0** | 250 calls/day — **pilot target** |
| Starter | **~$22/mo** (billed annually) | Only if Free entitlement/history fails |
| Premium | ~$59/mo | Not needed for V1 gate if Free/Starter OK |
| Trading Economics Standard | ~$149/mo | Rejected for V1 cost |
| QuantGist Starter | ~$19/mo | Blocked — do not pay while auth broken |

## Field fit for NEWS GATE FX/XAU

| Need | FMP (docs / prior samples) | Gate use |
|---|---|---|
| Historical calendar | `from` / `to` (YYYY-MM-DD) | Page ≤90d windows |
| Timestamps | `date` (UTC) | Scheduled blackout start |
| Currency | `currency` | Map pair → currencies |
| Country | `country` | Secondary filter |
| Impact | High / Medium / Low | High-impact filter |
| Event name | `event` | Logging / fingerprint |
| Stable event id | Not guaranteed | SHA fingerprint candidate |
| PIT / first_print | Not documented | Use scheduled time only → PARTIAL look-ahead |

## News map (unchanged)

- EURUSD → EUR + USD
- GBPUSD → GBP + USD
- USDJPY → USD + JPY
- XAUUSD → USD high-impact
- BTCUSDT / ETHUSDT → no macro FX news gate V1

## Look-ahead stance

- **Avoid look-ahead** by treating `date` as the **scheduled** release time for blackout windows.
- Do **not** use revised `actual` prints as if known before bar time.
- Without PIT vintage, we cannot claim full first-print safety — same PARTIAL stance as TE/QG docs.

## Unauth probe (this run)

- No key → HTTP **401**
- `apikey=demo` → HTTP **401**
- Confirms auth wall; live Free key required for schema/history proof.

## Risks before treating Free as validated

1. Free Basic may **not** entitle `/stable/economic-calendar` (possible **402**) — must verify with live key.
2. Max ~90 days per request → 3y needs paging (~12–15 calls/year × currencies filters client-side).
3. Weak/no stable `event_id`.
4. Timestamp precision may be date-level or datetime-in-`date` string — verify on sample.

## Pilot procedure (user)

1. Sign up Free: https://site.financialmodelingprep.com/register
2. Copy API key into local `.env` as `FMP_API_KEY=...` (never paste in chat).
3. Push only a **redacted** sample if needed, or run gate in cloud after placing key in cloud `.env`.
4. Run: `python3 scripts/fmp_free_pilot_gate.py`
5. Signal: « sample FMP poussé » when sample JSON is in repo / available for analysis.
6. **Do not** build definitive connector until PASS_SAMPLE + schema OK.

## Related

- Comparison: `docs/ETAPE_5_1_NEWS_ALT_COMPARISON.md`
- Gate output: `docs/ETAPE_5_1B_FMP_FREE_PILOT_GATE.md`
- QuantGist: `BLOCKED_AUTH` — `docs/ETAPE_5_1B_QUANTGIST_STATUS.md`
