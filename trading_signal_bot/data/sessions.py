"""FX / crypto session and trading-day helpers (America/New_York DST-aware)."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


NY = "America/New_York"


def to_utc(ts: pd.Timestamp) -> pd.Timestamp:
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def fx_trading_day_id(ts: pd.Timestamp) -> pd.Timestamp:
    """
    FX trading day labeled by its START at 17:00 America/New_York.
    Day = [17:00 NY, next 17:00 NY).
    """
    ts_ny = to_utc(ts).tz_convert(NY)
    cutoff = ts_ny.normalize() + pd.Timedelta(hours=17)
    if ts_ny < cutoff:
        start = cutoff - pd.Timedelta(days=1)
    else:
        start = cutoff
    return start.tz_convert("UTC")


def previous_fx_trading_day_bounds(ts: pd.Timestamp) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Return [start, end) UTC of the previous complete FX trading day."""
    current_start = fx_trading_day_id(ts)
    prev_start = current_start - pd.Timedelta(days=1)
    # Skip if landing on Saturday open weirdness: walk back until we have a weekday span
    # FX week: Sun 17:00 NY -> Fri 17:00 NY. Sat/Sun day ids may exist; PDH uses previous complete day.
    return prev_start, current_start


def fx_week_start(ts: pd.Timestamp) -> pd.Timestamp:
    """
    FX week start = Sunday 17:00 America/New_York (T1 validated).
    Week = [Sun 17:00 NY, Fri 17:00 NY] for trading; label by Sunday open.
    """
    ts_ny = to_utc(ts).tz_convert(NY)
    # weekday: Mon=0 ... Sun=6
    days_since_sunday = (ts_ny.weekday() + 1) % 7
    sunday = (ts_ny.normalize() - pd.Timedelta(days=days_since_sunday)) + pd.Timedelta(hours=17)
    if ts_ny < sunday:
        sunday = sunday - pd.Timedelta(days=7)
    # After Friday 17:00 NY, still in same week until Sunday 17:00
    friday_close = sunday + pd.Timedelta(days=5)  # Fri 17:00
    if ts_ny >= friday_close and ts_ny < sunday + pd.Timedelta(days=7):
        # weekend after Fri close belongs to outgoing week label = sunday
        pass
    return sunday.tz_convert("UTC")


def previous_fx_week_bounds(ts: pd.Timestamp) -> tuple[pd.Timestamp, pd.Timestamp]:
    week_start = fx_week_start(ts)
    prev_start = week_start - pd.Timedelta(days=7)
    prev_end = week_start  # Sunday 17:00; trading ended Fri 17:00 but high/low week uses full label span until next Sun
    # Spec: week Sun 17:00 -> Fri 17:00. For PWH/PWL use [prev_start, prev_start+5days) i.e. until Fri 17:00
    prev_fri_close = prev_start + pd.Timedelta(days=5)
    return prev_start, prev_fri_close


def crypto_utc_day_bounds(ts: pd.Timestamp) -> tuple[pd.Timestamp, pd.Timestamp]:
    ts_u = to_utc(ts)
    start = ts_u.normalize()
    return start, start + pd.Timedelta(days=1)


def previous_crypto_utc_day_bounds(ts: pd.Timestamp) -> tuple[pd.Timestamp, pd.Timestamp]:
    start, _ = crypto_utc_day_bounds(ts)
    return start - pd.Timedelta(days=1), start


@dataclass(frozen=True)
class SessionWindow:
    tz: str
    start: str  # HH:MM local
    end: str  # HH:MM local


def _parse_hhmm(value: str) -> tuple[int, int]:
    h, m = value.split(":")
    return int(h), int(m)


def is_in_session_windows(ts: pd.Timestamp, windows: list[SessionWindow]) -> bool:
    ts_u = to_utc(ts)
    for w in windows:
        local = ts_u.tz_convert(w.tz)
        sh, sm = _parse_hhmm(w.start)
        eh, em = _parse_hhmm(w.end)
        start_ok = (local.hour, local.minute) >= (sh, sm)
        end_ok = (local.hour, local.minute) < (eh, em)
        if start_ok and end_ok:
            return True
    return False
