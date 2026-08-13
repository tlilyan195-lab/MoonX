"""JBlanked economic calendar — locked D2 provider for NEWS GATE V1.

Rules (frozen):
- Scheduled Date only, normalized to UTC (naive Date → tz_localize UTC)
- Gate filter: Impact == High + Currency match
- Ignore Actual / Outcome / Strength / Quality (never used for timing)
- Missing/zero Event_ID → fingerprint(Name|Date|Currency)
- Missing events → empty frame (no synthetic fill)
"""

from __future__ import annotations

import hashlib
import os
import re
from typing import Any, Sequence

import pandas as pd
import requests

from trading_signal_bot.data import EconomicCalendarProvider
from trading_signal_bot.data.finnhub_calendar import HistoricalCoverageError
from trading_signal_bot.data.news_provider_validation import NEWS_CURRENCY_MAP

PROVIDER_ID = "jblanked"
PROVIDER_NAME = "jblanked_mql5_calendar"
BASE_RANGE_URL = "https://www.jblanked.com/news/api/mql5/calendar/range/"

# Locked V1 news-gate rules
NEWS_TZ = "UTC"
GATE_IMPACT = "high"
IGNORE_FIELDS = frozenset({"Actual", "Outcome", "Strength", "Quality", "actual", "outcome", "strength", "quality"})

DATE_RE = re.compile(r"^(\d{4})[.\-/](\d{2})[.\-/](\d{2})[ T](\d{2}):(\d{2}):(\d{2})$")


def event_fingerprint(name: str, date_raw: str, currency: str) -> str:
    raw = f"{name}|{date_raw}|{currency.upper()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def parse_scheduled_date_utc(raw: Any) -> pd.Timestamp | None:
    """Parse provider Date as scheduled time; freeze as UTC (no TZ inference)."""
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    if isinstance(raw, pd.Timestamp):
        ts = raw
        if ts.tzinfo is None:
            return ts.tz_localize(NEWS_TZ)
        return ts.tz_convert(NEWS_TZ)
    s = str(raw).strip()
    m = DATE_RE.match(s)
    if m:
        y, mo, d, h, mi, sec = map(int, m.groups())
        return pd.Timestamp(year=y, month=mo, day=d, hour=h, minute=mi, second=sec, tz=NEWS_TZ)
    try:
        ts = pd.Timestamp(s)
    except Exception:
        return None
    if ts.tzinfo is None:
        return ts.tz_localize(NEWS_TZ)
    return ts.tz_convert(NEWS_TZ)


