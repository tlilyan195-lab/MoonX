"""Finnhub economic calendar — interchangeable EconomicCalendarProvider."""

from __future__ import annotations

import os
from typing import Sequence

import pandas as pd
import requests

from trading_signal_bot.data import EconomicCalendarProvider


class FinnhubEconomicCalendarProvider(EconomicCalendarProvider):
    """
    Read-only calendar via FINNHUB_API_KEY.
    Does NOT invent missing historical events.
    If the API cannot cover the requested historical window, raises HistoricalCoverageError.
    """

    def __init__(
        self,
        api_key: str | None = None,
        session: requests.Session | None = None,
        base_url: str = "https://finnhub.io/api/v1",
    ) -> None:
        self.api_key = api_key or os.environ.get("FINNHUB_API_KEY", "")
        self.session = session or requests.Session()
        self.base_url = base_url.rstrip("/")
        self.provider_name = "finnhub_economic_calendar"

    def high_impact_events(
        self,
        start: pd.Timestamp,
        end: pd.Timestamp,
        currencies: Sequence[str] | None = None,
    ) -> pd.DataFrame:
        if not self.api_key:
            raise HistoricalCoverageError(
                "FINNHUB_API_KEY missing — cannot fetch economic calendar. "
                "Do not simulate missing news data."
            )
        start_t = pd.Timestamp(start)
        end_t = pd.Timestamp(end)
        if start_t.tzinfo is None:
            start_t = start_t.tz_localize("UTC")
        else:
            start_t = start_t.tz_convert("UTC")
        if end_t.tzinfo is None:
            end_t = end_t.tz_localize("UTC")
        else:
            end_t = end_t.tz_convert("UTC")
        start, end = start_t, end_t
        params = {
            "token": self.api_key,
            "from": start.strftime("%Y-%m-%d"),
            "to": end.strftime("%Y-%m-%d"),
        }
        r = self.session.get(f"{self.base_url}/calendar/economic", params=params, timeout=30)
        if r.status_code == 403:
            raise HistoricalCoverageError(
                f"Finnhub denied calendar access (HTTP 403). Cannot reconstruct news blackout. Body={r.text[:200]}"
            )
        r.raise_for_status()
        payload = r.json()
        economic = payload.get("economicCalendar") or payload.get("economic") or []
        if not economic:
            # Empty can mean no events OR no historical entitlement — distinguish by probing
            raise HistoricalCoverageError(
                "Finnhub returned empty economicCalendar for the requested window. "
                "Cannot verify historical news blackout coverage — STOP before TRAIN/VAL. "
                "Do not simulate missing events."
            )

        rows = []
        for ev in economic:
            ts_raw = ev.get("time") or ev.get("date")
            if not ts_raw:
                continue
            ts = pd.Timestamp(ts_raw)
            if ts.tzinfo is None:
                ts = ts.tz_localize("UTC")
            else:
                ts = ts.tz_convert("UTC")
            impact = str(ev.get("impact") or ev.get("importance") or "").lower()
            currency = str(ev.get("country") or ev.get("currency") or "").upper()
            event = str(ev.get("event") or ev.get("name") or "")
            rows.append(
                {
                    "ts_utc": ts,
                    "currency": currency,
                    "event": event,
                    "impact": impact,
                    "raw": ev,
                }
            )
        df = pd.DataFrame(rows)
        if df.empty:
            raise HistoricalCoverageError(
                "Finnhub events missing required fields (timestamp/currency/event). "
                "STOP before TRAIN/VAL."
            )
        # Require exploitable impact field
        if df["impact"].isna().all() or (df["impact"] == "").all():
            raise HistoricalCoverageError(
                "Finnhub impact/importance not exploitable for high-impact filtering. "
                "STOP before TRAIN/VAL."
            )
        required = {"ts_utc", "currency", "event", "impact"}
        if not required.issubset(df.columns):
            raise HistoricalCoverageError(f"Missing columns {required - set(df.columns)}")

        # Filter window precisely
        df = df[(df["ts_utc"] >= start) & (df["ts_utc"] <= end)]
        if currencies:
            cur = {c.upper() for c in currencies}
            df = df[df["currency"].isin(cur)]
        # High impact only for gate (keep all in return for inspection; caller filters)
        return df.sort_values("ts_utc").reset_index(drop=True)


class HistoricalCoverageError(RuntimeError):
    """Raised when calendar history cannot be reconstructed — must STOP before backtest."""
