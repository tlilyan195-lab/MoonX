"""NEWS GATE V1 — expand JBlanked high-impact events onto bar timestamps.

Locked rules:
- Use scheduled ts_utc only (UTC)
- Match Impact High + Currency via NEWS_CURRENCY_MAP
- Window from strategy config (major_window_minutes for High)
- No synthetic events
"""

from __future__ import annotations

from typing import Iterable

import pandas as pd

from trading_signal_bot.data.jblanked_calendar import GATE_IMPACT
from trading_signal_bot.data.news_provider_validation import NEWS_CURRENCY_MAP


def filter_high_impact(events: pd.DataFrame) -> pd.DataFrame:
    if events is None or events.empty:
        return events.iloc[0:0] if events is not None else pd.DataFrame()
    impact = events["impact"].astype(str).str.lower()
    return events.loc[impact == GATE_IMPACT].copy()


def events_for_symbol(events: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Filter high-impact events whose currency is in the symbol map."""
    if events is None or events.empty:
        return pd.DataFrame()
    sym = symbol.upper()
    if sym not in NEWS_CURRENCY_MAP:
        # Crypto / unmapped → no macro FX news gate V1
        return pd.DataFrame()
    cur = {c.upper() for c in NEWS_CURRENCY_MAP[sym]}
    hi = filter_high_impact(events)
    if hi.empty:
        return hi
    return hi.loc[hi["currency"].astype(str).str.upper().isin(cur)].copy()


def expand_blackout_timestamps(
    events: pd.DataFrame,
    bar_index: Iterable[pd.Timestamp] | pd.DatetimeIndex,
    window_minutes: int,
) -> set[pd.Timestamp]:
    """
    Bars whose close time falls within [event_ts - w, event_ts + w] (inclusive).
    Missing events → empty set (no-event).
    """
    if events is None or len(events) == 0:
        return set()
    if window_minutes < 0:
        raise ValueError("window_minutes must be >= 0")
    idx = pd.DatetimeIndex(pd.to_datetime(list(bar_index), utc=True)).sort_values()
    if idx.empty:
        return set()
    delta = pd.Timedelta(minutes=int(window_minutes))
    out: set[pd.Timestamp] = set()
    for ts in events["ts_utc"]:
        ts_u = pd.Timestamp(ts)
        if ts_u.tzinfo is None:
            ts_u = ts_u.tz_localize("UTC")
        else:
            ts_u = ts_u.tz_convert("UTC")
        left = ts_u - delta
        right = ts_u + delta
        # slice on sorted DatetimeIndex
        hit = idx[(idx >= left) & (idx <= right)]
        out.update(hit.tolist())
    return out


def blackout_set_for_symbol(
    events: pd.DataFrame,
    symbol: str,
    bar_index: Iterable[pd.Timestamp] | pd.DatetimeIndex,
    window_minutes: int = 30,
    major_window_minutes: int | None = 60,
) -> set[pd.Timestamp]:
    """
    V1: High-impact uses major_window_minutes when provided, else window_minutes.
    """
    sym_events = events_for_symbol(events, symbol)
    w = int(major_window_minutes if major_window_minutes is not None else window_minutes)
    return expand_blackout_timestamps(sym_events, bar_index, w)
