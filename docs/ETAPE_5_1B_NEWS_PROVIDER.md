# ÉTAPE 5.1B — NEWS provider validation (Trading Economics)

**STOP.** No 3y OHLC download. No TRAIN/VAL. No OOS. No SMC changes. No paid subscribe by agent.

## 1. TRADING_ECONOMICS verdict: **REJECT**

- usable_free_for_pilot: `False`
- usable_for_3y_without_paid: `False`
- Live guest probe status: `410` (discontinued)

### Reasons

- Live probe: guest:guest returns HTTP 410 — public guest sample discontinued.
- Unauthenticated calendar calls return 401 — key required.
- Trial is limited (100 requests / 100k points) and auto-charges if not cancelled (non-refundable trial fee per TE pricing page) — not a free ongoing pilot.
- Economic Calendar max 1000 rows/request → must page by date chunks for 3y.
- Date field documented as UTC; Importance 1/2/3; CalendarId stable event id.
- Currency field often empty in docs samples — Country→currency mapping required (project map: {'United States': 'USD', 'United Kingdom': 'GBP', 'Japan': 'JPY', 'Euro Area': 'EUR', 'Germany': 'EUR', 'France': 'EUR', 'Italy': 'EUR', 'Spain': 'EUR', 'European Union': 'EUR'}).
- Point-in-time / historical calendar endpoints exist for no-lookahead vintages.
- Third-party plan comps (Jun 2026): Standard ~$149/mo, Professional ~$299/mo (billed yearly); exact matrix behind TE account. Enterprise custom.
- No TE_API_KEY — cannot prove live 3y row coverage in this run.

## 2. Historical depth (TE)

Documented multi-year calendar history (examples from 2016+). Adequate for 3y target IF on a paid/trial plan with calendar entitlement. Not available without authentication.

## 3. Fields (TE)

| Need | TE |
|---|---|
| publication_timestamp | Date (UTC ISO) — YES |
| country | Country — YES |
| currency_or_deducible | Currency (often empty) + Country→ISO map — YES (deducible) |
| event_name | Event / Category — YES |
| importance_impact | Importance 1=low 2=medium 3=high — YES |
| stable_id | CalendarId (+ Symbol/Ticker) — YES |

## 4. Plan / cost (TE)

```json
{
  "free_guest": "Discontinued (HTTP 410)",
  "trial": "Limited 100 requests / 100k points; auto-charge risk; not free ongoing",
  "standard_est_usd_per_month": 149,
  "professional_est_usd_per_month": 299,
  "billing_note": "Estimates from third-party comps; confirm on TE pricing after account",
  "plan_needed_for_3y": "Paid subscription with Economic Calendar (Standard or above)",
  "subscribed_by_agent": false
}
```

## 5. API limits (TE)

```json
{
  "calendar_rows_per_request": 1000,
  "historical_rows_per_request": 10000,
  "requests_per_second": 2,
  "url_max_chars": 260
}
```

- Pagination: No cursor; split by country + initDate/endDate windows so each response stays ≤1000 calendar rows.
- Timezone: UTC (Date documented as release date/time in UTC)
- Event IDs: CalendarId (string) stable within TE; Symbol/Ticker for indicator series
- Immutable snapshot: Feasible: freeze JSON/parquet of calendar rows + sha256 into data_snapshot extras (same pattern as OHLC). TE does not ship a snapshot_id; we hash locally.
- No look-ahead: Use scheduled Date only for blackout windows; for printed values use Point-in-Time / historical calendar as-of trade time. Do not use post-revision Actual unknown pre-trade. CDC blackout rules unchanged in 5.1B.

## 6. Two alternatives (TE rejected for free/pilot)

### quantgist_economic_calendar_api — `ACCEPT_CONDITIONAL`

- Depth: Free: 365 days; Starter: 1,095 days (3y); Pro/Team: 3,650 days (10y). Archive marketed from 2014 on Pro.
- Plan for 3y: `Starter ($19/mo) minimum; Pro if PIT/v2 bulk preferred`
- Free pilot usable: `True`
- Fields: {'publication_timestamp': 'release_time UTC ISO — YES', 'country': 'ISO 3166-1 alpha-2 — YES', 'currency_or_deducible': 'ISO 4217 currency filter/field — YES (explicit)', 'event_name': 'title — YES', 'importance_impact': 'impact low|medium|high (+ impact_score) — YES', 'stable_id': 'id UUID + canonical_id (e.g. US_CPI_YOY) — YES'}
- Limits: {'free_requests_per_day': 100, 'starter_requests_per_day': 5000, 'pro_requests_per_day': 50000, 'calendar_range_limit': 500, 'events_per_page_max': 100}

### fmp_economic_calendar_api — `ACCEPT_CONDITIONAL`

- Depth: Endpoint accepts from/to date ranges; Starter plan advertises up to 5y historical data generally. Exact calendar-row depth for 3y EUR/GBP/JPY/USD must be verified with a key before freeze.
- Plan for 3y: `Starter ($22/mo) or higher — confirm calendar history entitlement`
- Free pilot usable: `False`
- Fields: {'publication_timestamp': 'date (UTC YYYY-MM-DD HH:MM:SS) — YES', 'country': 'ISO country code — YES', 'currency_or_deducible': 'currency ISO 4217 — YES (explicit)', 'event_name': 'event — YES', 'importance_impact': 'impact High|Medium|Low — YES', 'stable_id': 'No dedicated CalendarId in public schema samples — WEAK'}
- Limits: {'free_calls_per_day': 250, 'starter_calls_per_minute': 300, 'note': 'Free plan limited; economic calendar may require paid tier — verify with account'}

## 7. Recommended NEWS provider for V1

**quantgist_economic_calendar_api** — plan `Starter ($19/mo)`

Cheapest documented plan with explicit 3-year history, native currency+impact, pagination, UTC timestamps, stable ids, and backtest_safe / first_print flags. TE remains the institutional alternative (CalendarId + PIT) if budget allows Standard≈$149/mo after account.

- Runner-up: trading_economics_calendar_api (Standard+) if preferring TE PIT/CalendarId
- Second alt: fmp_economic_calendar_api (Starter≈$22/mo) — verify 3y depth + ids

## 8. Action required from you

- **Now:** No paid key required yet. Optional: create a free QuantGist account (https://quantgist.com/signup) to pilot schema on ≤1y only — do not freeze 3y on Free.
- **Before 3y news snapshot:** Upgrade QuantGist to Starter (or subscribe TE Standard / FMP Starter), place key in .env only (QUANTGIST_API_KEY or TE_API_KEY), then authorize a read-only historical pull for EUR/GBP/JPY/USD high-impact covering the OHLC window.

Do not:

- Do not scrape HTML calendars
- Do not invent missing events
- Do not change CDC blackout rules in this step
- Do not download 3y OHLC before news provider key validation

## News currency map (unchanged CDC blackout rules)

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
    "USD"
  ]
}
```

BTCUSDT/ETHUSDT: no macro FX news gate in V1.

Unexpected OHLC gaps remain explicit (no interpolation).

## 9. Tests

```text
51 passed (full suite)
```

- `tests/test_etape_5_1b_news.py` — TE REJECT + guest 410, QuantGist 3y=Starter, FMP fields, currency map, no CDC/OHLC side effects
- Live probes in `scripts/validate_news_provider_5_1b.py`: TE guest→410, QuantGist `/v1/health`→200

## 10. Machine-readable

`docs/ETAPE_5_1B_NEWS_PROVIDER.json`

