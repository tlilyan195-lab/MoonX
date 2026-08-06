"""
Trading calendars for gap classification (Dukascopy FX / XAU, crypto 24/7).

No candle filling/interpolation. Used only to compute expected tradable bars
and classify missing timestamps as scheduled closed vs unexpected missing.

Dukascopy boundaries are expressed in America/New_York (DST-aware), which
matches documented UTC settlement shifts (21:00 GMT summer / 22:00 GMT winter):

- FX week: Sun 17:00 NY → Fri 17:00 NY
- XAU daily break: every Mon–Thu 17:00–18:00 NY
- XAU US national holidays: extra closed 13:00–17:00 NY (then daily break)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable

import pandas as pd

NY = "America/New_York"


class ClosedReason(str, Enum):
    WEEKEND = "weekend_market_close"
    DAILY_BREAK = "daily_trading_break"
    US_HOLIDAY_WINDOW = "us_holiday_official_closed"
    FULL_HOLIDAY = "full_holiday_session_closed"
    TRADABLE = "tradable"


@dataclass(frozen=True)
class CalendarStats:
    expected_trading_bars: int
    observed_bars: int
    observed_in_expected: int
    scheduled_closed_bars: int
    unexpected_missing_bars: int
    unexpected_missing_pct: float
    unexpected_gap_count: int
    unexpected_gap_samples: list[dict]
    closed_reason_counts: dict[str, int]


def _as_utc(ts: pd.Timestamp) -> pd.Timestamp:
    ts = pd.Timestamp(ts)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def _as_ny(ts: pd.Timestamp) -> pd.Timestamp:
    return _as_utc(ts).tz_convert(NY)


def is_us_dst_summer(ts: pd.Timestamp) -> bool:
    local = _as_ny(ts)
    return bool(local.dst() is not None and local.dst() != pd.Timedelta(0))


def dukascopy_settlement_hour_utc(ts: pd.Timestamp) -> int:
    """Dukascopy day-change hour in UTC (21 summer / 22 winter)."""
    return 21 if is_us_dst_summer(ts) else 22


# US federal holidays (America/New_York calendar dates) for Dukascopy XAU footnote.
_US_FEDERAL_HOLIDAYS_NY: set[str] = {
    "2024-01-01",
    "2024-01-15",
    "2024-02-19",
    "2024-05-27",
    "2024-06-19",
    "2024-07-04",
    "2024-09-02",
    "2024-10-14",
    "2024-11-11",
    "2024-11-28",
    "2024-12-25",
    "2025-01-01",
    "2025-01-20",
    "2025-02-17",
    "2025-05-26",
    "2025-06-19",
    "2025-07-04",
    "2025-09-01",
    "2025-10-13",
    "2025-11-11",
    "2025-11-27",
    "2025-12-25",
    "2026-01-01",
    "2026-01-19",
    "2026-02-16",
    "2026-05-25",
    "2026-06-19",
    "2026-07-03",
    "2026-09-07",
    "2026-10-12",
    "2026-11-11",
    "2026-11-26",
    "2026-12-25",
}

# FX majors: Christmas Day + New Year's Day are treated as full scheduled closures
# (broker holiday / non-tradable session for quality gating).
_FX_FULL_HOLIDAYS_NY: set[str] = set()
for y in (2024, 2025, 2026):
    _FX_FULL_HOLIDAYS_NY.add(f"{y}-12-25")
    _FX_FULL_HOLIDAYS_NY.add(f"{y}-01-01")


def _ny_date_str(ts: pd.Timestamp) -> str:
    return _as_ny(ts).strftime("%Y-%m-%d")


def is_us_federal_holiday(ts: pd.Timestamp) -> bool:
    return _ny_date_str(ts) in _US_FEDERAL_HOLIDAYS_NY


def _ny_minutes(ts: pd.Timestamp) -> tuple[int, int, int]:
    """Return (weekday Mon=0, minutes_from_midnight,) and raw minutes."""
    ny = _as_ny(ts)
    return ny.weekday(), ny.hour * 60 + ny.minute


def classify_fx_bar_close(ts: pd.Timestamp) -> ClosedReason:
    """
    FX majors on Dukascopy: Sun 17:05 NY first close → Fri 17:00 NY last close.

    Note: Dukascopy historical ticks in the pilot end every Friday at 21:00 UTC
    even in winter (when Fri 17:00 NY = 22:00 UTC). The final winter Friday hour
    is therefore counted as unexpected_missing vs the live tradable calendar.
    Christmas Eve early-thin liquidity after 17:00 NY is treated as scheduled
    holiday closure for quality gating (no fill).
    """
    ny_date = _ny_date_str(ts)
    if ny_date in _FX_FULL_HOLIDAYS_NY:
        return ClosedReason.FULL_HOLIDAY
    # Christmas Eve: treat post-17:00 NY as scheduled closed (thin/holiday desk)
    if ny_date.endswith("-12-24"):
        wd, minutes = _ny_minutes(ts)
        if minutes > 17 * 60:
            return ClosedReason.FULL_HOLIDAY

    wd, minutes = _ny_minutes(ts)
    open_m = 17 * 60
    close_m = 17 * 60

    if wd == 5:  # Saturday
        return ClosedReason.WEEKEND
    if wd == 6:  # Sunday — first close 17:05
        if minutes < open_m + 5:
            return ClosedReason.WEEKEND
        return ClosedReason.TRADABLE
    if wd == 4:  # Friday — last close 17:00 inclusive
        if minutes > close_m:
            return ClosedReason.WEEKEND
        return ClosedReason.TRADABLE
    return ClosedReason.TRADABLE  # Mon–Thu


def classify_xau_bar_close(ts: pd.Timestamp) -> ClosedReason:
    """
    XAUUSD Dukascopy:
    - Week: Sun 18:05 NY first close → Fri 17:00 NY last close
      (Sunday open is 18:00 NY / 22:00 UTC summer / 23:00 UTC winter — one hour
      after FX open; matches Dukascopy gold hours + empirical pilot ticks)
    - Daily break Mon–Thu 17:00–18:00 NY
    - US national holidays: closed 13:00–18:00 NY (holiday window + break)
    - Christmas Day / New Year's Day: full dark session
    - Christmas Eve + day-after-Thanksgiving: early close from 13:00 NY
      (known metals liquidity cut; no interpolation of missing bars)
    """
    ny_date = _ny_date_str(ts)
    if ny_date.endswith("-12-25") or ny_date.endswith("-01-01"):
        return ClosedReason.FULL_HOLIDAY

    wd, minutes = _ny_minutes(ts)
    fx_open_m = 17 * 60
    xau_sun_open_m = 18 * 60  # XAU opens 1h after FX on Sunday
    close_m = 17 * 60
    break_start = 17 * 60
    break_end = 18 * 60
    holiday_start = 13 * 60

    # Early-close sessions for metals (scheduled closed after 13:00 NY)
    if ny_date.endswith("-12-24") and minutes > holiday_start:
        return ClosedReason.FULL_HOLIDAY
    # Day after US Thanksgiving (fourth Thu in Nov + 1 day) — use fixed observed dates
    if ny_date in {"2024-11-29", "2025-11-28", "2026-11-27"} and minutes > holiday_start:
        return ClosedReason.FULL_HOLIDAY

    if wd == 5:
        return ClosedReason.WEEKEND
    if wd == 6:
        if minutes < xau_sun_open_m + 5:
            return ClosedReason.WEEKEND
        return ClosedReason.TRADABLE
    if wd == 4:
        if is_us_federal_holiday(ts) and minutes > holiday_start:
            return ClosedReason.US_HOLIDAY_WINDOW
        if minutes > close_m:
            return ClosedReason.WEEKEND
        return ClosedReason.TRADABLE

    # Mon–Thu
    if is_us_federal_holiday(ts) and holiday_start < minutes <= break_end:
        if minutes <= break_start:
            return ClosedReason.US_HOLIDAY_WINDOW
        return ClosedReason.DAILY_BREAK

    if break_start < minutes <= break_end:
        return ClosedReason.DAILY_BREAK

    return ClosedReason.TRADABLE


def classify_crypto_bar_close(_ts: pd.Timestamp) -> ClosedReason:
    return ClosedReason.TRADABLE


def classify_bar_close(ts: pd.Timestamp, asset_class: str) -> ClosedReason:
    ac = asset_class.upper()
    if ac == "CRYPTO":
        return classify_crypto_bar_close(ts)
    if ac == "XAU":
        return classify_xau_bar_close(ts)
    return classify_fx_bar_close(ts)


def expected_close_index(
    start: pd.Timestamp,
    end: pd.Timestamp,
    timeframe: str,
    asset_class: str,
) -> pd.DatetimeIndex:
    delta = {
        "5M": pd.Timedelta(minutes=5),
        "15M": pd.Timedelta(minutes=15),
        "1H": pd.Timedelta(hours=1),
        "4H": pd.Timedelta(hours=4),
    }[timeframe]
    start = _as_utc(start)
    end = _as_utc(end)
    grid = pd.date_range(start=start.ceil(delta), end=end.floor(delta), freq=delta, tz="UTC")
    keep = [t for t in grid if classify_bar_close(t, asset_class) == ClosedReason.TRADABLE]
    return pd.DatetimeIndex(keep, tz="UTC")


def scheduled_closed_index(
    start: pd.Timestamp,
    end: pd.Timestamp,
    timeframe: str,
    asset_class: str,
) -> pd.DatetimeIndex:
    delta = {
        "5M": pd.Timedelta(minutes=5),
        "15M": pd.Timedelta(minutes=15),
        "1H": pd.Timedelta(hours=1),
        "4H": pd.Timedelta(hours=4),
    }[timeframe]
    start = _as_utc(start)
    end = _as_utc(end)
    grid = pd.date_range(start=start.ceil(delta), end=end.floor(delta), freq=delta, tz="UTC")
    keep = [t for t in grid if classify_bar_close(t, asset_class) != ClosedReason.TRADABLE]
    return pd.DatetimeIndex(keep, tz="UTC")


def _count_contiguous_gaps(missing: Iterable[pd.Timestamp], delta: pd.Timedelta) -> tuple[int, list[dict]]:
    misses = sorted(pd.Timestamp(t) for t in missing)
    if not misses:
        return 0, []
    gaps = 0
    samples: list[dict] = []
    run_start = misses[0]
    prev = misses[0]
    for t in misses[1:]:
        if t - prev <= delta * 1.01:
            prev = t
            continue
        gaps += 1
        if len(samples) < 25:
            samples.append(
                {
                    "from": str(run_start),
                    "to": str(prev),
                    "bars": int(round((prev - run_start) / delta)) + 1,
                    "class": "unexpected_missing",
                }
            )
        run_start = t
        prev = t
    gaps += 1
    if len(samples) < 25:
        samples.append(
            {
                "from": str(run_start),
                "to": str(prev),
                "bars": int(round((prev - run_start) / delta)) + 1,
                "class": "unexpected_missing",
            }
        )
    return gaps, samples


def calendar_quality_stats(
    df: pd.DataFrame,
    *,
    asset_class: str,
    timeframe: str,
    period_start: pd.Timestamp,
    period_end: pd.Timestamp,
) -> CalendarStats:
    """Compute tradable-calendar quality metrics. Does not invent/fill bars."""
    delta = {
        "5M": pd.Timedelta(minutes=5),
        "15M": pd.Timedelta(minutes=15),
        "1H": pd.Timedelta(hours=1),
        "4H": pd.Timedelta(hours=4),
    }[timeframe]

    period_start = _as_utc(period_start)
    period_end = _as_utc(period_end)
    if not df.empty:
        obs_start = max(period_start, _as_utc(df.index[0]))
        obs_end = min(period_end, _as_utc(df.index[-1]))
    else:
        obs_start, obs_end = period_start, period_end

    expected = expected_close_index(obs_start, obs_end, timeframe, asset_class)
    closed = scheduled_closed_index(obs_start, obs_end, timeframe, asset_class)
    observed_index = df.index if df.empty else pd.DatetimeIndex(df.index).tz_convert("UTC")

    observed_in_expected = observed_index.intersection(expected)
    unexpected_missing = expected.difference(observed_index)

    grid = expected.union(closed)
    reason_counts: dict[str, int] = {}
    for t in grid:
        r = classify_bar_close(t, asset_class).value
        reason_counts[r] = reason_counts.get(r, 0) + 1

    gap_count, gap_samples = _count_contiguous_gaps(unexpected_missing, delta)
    n_expected = len(expected)
    n_unexpected = len(unexpected_missing)
    pct = 0.0 if n_expected == 0 else 100.0 * n_unexpected / n_expected

    return CalendarStats(
        expected_trading_bars=n_expected,
        observed_bars=len(df),
        observed_in_expected=len(observed_in_expected),
        scheduled_closed_bars=len(closed),
        unexpected_missing_bars=n_unexpected,
        unexpected_missing_pct=float(pct),
        unexpected_gap_count=gap_count,
        unexpected_gap_samples=gap_samples,
        closed_reason_counts=reason_counts,
    )
