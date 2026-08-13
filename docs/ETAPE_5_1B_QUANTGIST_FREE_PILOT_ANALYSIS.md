# ÉTAPE 5.1B′ — QuantGist FREE pilot analysis

**Free ≠ 3y confirmation.**

- QUANTGIST_FREE_PILOT = **FAIL**
- Provider status (decision) = **BLOCKED_AUTH** / non validé (not permanently unusable)
- 3Y_HISTORY = **DOC_ONLY**
- LOOKAHEAD_SAFETY = **FAIL**
- STARTER_PRICE (doc) = $19/month (https://quantgist.com/pricing.md); JSON-LD conflict $29 on /terms
- PAYMENT_RECOMMENDATION = **NO** (do not pay; do not regenerate keys for now)

## Sections (HTTP / counts)

- `calendar_range`: status=401 n_events=0 fields=[]
- `history_probe_2024`: status=401 n_events=0 fields=[]
- `events_backtest_safe`: status=401 n_events=0 fields=[]

Error class: `authentication_failed` / API key not found or has been revoked.

## Schema field presence

| Field | Status |
|---|---|
| `id` | ABSENT |
| `event_id` | ABSENT |
| `canonical_id` | ABSENT |
| `release_time` | ABSENT |
| `date` | ABSENT |
| `timestamp` | ABSENT |
| `time` | ABSENT |
| `timezone` | ABSENT |
| `tz` | ABSENT |
| `currency` | ABSENT |
| `country` | ABSENT |
| `event` | ABSENT |
| `title` | ABSENT |
| `name` | ABSENT |
| `impact` | ABSENT |
| `importance` | ABSENT |
| `impact_score` | ABSENT |
| `first_print` | ABSENT |
| `revision_seq` | ABSENT |
| `actual` | ABSENT |
| `forecast` | ABSENT |
| `previous` | ABSENT |

## High-impact coverage (sample)

```json
{
  "EUR": 0,
  "GBP": 0,
  "JPY": 0,
  "USD": 0
}
```

Timestamps: oldest=`None` newest=`None` ([])

## History

```json
{
  "probe_http_status": 401,
  "probe_n_events": 0,
  "oldest_timestamp_in_sample": null,
  "newest_timestamp_in_sample": null,
  "three_year_confirmed_by_api": false,
  "note": "Free pilot sample must NOT be treated as 3y confirmation. Auth blocked — re-evaluate later separately."
}
```

## Look-ahead

```json
{
  "backtest_safe_section_present": true,
  "backtest_safe_http": 401,
  "backtest_safe_n_events": 0,
  "first_print_field_seen": false,
  "first_print_values_sample": [],
  "revision_seq_field_seen": false,
  "revision_seq_values_sample": [],
  "verdict": "FAIL",
  "note": "backtest_safe not available on this plan/key."
}
```

## Payment gate

```json
{
  "history_ge_3y_api_confirmed": false,
  "eur_usd_gbp_jpy_high_impact": false,
  "timestamp_reliable": false,
  "impact_exploitable": false,
  "lookahead_ok": false,
  "cost_le_25": true,
  "PAYMENT_RECOMMENDATION": "NO",
  "why": "BLOCKED_AUTH. Do not pay QuantGist. Do not regenerate keys for now. Continue ÉTAPE 5.1 with FMP Free pilot. QuantGist may be re-evaluated later separately."
}
```

See also: `docs/ETAPE_5_1B_QUANTGIST_STATUS.md`, `docs/ETAPE_5_1_NEWS_ALT_COMPARISON.md`.
