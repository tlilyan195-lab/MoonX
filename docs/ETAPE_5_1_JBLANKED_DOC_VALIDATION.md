# ÉTAPE 5.1 — JBlanked Calendar (documentary validation)

Read-only documentary check before / alongside the Free pilot gate.
**No definitive connector. No strategy changes. No OOS. No real orders. No payment.**

## Why this provider now

| Prior | Status |
|---|---|
| QuantGist Free | `BLOCKED_AUTH` (401) |
| FMP Free | `BLOCKED_ENTITLEMENT` (402 Restricted Endpoint) — confirmed in repo sample |
| Trading Economics | `REJECT_COST_V1` |
| Finnhub Free | `REJECT_FREE_ENTITLEMENT` |
| EODHD economic-events | demo **403**; schema **sans impact** → reject for Free gate |
| Scrape FF | `REJECT_POLICY` |

**Next no-pay pilot:** JBlanked News Calendar API (prefer **MQL5** source).

## Product

| Item | Value |
|---|---|
| Provider | JBlanked |
| Endpoint (pilot) | `GET https://www.jblanked.com/news/api/mql5/calendar/range/?from=&to=` |
| Docs | https://www.jblanked.com/news/api/docs/calendar/ |
| Auth | Header `Authorization: Api-Key <key>` |
| Free limit | Docs (2026-02): **1 request/day** |

## Field fit for NEWS GATE FX/XAU

| Need | JBlanked (docs) | Gate use |
|---|---|---|
| Historical calendar | `from` / `to` range | One week pilot first |
| Timestamps | `Date` (`YYYY.MM.DD HH:MM:SS`) | Scheduled blackout |
| Currency | `Currency` + filter | Pair → currencies |
| Impact | `Impact` High/Medium/Low + filter | High-impact only |
| Event name/id | `Name` + `eventID` (library) | Logging / fingerprint |
| PIT / first_print | Not documented | Scheduled time only → PARTIAL |

## Cost

- Pilot: **$0** (Free key; 1 req/day).
- Credits/membership: optional — **do not buy** until Free sample proves schema + coverage.
- Explicitly avoids FMP Starter / TE / QuantGist payment.

## Risks

1. Free = 1 req/day → 3y history pull is slow without credits (acceptable for pilot).
2. Smaller vendor than FMP/TE — API stability must be proven by live sample.
3. Date timezone via `offset` in libraries — verify on sample.
4. Aggregates MQL5/FF/FxStreet — we call their API (not scraping HTML ourselves).

## Unauth probe

- No key → HTTP **401** (`Either no API key was provided…`).

## Pilot procedure

1. Create free account / API key at JBlanked profile.
2. Put `JBLANKED_API_KEY=...` in `.env` only (never paste in chat).
3. Run `python3 scripts/jblanked_free_pilot_gate.py` (one range call).
4. Signal « sample JBlanked poussé » when sample is in repo.

## Related

- FMP analysis: `docs/ETAPE_5_1B_FMP_FREE_PILOT_ANALYSIS.md`
- Comparison: `docs/ETAPE_5_1_NEWS_ALT_COMPARISON.md`
- Gate: `scripts/jblanked_free_pilot_gate.py`
