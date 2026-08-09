# ÉTAPE 5.1 — NEWS GATE provider alternatives

- Generated (UTC): `2026-08-09T21:28:54Z`
- Goal: free / cheapest provider for FX/XAU news blackout calendar (backtest-safe).
- Constraints: no QuantGist/FMP auto-pay, no QG key regen, no strategy changes, no OOS, no real orders.

## Recommendation (ONE next pilot)

**Provider:** `jblanked` — JBlanked News Calendar API — MQL5 calendar/range (Free first)

### Why

- QuantGist Free is BLOCKED_AUTH (401) — do not pay / do not regenerate keys.
- FMP Free is BLOCKED_ENTITLEMENT (402 Restricted Endpoint) — PAYMENT_RECOMMENDATION=NO_AUTO.
- Trading Economics guest dead (410); paid ~$149/mo rejected for V1.
- Finnhub Free already REJECT for 3y calendar (5.1A).
- EODHD economic-events: demo 403 + no impact field → reject for Free gate fit.
- JBlanked Free documents currency + impact + Date + range range + event id — FX-shaped.
- No payment required for pilot (1 free req/day); credits only if Free proves fit later.

### Pilot plan

- Script: `scripts/jblanked_free_pilot_gate.py`
- Env: `JBLANKED_API_KEY`
- Documentary validation + small read-only pilot (same pattern as QuantGist).
- **Do not** code the definitive connector yet.

## Comparison matrix

| Provider | Status | Cost pilot | History | TS | CCY/Country | Impact | Event id | Look-ahead | Depth |
|---|---|---|---|---|---|---|---|---|---|
| `quantgist` | `BLOCKED_AUTH` | $0 Free (auth currently broken) | Starter $19/mo official | YES (doc) — /v1/calendar/range + /v1/events | YES — time_utc + date + time_et | YES — currency + country | YES — impact High/Medium/Low + importance 1-3 | YES — event + event_id | PARTIAL — no PIT vintage; use scheduled time only | DOC_CLAIM 10y Free / 20y Starter — NOT empirically confirmed |
| `trading_economics` | `REJECT_COST_V1` | $0 guest → HTTP 410 Gone | YES if paid | YES DateTime | YES Currency + Country | YES Importance 1-3 | YES Event + CalendarId | PARTIAL — no PIT vintage | Likely OK if paid — not piloted paid |
| `finnhub` | `REJECT_FREE_ENTITLEMENT` | $0 Free | NO for 3y — Free not entitled (ÉTAPE 5.1A) | YES when entitled | country primarily | YES impact | event name; id weak | PARTIAL | FAIL Free entitlement |
| `fmp` | `BLOCKED_ENTITLEMENT` | $0 Basic Free — live key → HTTP 402 Restricted Endpoint | DOC YES — Free not entitled (pilot) | DOC YES — not received on Free | DOC YES — not received on Free | DOC YES — not received on Free | DOC YES — not received on Free | UNKNOWN on Free sample (0 events) | NOT_CONFIRMED — Free blocked |
| `jblanked` | `RECOMMENDED_NEXT_PILOT` | $0 Free (docs: 1 request/day) — needs JBLANKED_API_KEY | YES — /mql5/calendar/range/?from=&to= | YES — Date (e.g. 2024.02.08 15:30:00) + offset guidance | YES — Currency (USD/EUR/GBP/JPY…); country via category/source | YES — High/Medium/Low/None filter + field | YES — Name + eventID (library/docs) | PARTIAL — scheduled Date only; no PIT vintage | UNKNOWN until Free pilot — range supports history; 1 req/day slows 3y |
| `eodhd` | `REJECT_FREE_SHAPE_ENTITLEMENT` | $0 Free register — demo economic-events → 403; likely not Free-entitled | DOC from 2020 — paid feed likely | YES date YYYY-MM-DD HH:MM:SS | country only (ISO2) — no currency field | NO impact field in documented schema | type name; no stable event id | PARTIAL if entitled | from 2020 if entitled — moot without Free access + impact |
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

- Fit: HIGH if paid; FAIL on Free
- API stability: GOOD docs; Free = BLOCKED_ENTITLEMENT (402 all windows)
- Notes: Confirmed FAIL_ENTITLEMENT_402. PAYMENT_RECOMMENDATION=NO_AUTO. Keep diagnostics; re-evaluate paid path only if explicitly approved.

### `jblanked` — News Calendar API (MQL5 / FF / FxStreet)

- Fit: HIGH for Free field shape
- API stability: GOOD FX-focused docs; smaller vendor than FMP/TE
- Notes: Best remaining no-pay fit after QG BLOCKED_AUTH + FMP BLOCKED_ENTITLEMENT. Prefer MQL5 source path. Pilot = one range call.

### `eodhd` — Economic Events Data API

- Fit: LOW (no impact; Free entitlement doubtful)
- API stability: GOOD docs
- Notes: Demo 403 Forbidden. Missing impact for NEWS GATE. Not next pilot.

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
- `te_guest`: http=410 ok=False n_items=None snippet=`<p>We are sorry, but the guest account has been discontinued.</p>
<p>Please subscribe to a plan at <a href="https://trad`
- `eodhd_demo`: http=403 ok=False n_items=None snippet=`Forbidden. Please contact support@eodhistoricaldata.com`
- `jblanked_no_key`: http=401 ok=False n_items=None snippet=`{"message":"Either no API key was provided or the API key does not match any in our database."}`

Unauth probes only. FMP Free live = BLOCKED_ENTITLEMENT (402). Next Free pilot: JBLANKED_API_KEY + scripts/jblanked_free_pilot_gate.py. QuantGist remains BLOCKED_AUTH.


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

## Blocked / rejected (retained)

- QuantGist: **BLOCKED_AUTH** — `docs/ETAPE_5_1B_QUANTGIST_*`
- FMP Free: **BLOCKED_ENTITLEMENT** (402) — `docs/ETAPE_5_1B_FMP_*` — PAYMENT=NO_AUTO
- EODHD: **REJECT_FREE_SHAPE_ENTITLEMENT** (demo 403, no impact field)
