"""
Finnhub economic-calendar coverage assessment for news-gate V1.

Does NOT scrape, invent, or reconstruct missing events.
Assesses whether available Finnhub access can supply a multi-year
historical economic calendar with the fields required for blackout:
  - publication timestamp
  - country/currency
  - event name
  - impact/importance level
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
import requests

from trading_signal_bot.data.finnhub_calendar import (
    FinnhubEconomicCalendarProvider,
    HistoricalCoverageError,
)


# Required schema for news-gate blackout reconstruction
REQUIRED_EVENT_FIELDS = ("ts_utc", "currency", "event", "impact")

# Public pricing / docs signals (no key required for this static assessment)
FINNHUB_FREE_ECONOMIC_CALENDAR = False
FINNHUB_ALL_IN_ONE_ECONOMIC_CALENDAR = True
# Earnings calendar free depth is ~1 month; economic calendar is marked paid on pricing.
# Even when `/calendar/economic` responds on a free key for a short window, multi-year
# FX-relevant history is not an entitled free capability for this project.
FINNHUB_DOCUMENTED_FREE_HISTORICAL_YEARS = 0
REQUIRED_HISTORY_YEARS = 3


@dataclass
class NewsProviderVerdict:
    provider: str
    key_present: bool
    usable_for_3y_blackout: bool
    verdict: str  # ACCEPT | REJECT | INCONCLUSIVE_NEED_KEY_BUT_LIKELY_INSUFFICIENT
    reasons: list[str] = field(default_factory=list)
    required_fields: tuple[str, ...] = REQUIRED_EVENT_FIELDS
    probe: dict[str, Any] = field(default_factory=dict)
    alternative: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "key_present": self.key_present,
            "usable_for_3y_blackout": self.usable_for_3y_blackout,
            "verdict": self.verdict,
            "reasons": self.reasons,
            "required_fields": list(self.required_fields),
            "probe": self.probe,
            "alternative": self.alternative,
        }


def trading_economics_alternative() -> dict[str, Any]:
    """Documented alternative — not implemented; no scrape."""
    return {
        "provider": "trading_economics_calendar_api",
        "why": (
            "Historical economic calendar API with Date (UTC timestamp), Country, "
            "Event, and Importance (1=low,2=medium,3=high) — matches news-gate fields. "
            "Supports multi-year country/date range queries and point-in-time snapshots."
        ),
        "docs": "https://docs.tradingeconomics.com/economic_calendar/schema/",
        "endpoint_example": (
            "https://api.tradingeconomics.com/calendar/country/{countries}/"
            "{initDate}/{endDate}?c={api_key}"
        ),
        "required_fields_mapping": {
            "ts_utc": "Date",
            "currency/country": "Country",
            "event": "Event",
            "impact": "Importance",
        },
        "notes": (
            "Paid API. Do not scrape tradingeconomics.com HTML. "
            "Validate 3y EUR/GBP/JPY/USD high-importance coverage with a trial key "
            "before TRAIN/VAL."
        ),
    }


def assess_finnhub_historical_coverage(
    *,
    api_key: str | None = None,
    session: requests.Session | None = None,
    history_years: int = REQUIRED_HISTORY_YEARS,
) -> NewsProviderVerdict:
    """
    Static + optional live probe.

    Absence of FINNHUB_API_KEY is NOT treated as the sole failure mode:
    Finnhub Free pricing does not entitle Economic Calendar / Historical Economic
    Data for a 3-year FX blackout rebuild.
    """
    key = api_key if api_key is not None else os.environ.get("FINNHUB_API_KEY", "")
    reasons: list[str] = []
    probe: dict[str, Any] = {
        "finnhub_pricing_economic_calendar_free": FINNHUB_FREE_ECONOMIC_CALENDAR,
        "finnhub_pricing_economic_calendar_all_in_one": FINNHUB_ALL_IN_ONE_ECONOMIC_CALENDAR,
        "documented_free_historical_years": FINNHUB_DOCUMENTED_FREE_HISTORICAL_YEARS,
        "required_history_years": history_years,
        "required_fields": list(REQUIRED_EVENT_FIELDS),
        "live_probe": None,
    }

    reasons.append(
        "Finnhub public pricing: Economic Calendar and Historical Economic Data are "
        "not included on the Free plan (All-In-One only)."
    )
    reasons.append(
        f"Project needs ≥{history_years}y history with timestamp, country/currency, "
        "event, impact — Free entitlements document 0 years of economic-calendar history."
    )

    if not key:
        reasons.append(
            "FINNHUB_API_KEY absent — cannot run a live entitlement probe; "
            "key alone would not fix Free-plan historical coverage."
        )
        return NewsProviderVerdict(
            provider="finnhub_economic_calendar",
            key_present=False,
            usable_for_3y_blackout=False,
            verdict="REJECT",
            reasons=reasons,
            probe=probe,
            alternative=trading_economics_alternative(),
        )

    # Live probe with key: try a 3y window and a recent short window
    provider = FinnhubEconomicCalendarProvider(api_key=key, session=session)
    end = pd.Timestamp.now(tz="UTC").normalize()
    start_3y = end - pd.DateOffset(years=history_years)
    start_1m = end - pd.DateOffset(days=30)
    live: dict[str, Any] = {"window_3y": None, "window_1m": None}
    usable = False
    try:
        df_3y = provider.high_impact_events(start_3y, end)
        live["window_3y"] = {
            "ok": True,
            "n_events": int(len(df_3y)),
            "min_ts": str(df_3y["ts_utc"].min()) if len(df_3y) else None,
            "max_ts": str(df_3y["ts_utc"].max()) if len(df_3y) else None,
            "columns": list(df_3y.columns),
        }
        # Require events spanning most of the window (not just recent)
        if len(df_3y) > 0:
            span_days = (df_3y["ts_utc"].max() - df_3y["ts_utc"].min()).days
            live["window_3y"]["span_days"] = int(span_days)
            if span_days >= int(365 * history_years * 0.8):
                usable = True
                reasons.append(
                    f"Live 3y probe returned {len(df_3y)} events spanning {span_days} days."
                )
            else:
                reasons.append(
                    f"Live probe returned events but span_days={span_days} "
                    f"< 80% of {history_years}y — insufficient for blackout rebuild."
                )
    except HistoricalCoverageError as e:
        live["window_3y"] = {"ok": False, "error": str(e)}
        reasons.append(f"3y live probe failed: {e}")
    except requests.HTTPError as e:
        live["window_3y"] = {"ok": False, "error": str(e)}
        reasons.append(f"3y live probe HTTP error: {e}")

    try:
        df_1m = provider.high_impact_events(start_1m, end)
        live["window_1m"] = {"ok": True, "n_events": int(len(df_1m))}
    except Exception as e:  # noqa: BLE001 — diagnostic only
        live["window_1m"] = {"ok": False, "error": str(e)}

    probe["live_probe"] = live
    verdict = "ACCEPT" if usable else "REJECT"
    return NewsProviderVerdict(
        provider="finnhub_economic_calendar",
        key_present=True,
        usable_for_3y_blackout=usable,
        verdict=verdict,
        reasons=reasons,
        probe=probe,
        alternative=None if usable else trading_economics_alternative(),
    )
