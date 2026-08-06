"""ÉTAPE 5.1 — comparaison providers NEWS GATE (doc + probes unauth).

Aucun scraping. Aucune modification des règles SMC/CDC.
Aucun TRAIN/VAL/OOS. Aucun ordre réel.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

USER_AGENT = "MoonX-NewsAltEval/1.0 (+signals-only; no-trading)"


@dataclass(frozen=True)
class ProviderRow:
    provider_id: str
    product: str
    cost_pilot: str
    cost_if_paid: str
    historical_calendar: str
    timestamps: str
    currency_country: str
    impact: str
    event_name_id: str
    lookahead_avoidance: str
    train_val_depth: str
    api_stability: str
    fit_for_fx_xau_news_gate: str
    status: str
    notes: str


PROVIDER_ROWS: tuple[ProviderRow, ...] = (
    ProviderRow(
        provider_id="quantgist",
        product="Economic Calendar API (Free / Starter)",
        cost_pilot="$0 Free (auth currently broken) | Starter $19/mo official",
        cost_if_paid="$19/mo Starter (JSON-LD conflict $29 noted earlier)",
        historical_calendar="YES (doc) — /v1/calendar/range + /v1/events",
        timestamps="YES — time_utc + date + time_et",
        currency_country="YES — currency + country",
        impact="YES — impact High/Medium/Low + importance 1-3",
        event_name_id="YES — event + event_id",
        lookahead_avoidance="PARTIAL — no PIT vintage; use scheduled time only",
        train_val_depth="DOC_CLAIM 10y Free / 20y Starter — NOT empirically confirmed",
        api_stability="GOOD docs; live Free = BLOCKED_AUTH (401 revoked)",
        fit_for_fx_xau_news_gate="HIGH if auth works",
        status="BLOCKED_AUTH",
        notes="Do not pay now. Do not regenerate keys now. Re-evaluate later separately.",
    ),
    ProviderRow(
        provider_id="trading_economics",
        product="Calendar API (guest + Standard)",
        cost_pilot="$0 guest → HTTP 410 Gone",
        cost_if_paid="~USD 149/mo Standard (site)",
        historical_calendar="YES if paid",
        timestamps="YES DateTime",
        currency_country="YES Currency + Country",
        impact="YES Importance 1-3",
        event_name_id="YES Event + CalendarId",
        lookahead_avoidance="PARTIAL — no PIT vintage",
        train_val_depth="Likely OK if paid — not piloted paid",
        api_stability="GOOD docs; guest dead",
        fit_for_fx_xau_news_gate="HIGH if paid",
        status="REJECT_COST_V1",
        notes="Too expensive for V1 pilot. Keep as optional later.",
    ),
    ProviderRow(
        provider_id="finnhub",
        product="Economic Calendar (Free)",
        cost_pilot="$0 Free",
        cost_if_paid="N/A for this endpoint on Free",
        historical_calendar="NO for 3y — Free not entitled (ÉTAPE 5.1A)",
        timestamps="YES when entitled",
        currency_country="country primarily",
        impact="YES impact",
        event_name_id="event name; id weak",
        lookahead_avoidance="PARTIAL",
        train_val_depth="FAIL Free entitlement",
        api_stability="GOOD",
        fit_for_fx_xau_news_gate="LOW on Free",
        status="REJECT_FREE_ENTITLEMENT",
        notes="Already rejected in 5.1A for historical depth on Free.",
    ),
    ProviderRow(
        provider_id="fmp",
        product="Economic Calendar API (Free tier / Starter)",
        cost_pilot="$0 Basic Free (250 req/day) — needs FMP_API_KEY",
        cost_if_paid="Starter ~$22/mo billed annually if Free insufficient",
        historical_calendar="YES — from/to; max ~90 days per request (page for 3y)",
        timestamps="YES date (UTC)",
        currency_country="YES country + currency",
        impact="YES High/Medium/Low",
        event_name_id="YES event name; no stable event_id (hash candidate)",
        lookahead_avoidance="PARTIAL — no PIT vintage; use date as scheduled time",
        train_val_depth="UNKNOWN until Free pilot — pricing table shows 5y hist on Basic/Starter",
        api_stability="GOOD — docs /stable/economics-calendar; endpoint /stable/economic-calendar",
        fit_for_fx_xau_news_gate="HIGH candidate for Free pilot",
        status="RECOMMENDED_NEXT_PILOT",
        notes=(
            "Best free/cheap fit after QG auth block. Pilot read-only first. "
            "Risk: Free may return 402 for this endpoint — confirm before any payment."
        ),
    ),
    ProviderRow(
        provider_id="twelve_data",
        product="Economics / calendar endpoints",
        cost_pilot="$0 free credits; paid Grow+",
        cost_if_paid="Paid plans for deeper history",
        historical_calendar="UNCLEAR / limited on free",
        timestamps="varies",
        currency_country="partial",
        impact="unclear for FX gate",
        event_name_id="unclear",
        lookahead_avoidance="UNKNOWN",
        train_val_depth="UNLIKELY free for 3y calendar",
        api_stability="GOOD general market API",
        fit_for_fx_xau_news_gate="LOW for V1 news gate",
        status="DEFER",
        notes="Better as OHLC alternative than news calendar primary.",
    ),
    ProviderRow(
        provider_id="alpha_vantage",
        product="ECONOMIC indicators / NEWS_SENTIMENT",
        cost_pilot="$0 Free (rate limited)",
        cost_if_paid="Premium tiers",
        historical_calendar="NO multi-currency impact calendar matching our gate",
        timestamps="series timestamps for indicators",
        currency_country="indicator-specific, not calendar rows",
        impact="NO calendar impact field",
        event_name_id="indicator names only",
        lookahead_avoidance="N/A for gate shape",
        train_val_depth="N/A wrong product shape",
        api_stability="GOOD",
        fit_for_fx_xau_news_gate="LOW",
        status="REJECT_SHAPE",
        notes="Wrong product for event blackout calendar.",
    ),
    ProviderRow(
        provider_id="econpulse",
        product="Economic Calendar API",
        cost_pilot="Unknown / contact",
        cost_if_paid="Unknown",
        historical_calendar="CLAIMS historical",
        timestamps="CLAIMS",
        currency_country="CLAIMS",
        impact="CLAIMS",
        event_name_id="CLAIMS",
        lookahead_avoidance="UNKNOWN",
        train_val_depth="UNKNOWN",
        api_stability="Smaller vendor; docs less audited here",
        fit_for_fx_xau_news_gate="MEDIUM unknown",
        status="DEFER",
        notes="Not chosen for pilot — prefer FMP (known Free + OpenAPI).",
    ),
    ProviderRow(
        provider_id="ff_scrape",
        product="Forex Factory HTML scrape",
        cost_pilot="$0",
        cost_if_paid="$0",
        historical_calendar="possible via scrape — FORBIDDEN here",
        timestamps="yes if scraped",
        currency_country="yes",
        impact="yes",
        event_name_id="yes",
        lookahead_avoidance="risky + ToS/legal",
        train_val_depth="N/A",
        api_stability="brittle HTML",
        fit_for_fx_xau_news_gate="N/A",
        status="REJECT_POLICY",
        notes="Hard rule: no scraping.",
    ),
)


def _http_json(url: str, timeout: float = 25.0) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            try:
                parsed: Any = json.loads(body) if body else None
            except json.JSONDecodeError:
                parsed = body[:400]
            return {
                "ok": True,
                "http_status": int(resp.status),
                "body_type": type(parsed).__name__,
                "n_items": len(parsed) if isinstance(parsed, list) else None,
                "snippet": (body[:240] if isinstance(body, str) else str(parsed)[:240]),
            }
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        return {
            "ok": False,
            "http_status": int(exc.code),
            "error": raw[:400],
            "snippet": raw[:240],
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "http_status": None, "error": f"{type(exc).__name__}: {exc}"}


def run_unauth_probes() -> dict[str, Any]:
    """Probes without secrets — document auth walls only."""
    return {
        "probed_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fmp_stable_no_key": _http_json(
            "https://financialmodelingprep.com/stable/economic-calendar"
            "?from=2024-01-01&to=2024-01-31"
        ),
        "fmp_stable_demo_key": _http_json(
            "https://financialmodelingprep.com/stable/economic-calendar"
            "?from=2024-01-01&to=2024-01-31&apikey=demo"
        ),
        "te_guest": _http_json(
            "https://api.tradingeconomics.com/calendar"
            "?c=guest:guest&d1=2024-01-01&d2=2024-01-31"
        ),
        "note": (
            "Unauth probes only. Live Free FMP pilot requires FMP_API_KEY in .env "
            "(scripts/fmp_free_pilot_gate.py). QuantGist remains BLOCKED_AUTH."
        ),
    }


def recommendation() -> dict[str, Any]:
    return {
        "recommended_provider_for_next_pilot": "fmp",
        "product": "Financial Modeling Prep — Economic Calendar API (Free tier first)",
        "why": [
            "QuantGist Free is BLOCKED_AUTH (401) — do not pay / do not regenerate keys now.",
            "Trading Economics guest is dead (410); paid ~$149/mo too expensive for V1.",
            "Finnhub Free already REJECT for 3y calendar entitlement (5.1A).",
            "FMP Basic Free is $0 with documented historical from/to, currency, country, impact, event.",
            "Max ~90 days per request is workable via paging for pilot + later depth if entitled.",
            "Official docs + stable endpoint; same read-only pilot pattern as QuantGist.",
            "If Free returns 402, stop and report — do not auto-pay; Starter ~$22/mo is cheaper than TE.",
        ],
        "pilot_plan": {
            "type": "documentary_validation + read-only Free pilot",
            "script": "scripts/fmp_free_pilot_gate.py",
            "required_env": "FMP_API_KEY",
            "signup": "https://site.financialmodelingprep.com/register",
            "docs": "https://site.financialmodelingprep.com/developer/docs/stable/economics-calendar",
            "no_definitive_connector_yet": True,
            "no_strategy_rule_changes": True,
            "no_oos": True,
            "no_real_orders": True,
            "quantgist_status": "BLOCKED_AUTH — re-evaluate later separately",
        },
        "fallback_if_fmp_free_fails": [
            "Inspect FMP error (401 auth vs 402 entitlement vs empty).",
            "Only then consider FMP Starter ~$22/mo IF Free history/entitlement insufficient.",
            "Do not default to TE ~$149 or QuantGist payment.",
        ],
    }


def comparison_payload(probe_results: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "etape": "5.1_NEWS_ALT",
        "constraints": {
            "no_quantgist_payment": True,
            "no_key_regeneration_for_now": True,
            "quantgist_status": "BLOCKED_AUTH",
            "no_strategy_rule_changes": True,
            "no_oos": True,
            "no_real_orders": True,
            "preserve_prior_diagnostics": True,
        },
        "providers": [asdict(r) for r in PROVIDER_ROWS],
        "unauthenticated_probes": probe_results or {},
        "recommendation": recommendation(),
        "news_map_unchanged": {
            "EURUSD": ["EUR", "USD"],
            "GBPUSD": ["GBP", "USD"],
            "USDJPY": ["USD", "JPY"],
            "XAUUSD": ["USD_high_impact"],
            "BTCUSDT": [],
            "ETHUSDT": [],
        },
    }