def _get(row: dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if k in row and row[k] not in (None, ""):
            return row[k]
    lower = {str(k).lower(): v for k, v in row.items()}
    for k in keys:
        if k.lower() in lower and lower[k.lower()] not in (None, ""):
            return lower[k.lower()]
    return None


def normalize_event_row(row: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize one API row. Drops look-ahead fields from output."""
    if not isinstance(row, dict):
        return None
    name = str(_get(row, "Name", "name", "event") or "").strip()
    currency = str(_get(row, "Currency", "currency") or "").strip().upper()
    impact = str(_get(row, "Impact", "impact") or "").strip()
    date_raw = _get(row, "Date", "date", "datetime")
    ts = parse_scheduled_date_utc(date_raw)
    if ts is None or not currency or not name:
        return None
    eid_raw = _get(row, "Event_ID", "EventID", "event_id", "eventID", "id")
    try:
        eid_num = int(eid_raw) if eid_raw is not None and str(eid_raw).strip() != "" else 0
    except (TypeError, ValueError):
        eid_num = 0
    if eid_num == 0:
        event_id = event_fingerprint(name, str(date_raw), currency)
        event_id_source = "fingerprint_name_date_currency"
    else:
        event_id = str(eid_num)
        event_id_source = "provider_event_id"
    return {
        "ts_utc": ts,
        "currency": currency,
        "event": name,
        "impact": impact.lower(),
        "event_id": event_id,
        "event_id_source": event_id_source,
        "category": str(_get(row, "Category", "category") or ""),
        # Explicitly do NOT include Actual/Outcome/Strength/Quality
    }


def rows_from_api_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        raw_rows = payload
    elif isinstance(payload, dict):
        raw_rows = None
        for k in ("data", "events", "results", "calendar"):
            if isinstance(payload.get(k), list):
                raw_rows = payload[k]
                break
        if raw_rows is None:
            return []
    else:
        return []
    out: list[dict[str, Any]] = []
    for r in raw_rows:
        norm = normalize_event_row(r) if isinstance(r, dict) else None
        if norm is not None:
            out.append(norm)
    return out


def currencies_for_symbol(symbol: str) -> tuple[str, ...]:
    return NEWS_CURRENCY_MAP.get(symbol.upper(), ())


class JBlankedEconomicCalendarProvider(EconomicCalendarProvider):
    """Locked D2 for V1. READ-ONLY. Does not invent events."""

    def __init__(
        self,
        api_key: str | None = None,
        session: requests.Session | None = None,
        base_url: str = BASE_RANGE_URL,
    ) -> None:
        self.api_key = (api_key if api_key is not None else os.environ.get("JBLANKED_API_KEY", "")).strip()
        self.session = session or requests.Session()
        self.base_url = base_url
        self.provider_name = PROVIDER_NAME
        self.provider_id = PROVIDER_ID
        self.last_fetch_meta: dict[str, Any] = {}

    def high_impact_events(
        self,
        start: pd.Timestamp,
        end: pd.Timestamp,
        currencies: Sequence[str] | None = None,
    ) -> pd.DataFrame:
        """
        Fetch calendar rows and return high-impact events only.

        Empty result means no-event for the window (not an error).
        Auth / HTTP failures raise HistoricalCoverageError.
        """
        df = self.fetch_events(start, end, currencies=currencies, high_impact_only=True)
        return df

    def fetch_events(
        self,
        start: pd.Timestamp,
        end: pd.Timestamp,
        currencies: Sequence[str] | None = None,
        high_impact_only: bool = False,
    ) -> pd.DataFrame:
        start_t = pd.Timestamp(start)
        end_t = pd.Timestamp(end)
        if start_t.tzinfo is None:
            start_t = start_t.tz_localize(NEWS_TZ)
        else:
            start_t = start_t.tz_convert(NEWS_TZ)
        if end_t.tzinfo is None:
            end_t = end_t.tz_localize(NEWS_TZ)
        else:
            end_t = end_t.tz_convert(NEWS_TZ)

        if not self.api_key:
            # Allow offline cache via load_events_parquet in snapshot scripts.
            raise HistoricalCoverageError(
                "JBLANKED_API_KEY missing — cannot fetch D2 calendar. "
                "Do not invent events; use a cached news parquet if available."
            )

        params = {
            "from": start_t.strftime("%Y-%m-%d"),
            "to": end_t.strftime("%Y-%m-%d"),
        }
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Api-Key {self.api_key}",
            "User-Agent": "MoonX-JBlankedD2/1.0 (+signals-only; no-trading)",
        }
        try:
            r = self.session.get(self.base_url, params=params, headers=headers, timeout=60)
        except requests.RequestException as exc:
            raise HistoricalCoverageError(f"JBlanked network error: {exc}") from exc

        self.last_fetch_meta = {
            "http_status": r.status_code,
            "from": params["from"],
            "to": params["to"],
            "url_safe": f"{self.base_url}?from={params['from']}&to={params['to']}",
        }
        if r.status_code in (401, 403):
            raise HistoricalCoverageError(
                f"JBlanked auth failed HTTP {r.status_code}. Body={r.text[:240]}"
            )
        if r.status_code == 429:
            raise HistoricalCoverageError(
                "JBlanked rate limited (429). Free=1 req/day — retry later; do not invent events."
            )
        if r.status_code >= 400:
            raise HistoricalCoverageError(
                f"JBlanked HTTP {r.status_code}. Body={r.text[:240]}"
            )

        try:
            payload = r.json()
        except ValueError as exc:
            raise HistoricalCoverageError("JBlanked returned non-JSON body") from exc

        rows = rows_from_api_payload(payload)
        # Missing events → empty frame (no synthetic fill)
        if not rows:
            self.last_fetch_meta["n_events_raw"] = 0
            return _empty_events_frame()

        df = pd.DataFrame(rows)
        df = df[(df["ts_utc"] >= start_t) & (df["ts_utc"] <= end_t)]
        if currencies:
            cur = {c.upper() for c in currencies}
            df = df[df["currency"].isin(cur)]
        if high_impact_only:
            df = df[df["impact"] == GATE_IMPACT]
        df = df.sort_values("ts_utc").reset_index(drop=True)
        self.last_fetch_meta["n_events_raw"] = int(len(rows))
        self.last_fetch_meta["n_events_returned"] = int(len(df))
        self.last_fetch_meta["high_impact_only"] = high_impact_only
        self.last_fetch_meta["timezone"] = NEWS_TZ
        return df


def _empty_events_frame() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "ts_utc",
            "currency",
            "event",
            "impact",
            "event_id",
            "event_id_source",
            "category",
        ]
    )


def load_events_parquet(path: str | os.PathLike[str]) -> pd.DataFrame:
    """Load cached normalized events. Empty/missing file → empty frame (no-event)."""
    p = os.fspath(path)
    if not os.path.isfile(p):
        return _empty_events_frame()
    df = pd.read_parquet(p)
    if df.empty:
        return _empty_events_frame()
    if "ts_utc" in df.columns:
        df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True)
    return df


def save_events_parquet(df: pd.DataFrame, path: str | os.PathLike[str]) -> None:
    p = os.fspath(path)
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    out = df.copy()
    if not out.empty and "ts_utc" in out.columns:
        out["ts_utc"] = pd.to_datetime(out["ts_utc"], utc=True)
    out.to_parquet(p, index=False)
