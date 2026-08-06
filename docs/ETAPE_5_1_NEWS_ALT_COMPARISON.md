# ÉTAPE 5.1 — NEWS GATE provider alternatives

- Generated (UTC): `2026-08-06T09:48:58Z`
- Goal: free / cheapest provider for FX/XAU news blackout calendar (backtest-safe).
- Constraints: no QuantGist payment, no key regen now, no strategy changes, no OOS, no real orders.

## Recommendation (ONE next pilot)

**Provider:** `fmp` — Financial Modeling Prep — Economic Calendar API (Free tier first)

### Why

- QuantGist Free is BLOCKED_AUTH (401) — do not pay / do not regenerate keys now.
- Trading Economics guest is dead (410); paid ~$149/mo too expensive for V1.
- Finnhub Free already REJECT for 3y calendar entitlement (5.1A).
- FMP Basic Free is $0 with documented historical from/to, currency, country, impact, event.
- Max ~90 days per request is workable via paging for pilot + later depth if entitled.
- Official docs + stable endpoint; same read-only pilot pattern as QuantGist.
- If Free returns 402, stop and report — do not auto-pay; Starter ~$22/mo is cheaper than TE.

### Pilot plan

- Script: `scripts/fmp_free_pilot_gate.py`
- Env: `FMP_API_KEY`
- Documentary validation + small read-only pilot (same pattern as QuantGist).
- **Do not** code the definitive connector yet.

## Comparison matrix

| Provider | Status | Cost pilot | History | TS | CCY/Country | Impact | Event id | Look-ahead | Depth |
|---|---|---|---|---|---|---|---|---|---|
| `quantgist` | `BLOCKED_AUTH` | $0 Free (auth currently broken) | Starter $19/mo official | YES (doc) — /v1/calendar/range + /v1/events | YES — time_utc + date + time_et | YES — currency + country | YES — impact High/Medium/Low + importance 1-3 | YES — event + event_id | PARTIAL — no PIT vintage; use scheduled time only | DOC_CLAIM 10y Free / 20y Starter — NOT empirically confirmed |
| `trading_economics` | `REJECT_COST_V1` | $0 guest → HTTP 410 Gone | YES if paid | YES DateTime | YES Currency + Country | YES Importance 1-3 | YES Event + CalendarId | PARTIAL — no PIT vintage | Likely OK if paid — not piloted paid |
| `finnhub` | `REJECT_FREE_ENTITLEMENT` | $0 Free | NO for 3y — Free not entitled (ÉTAPE 5.1A) | YES when entitled | country primarily | YES impact | event name; id weak | PARTIAL | FAIL Free entitlement |
| `fmp` | `RECOMMENDED_NEXT_PILOT` | $0 Basic Free (250 req/day) — needs FMP_API_KEY | YES — from/to; max ~90 days per request (page for 3y) | YES date (UTC) | YES country + currency | YES High/Medium/Low | YES event name; no stable event_id (hash candidate) | PARTIAL — no PIT vintage; use date as scheduled time | UNKNOWN until Free pilot — pricing table shows 5y hist on Basic/Starter |
| `twelve_data` | `DEFER` | $0 free credits; paid Grow+ | UNCLEAR / limited on free | varies | partial | unclear for FX gate | unclear | UNKNOWN | UNLIKELY free for 3y calendar |
| `alpha_vantage` | `REJECT_SHAPE` | $0 Free (rate limited) | NO multi-currency impact calendar matching our gate | series timestamps for indicators | indicator-specific, not calendar rows | NO calendar impact field | indicator names only | N/A for gate shape | N/A wrong product shape |
| `econpulse` | `DEFER` | Unknown / contact | CLAIMS historical | CLAIMS | CLAIMS | CLAIMS | CLAIMS | UNKNOWN | UNKNOWN |
| `ff_scrape` | `REJECT_POLICY` | $0 | possible via scrape — FORBIDDEN here | yes if scraped | yes | yes | yes | risky + ToS/legal | N/A |

## Notes per provider

### `quantgist` — Economic Calendar API (Free / Starter)

- Fit: HIGH if auth works
- API stability: GOOD docs; live Free = BLOCKED_AUTH (401 revoked)
- Notes: Do not pay now. Do not regenerate keys now. Re-evaluate later separately.

### `trading_economics` — Calendar API (guest + Standard)

- Fit: HIGH if paid
- API stability: GOOD docs; guest dead
- Notes: Too expensive for V1 pilot. Keep as optional later.

### `finnhub` — Economic Calendar (Free)

- Fit: LOW on Free
- API stability: GOOD
- Notes: Already rejected in 5.1A for historical depth on Free.

### `fmp` — Economic Calendar API (Free tier / Starter)

- Fit: HIGH candidate for Free pilot
- API stability: GOOD — docs /stable/economics-calendar; endpoint /stable/economic-calendar
- Notes: Best free/cheap fit after QG auth block. Pilot read-only first. Risk: Free may return 402 for this endpoint — confirm before any payment.

### `twelve_data` — Economics / calendar endpoints

- Fit: LOW for V1 news gate
- API stability: GOOD general market API
- Notes: Better as OHLC alternative than news calendar primary.

### `alpha_vantage` — ECONOMIC indicators / NEWS_SENTIMENT

- Fit: LOW
- API stability: GOOD
- Notes: Wrong product for event blackout calendar.

### `econpulse` — Economic Calendar API

- Fit: MEDIUM unknown
- API stability: Smaller vendor; docs less audited here
- Notes: Not chosen for pilot — prefer FMP (known Free + OpenAPI).

### `ff_scrape` — Forex Factory HTML scrape

- Fit: N/A
- API stability: brittle HTML
- Notes: Hard rule: no scraping.

## Unauthenticated probes (evidence)

- `fmp_stable_no_key`: http=401 ok=False n_items=None snippet=`{
  "Error Message": "Invalid API KEY. Feel free to create a Free API Key or visit https://site.financialmodelingprep.co`
- `fmp_stable_demo_key`: http=401 ok=False n_items=None snippet=`{
  "Error Message": "Invalid API KEY. Feel free to create a Free API Key or visit https://site.financialmodelingprep.co`
- `te_guest`: http=410 ok=False n_items=None snippet=`<p>We are sorry, but the guest account has been discontinued.</p>
<p>Please subscribe to a plan at <a href="https://trad`

Unauth probes only. Live Free FMP pilot requires FMP_API_KEY in .env (scripts/fmp_free_pilot_gate.py). QuantGist remains BLOCKED_AUTH.


## News map (unchanged)

```json
{
  "EURUSD": [
    "EUR",
    "USD"
  ],
  "GBPUSD": [
    "GBP",
    "USD"
  ],
  "USDJPY": [
    "USD",
    "JPY"
  ],
  "XAUUSD": [
    "USD_high_impact"
  ],
  "BTCUSDT": [],
  "ETHUSDT": []
}
```

## QuantGist

- Status: **BLOCKED_AUTH** / provider non validé (not permanently unusable).
- Diagnostics preserved: `docs/ETAPE_5_1B_QUANTGIST_*`.
- Re-evaluate later separately; do not pay; do not regenerate keys for now.
