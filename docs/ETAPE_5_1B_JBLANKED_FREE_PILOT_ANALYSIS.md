# ÉTAPE 5.1B — JBlanked Free pilot analysis

- Sample: `docs/ETAPE_5_1B_JBLANKED_FREE_PILOT_SAMPLE.json` (2026-08-09T21:39:25Z)
- Window: `pilot_week_2025q4` `2025-10-01`→`2025-10-07` http=200 n_events=39
- Key fingerprint: `FUrV…UppJ (len=32)`

## Verdict

- JBLANKED_FREE_PILOT = **PASS_SAMPLE**
- SCHEMA_OK_FOR_NEWS_GATE = **True**
- FX_GATE_COMPAT = **YES_WITH_CAVEATS**
- LOOKAHEAD_SAFETY = **PARTIAL_SCHEDULED_TIME_ONLY**
- 3Y_HISTORY = **DOC_ONLY_NEED_MULTI_DAY_OR_CREDITS**
- TRAIN_VAL_FREE_FEASIBLE = **YES_SLOW_MULTI_DAY**
- CREDITS_REQUIRED_NOW = **False**
- PAYMENT_RECOMMENDATION = **NO**

Next: Optional: multi-day Free harvest OR single larger-range history probe (still Free, 1/day). Do not buy credits yet. No connector freeze / no OOS.

## 1) Event structure

- Field union: `['Actual', 'Category', 'Currency', 'Date', 'Event_ID', 'Forecast', 'Impact', 'Name', 'Outcome', 'Previous', 'Quality', 'Strength']`
- Date format: `YYYY.MM.DD HH:MM:SS` (parse ok on sample rows: 8/8)
- Country field in API union: `False` (null in sample rows: 8)
- Event_ID zero/null in sample rows: `1`

| Gate need | Present |
|---|---|
| `Date` | `True` |
| `Currency` | `True` |
| `Impact` | `True` |
| `Name` | `True` |
| `Event_ID` | `True` |

- Safe for blackout scheduling: `['Category', 'Currency', 'Date', 'Event_ID', 'Impact', 'Name']`
- Look-ahead risky if misused: `['Actual', 'Outcome', 'Quality', 'Strength']`
- Pre-release optional (not for timing): `['Forecast', 'Previous']`

## 2) FX news gate compatibility

- impact_fields = **True**
- currency_fields = **True**
- Week currencies: `{'EUR': 12, 'USD': 10, 'JPY': 7, 'CHF': 4, 'AUD': 4, 'CAD': 2}`
- Week impacts: `{'Low': 20, 'High': 11, 'Medium': 7, 'None': 1}`
- target_currency_hits = `29`
- target_high_impact = `8`
- GBP in this sample week = `False`
- Country usable = `False` — Country absent/null — gate must key off Currency (OK for our news map).

### Pair map (unchanged) vs this week

- **EURUSD**: needed `['EUR', 'USD']` present `['EUR', 'USD']` missing `[]` week_ok=`True`
- **GBPUSD**: needed `['GBP', 'USD']` present `['USD']` missing `['GBP']` week_ok=`False`
- **USDJPY**: needed `['USD', 'JPY']` present `['USD', 'JPY']` missing `[]` week_ok=`True`
- **XAUUSD**: needed `['USD']` present `['USD']` missing `[]` week_ok=`True`

Caveats:

- 1-week sample only — not 3y proof.
- GBP missing in this week (structural unknown).
- Event_ID can be 0 for some rows — keep Name+Date+Currency fingerprint fallback.
- No Country — fine if Currency reliable.

## 3) Anti look-ahead strategy

- Verdict: **PARTIAL_SCHEDULED_TIME_ONLY**
- Rule: Blackout may use only scheduled Date + Currency + Impact(+ Name/Event_ID). Do NOT use Actual/Outcome/Strength/Quality to decide blackout timing or membership.
- Timezone: Date has no explicit TZ in payload; JBlanked libs document offset. Must pin TZ convention before TRAIN/VAL freeze (assume UTC or documented offset).
- PIT vintage: `False` / first_print: `False` / revision_seq: `False`

Operational blackout inputs only:

1. `Date` (scheduled release time, TZ pinned once)
2. `Currency` ∈ pair map
3. `Impact == High` (XAUUSD: USD High only)
4. Optional identity: `Name` / `Event_ID` (fingerprint if Event_ID==0)

Never use for gate membership/timing: `Actual`, `Outcome`, `Strength`, `Quality`.

## 4) TRAIN/VAL feasibility — Free vs credits

- Free limit: **1 request/day (docs)**
- 3y confirmed by API: `False`
- History status: `DOC_ONLY_NEED_MULTI_DAY_OR_CREDITS`

### Free path

- Feasible: **True** (multi_day_harvest)
- Conservative weekly paging: ~156 requests → ~156 calendar days at 1/day
- Optimistic monthly paging (if API allows): ~36 requests
- Note: Free can accumulate a 3y calendar without payment if range paging works and daily quota is used over ~1–6 months depending on page size. Not validated beyond 7-day window yet.

### Credits path

- Needed for schema pilot: `False`
- Needed for fast 3y pull: `True`
- Payment recommendation: **NO_AUTO**
- Note: Buy credits only after explicit approval AND after a larger historical range probe proves depth (e.g. 2023 / 2024 windows).

### Blockers before any TRAIN/VAL freeze

- Confirm max from/to span per request (week vs month vs longer).
- Confirm GBP high-impact appears in other weeks (absent in this sample week).
- Pin timezone interpretation for Date.
- Freeze look-ahead rule: scheduled Date only; ignore post-release fields.
- No OOS access; no strategy rule changes.

## Constraints honored

- No optimization
- No OOS
- No strategy rule changes
- No definitive connector freeze in this step
- No auto payment / credits
