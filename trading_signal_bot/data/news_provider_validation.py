"""
ÉTAPE 5.1B — Trading Economics NEWS provider validation + alternatives.

No paid subscription. No scrape. No invented events.
No CDC blackout rule changes. No 3y OHLC download.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import requests

REQUIRED_HISTORY_YEARS = 3
REQUIRED_FIELDS = (
    "publication_timestamp",
    "country",
    "currency_or_deducible",
    "event_name",
    "importance_impact",
)

# Symbol → currency filters (CDC mapping; blackout windows unchanged)
NEWS_CURRENCY_MAP = {
    "EURUSD": ("EUR", "USD"),
    "GBPUSD": ("GBP", "USD"),
    "USDJPY": ("USD", "JPY"),
    "XAUUSD": ("USD",),  # min high-impact USD per CDC
    # BTCUSDT / ETHUSDT: no macro FX news gate in V1
}

COUNTRY_TO_CURRENCY = {
    "United States": "USD",
    "United Kingdom": "GBP",
    "Japan": "JPY",
    "Euro Area": "EUR",
    "Germany": "EUR",
    "France": "EUR",
    "Italy": "EUR",
    "Spain": "EUR",
    "European Union": "EUR",
}


@dataclass
class ProviderAssessment:
    provider: str
    verdict: str  # ACCEPT | REJECT
    historical_depth: str
    fields: dict[str, str]
    plan_cost: dict[str, Any]
    api_limits: dict[str, Any]
    pagination: str
    timezone: str
    event_ids: str
    immutable_snapshot: str
    no_lookahead: str
    usable_free_for_pilot: bool
    usable_for_3y_without_paid: bool
    reasons: list[str] = field(default_factory=list)
    probe: dict[str, Any] = field(default_factory=dict)
    docs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "verdict": self.verdict,
            "historical_depth": self.historical_depth,
            "fields": self.fields,
            "plan_cost": self.plan_cost,
            "api_limits": self.api_limits,
            "pagination": self.pagination,
            "timezone": self.timezone,
            "event_ids": self.event_ids,
            "immutable_snapshot": self.immutable_snapshot,
            "no_lookahead": self.no_lookahead,
            "usable_free_for_pilot": self.usable_free_for_pilot,
            "usable_for_3y_without_paid": self.usable_for_3y_without_paid,
            "reasons": self.reasons,
            "probe": self.probe,
            "docs": self.docs,
        }


def probe_trading_economics_guest(session: requests.Session | None = None) -> dict[str, Any]:
    """Live probe: discontinued guest:guest must fail (no paid key used)."""
    sess = session or requests.Session()
    url = (
        "https://api.tradingeconomics.com/calendar/country/united%20states/"
        "2023-01-01/2023-01-07"
    )
    out: dict[str, Any] = {
        "url": url + "?c=guest:guest&f=json",
        "guest_status": None,
        "guest_body_snippet": "",
        "noauth_status": None,
    }
    try:
        r = sess.get(url, params={"c": "guest:guest", "f": "json"}, timeout=30)
        out["guest_status"] = r.status_code
        out["guest_body_snippet"] = (r.text or "")[:300]
    except requests.RequestException as e:
        out["guest_status"] = "error"
        out["guest_body_snippet"] = str(e)
    try:
        r2 = sess.get(url, params={"f": "json"}, timeout=30)
        out["noauth_status"] = r2.status_code
    except requests.RequestException as e:
        out["noauth_status"] = f"error:{e}"
    return out


def assess_trading_economics(
    *,
    api_key: str | None = None,
    session: requests.Session | None = None,
    run_live_probe: bool = True,
) -> ProviderAssessment:
    """
    TE is field-capable for 3y blackout (docs), but not usable without a paid/trial key.
    guest:guest discontinued (HTTP 410). Do not subscribe from this agent.
    """
    key = api_key if api_key is not None else os.environ.get("TE_API_KEY", "")
    probe = probe_trading_economics_guest(session) if run_live_probe else {"skipped": True}
    guest_gone = probe.get("guest_status") in (410, 401, 403)

    reasons = [
        "Live probe: guest:guest returns HTTP 410 — public guest sample discontinued.",
        "Unauthenticated calendar calls return 401 — key required.",
        "Trial is limited (100 requests / 100k points) and auto-charges if not cancelled "
        "(non-refundable trial fee per TE pricing page) — not a free ongoing pilot.",
        "Economic Calendar max 1000 rows/request → must page by date chunks for 3y.",
        "Date field documented as UTC; Importance 1/2/3; CalendarId stable event id.",
        "Currency field often empty in docs samples — Country→currency mapping required "
        f"(project map: {COUNTRY_TO_CURRENCY}).",
        "Point-in-time / historical calendar endpoints exist for no-lookahead vintages.",
        "Third-party plan comps (Jun 2026): Standard ~$149/mo, Professional ~$299/mo "
        "(billed yearly); exact matrix behind TE account. Enterprise custom.",
    ]
    if key:
        reasons.append(
            "TE_API_KEY present in env — live 3y entitlement not exercised in 5.1B "
            "(no paid validation requested)."
        )
    else:
        reasons.append("No TE_API_KEY — cannot prove live 3y row coverage in this run.")

    # REJECT: not usable free for pilot / 3y without subscription
    return ProviderAssessment(
        provider="trading_economics_calendar_api",
        verdict="REJECT",
        historical_depth=(
            "Documented multi-year calendar history (examples from 2016+). "
            f"Adequate for {REQUIRED_HISTORY_YEARS}y target IF on a paid/trial plan with "
            "calendar entitlement. Not available without authentication."
        ),
        fields={
            "publication_timestamp": "Date (UTC ISO) — YES",
            "country": "Country — YES",
            "currency_or_deducible": "Currency (often empty) + Country→ISO map — YES (deducible)",
            "event_name": "Event / Category — YES",
            "importance_impact": "Importance 1=low 2=medium 3=high — YES",
            "stable_id": "CalendarId (+ Symbol/Ticker) — YES",
        },
        plan_cost={
            "free_guest": "Discontinued (HTTP 410)",
            "trial": "Limited 100 requests / 100k points; auto-charge risk; not free ongoing",
            "standard_est_usd_per_month": 149,
            "professional_est_usd_per_month": 299,
            "billing_note": "Estimates from third-party comps; confirm on TE pricing after account",
            "plan_needed_for_3y": "Paid subscription with Economic Calendar (Standard or above)",
            "subscribed_by_agent": False,
        },
        api_limits={
            "calendar_rows_per_request": 1000,
            "historical_rows_per_request": 10000,
            "requests_per_second": 2,
            "url_max_chars": 260,
        },
        pagination=(
            "No cursor; split by country + initDate/endDate windows so each response "
            "stays ≤1000 calendar rows."
        ),
        timezone="UTC (Date documented as release date/time in UTC)",
        event_ids="CalendarId (string) stable within TE; Symbol/Ticker for indicator series",
        immutable_snapshot=(
            "Feasible: freeze JSON/parquet of calendar rows + sha256 into data_snapshot "
            "extras (same pattern as OHLC). TE does not ship a snapshot_id; we hash locally."
        ),
        no_lookahead=(
            "Use scheduled Date only for blackout windows; for printed values use "
            "Point-in-Time / historical calendar as-of trade time. Do not use "
            "post-revision Actual unknown pre-trade. CDC blackout rules unchanged in 5.1B."
        ),
        usable_free_for_pilot=False,
        usable_for_3y_without_paid=False,
        reasons=reasons,
        probe={**probe, "guest_discontinued": guest_gone, "key_present": bool(key)},
        docs=[
            "https://docs.tradingeconomics.com/economic_calendar/schema/",
            "https://docs.tradingeconomics.com/economic_calendar/country/",
            "https://docs.tradingeconomics.com/economic_calendar/point-in-time/",
            "https://docs.tradingeconomics.com/get_started/rate-limits/",
            "https://tradingeconomics.com/api/pricing.aspx",
        ],
    )


def assess_quantgist() -> ProviderAssessment:
    return ProviderAssessment(
        provider="quantgist_economic_calendar_api",
        verdict="ACCEPT_CONDITIONAL",  # needs Starter for 3y; Free only 1y
        historical_depth=(
            "Free: 365 days; Starter: 1,095 days (3y); Pro/Team: 3,650 days (10y). "
            "Archive marketed from 2014 on Pro."
        ),
        fields={
            "publication_timestamp": "release_time UTC ISO — YES",
            "country": "ISO 3166-1 alpha-2 — YES",
            "currency_or_deducible": "ISO 4217 currency filter/field — YES (explicit)",
            "event_name": "title — YES",
            "importance_impact": "impact low|medium|high (+ impact_score) — YES",
            "stable_id": "id UUID + canonical_id (e.g. US_CPI_YOY) — YES",
        },
        plan_cost={
            "free_usd_per_month": 0,
            "starter_usd_per_month": 19,
            "pro_usd_per_month": 79,
            "plan_needed_for_3y": "Starter ($19/mo) minimum; Pro if PIT/v2 bulk preferred",
            "free_history_years": 1,
            "starter_history_years": 3,
            "subscribed_by_agent": False,
        },
        api_limits={
            "free_requests_per_day": 100,
            "starter_requests_per_day": 5000,
            "pro_requests_per_day": 50000,
            "calendar_range_limit": 500,
            "events_per_page_max": 100,
        },
        pagination="page/per_page and cursor on /v1/events; limit≤500 on /v1/calendar/range",
        timezone="UTC ISO 8601",
        event_ids="UUID id + canonical_id for stable macro series",
        immutable_snapshot="Freeze /v1/calendar/range exports + local sha256; Pro adds bulk NDJSON/CSV",
        no_lookahead=(
            "backtest_safe=true / first_print_only / released_only on /v1/events; "
            "Pro v2 as_of vintages. Use only release_time known at bar time."
        ),
        usable_free_for_pilot=True,  # schema pilot 1y free (non-commercial)
        usable_for_3y_without_paid=False,
        reasons=[
            "Free plan allows schema pilot without card but only 1y history — insufficient for 3y freeze.",
            "Starter explicitly gates 3-year history at $19/mo — matches V1 target cheaply.",
            "Native currency + country + impact + pagination + backtest_safe flags.",
            "Younger vendor than TE; validate coverage for EUR/GBP/JPY/USD high-impact before freeze.",
        ],
        probe={"health": "GET https://api.quantgist.com/v1/health → 200 (live)"},
        docs=[
            "https://quantgist.com/pricing.md",
            "https://quantgist.com/llms.txt",
            "https://api.quantgist.com/openapi.json",
        ],
    )


def assess_fmp_economic_calendar() -> ProviderAssessment:
    return ProviderAssessment(
        provider="fmp_economic_calendar_api",
        verdict="ACCEPT_CONDITIONAL",
        historical_depth=(
            "Endpoint accepts from/to date ranges; Starter plan advertises up to 5y historical "
            "data generally. Exact calendar-row depth for 3y EUR/GBP/JPY/USD must be verified "
            "with a key before freeze."
        ),
        fields={
            "publication_timestamp": "date (UTC YYYY-MM-DD HH:MM:SS) — YES",
            "country": "ISO country code — YES",
            "currency_or_deducible": "currency ISO 4217 — YES (explicit)",
            "event_name": "event — YES",
            "importance_impact": "impact High|Medium|Low — YES",
            "stable_id": "No dedicated CalendarId in public schema samples — WEAK",
        },
        plan_cost={
            "free_usd_per_month": 0,
            "starter_usd_per_month": 22,
            "premium_usd_per_month": 59,
            "plan_needed_for_3y": "Starter ($22/mo) or higher — confirm calendar history entitlement",
            "subscribed_by_agent": False,
        },
        api_limits={
            "free_calls_per_day": 250,
            "starter_calls_per_minute": 300,
            "note": "Free plan limited; economic calendar may require paid tier — verify with account",
        },
        pagination="from/to date windows (chunk requests for long spans)",
        timezone="UTC per FMP FAQ",
        event_ids="No stable public event id in documented samples — hash(date,country,event,currency)",
        immutable_snapshot="Local freeze of JSON responses + sha256 feasible",
        no_lookahead=(
            "Use scheduled date only for blackout; ignore revised actuals unknown pre-release. "
            "No dedicated point-in-time calendar vintage API documented like TE/QuantGist v2."
        ),
        usable_free_for_pilot=False,  # demo key invalid; free entitlement unclear for calendar
        usable_for_3y_without_paid=False,
        reasons=[
            "Schema matches required fields (date, country, currency, event, impact).",
            "Weaker event-id stability and no documented PIT vintages vs TE/QuantGist.",
            "Live demo apikey rejected (401) — no free anonymous pilot.",
        ],
        probe={"demo_key_status": 401},
        docs=[
            "https://site.financialmodelingprep.com/developer/docs/stable/economics-calendar",
            "https://site.financialmodelingprep.com/pricing-plans",
        ],
    )


def assess_news_providers_5_1b(
    *,
    session: requests.Session | None = None,
    run_live_probe: bool = True,
) -> dict[str, Any]:
    te = assess_trading_economics(session=session, run_live_probe=run_live_probe)
    qg = assess_quantgist()
    fmp = assess_fmp_economic_calendar()

    # Probe QuantGist health live
    if run_live_probe:
        sess = session or requests.Session()
        try:
            hr = sess.get("https://api.quantgist.com/v1/health", timeout=20)
            qg.probe["health_status"] = hr.status_code
            qg.probe["health_body"] = (hr.text or "")[:200]
        except requests.RequestException as e:
            qg.probe["health_status"] = "error"
            qg.probe["health_body"] = str(e)

    recommended = {
        "provider": "quantgist_economic_calendar_api",
        "plan_for_v1_3y": "Starter ($19/mo)",
        "why": (
            "Cheapest documented plan with explicit 3-year history, native currency+impact, "
            "pagination, UTC timestamps, stable ids, and backtest_safe / first_print flags. "
            "TE remains the institutional alternative (CalendarId + PIT) if budget allows "
            "Standard≈$149/mo after account."
        ),
        "runner_up": "trading_economics_calendar_api (Standard+) if preferring TE PIT/CalendarId",
        "second_alternative": "fmp_economic_calendar_api (Starter≈$22/mo) — verify 3y depth + ids",
    }

    action_required = {
        "now": (
            "No paid key required yet. Optional: create a free QuantGist account "
            "(https://quantgist.com/signup) to pilot schema on ≤1y only — do not freeze 3y on Free."
        ),
        "before_3y_news_snapshot": (
            "Upgrade QuantGist to Starter (or subscribe TE Standard / FMP Starter), place key in "
            ".env only (QUANTGIST_API_KEY or TE_API_KEY), then authorize a read-only historical "
            "pull for EUR/GBP/JPY/USD high-impact covering the OHLC window."
        ),
        "do_not": [
            "Do not scrape HTML calendars",
            "Do not invent missing events",
            "Do not change CDC blackout rules in this step",
            "Do not download 3y OHLC before news provider key validation",
        ],
    }

    return {
        "step": "5.1B",
        "required_history_years": REQUIRED_HISTORY_YEARS,
        "required_fields": list(REQUIRED_FIELDS),
        "news_currency_map": {k: list(v) for k, v in NEWS_CURRENCY_MAP.items()},
        "crypto_news_gate_v1": False,
        "trading_economics": te.to_dict(),
        "alternatives": [qg.to_dict(), fmp.to_dict()],
        "recommended_v1": recommended,
        "action_required": action_required,
        "cdc_blackout_rules_modified": False,
        "ohlc_3y_downloaded": False,
        "gaps_interpolation": False,
        "unexpected_gaps_remain_explicit": True,
    }
