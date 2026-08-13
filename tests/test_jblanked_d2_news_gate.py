"""Tests for locked JBlanked D2 + news blackout rules."""

from __future__ import annotations

import pandas as pd

from trading_signal_bot.data.jblanked_calendar import (
    event_fingerprint,
    normalize_event_row,
    parse_scheduled_date_utc,
    rows_from_api_payload,
)
from trading_signal_bot.data.news_blackout import (
    blackout_set_for_symbol,
    events_for_symbol,
    filter_high_impact,
)


def test_parse_scheduled_date_utc_frozen():
    ts = parse_scheduled_date_utc("2025.10.06 12:00:00")
    assert ts is not None
    assert str(ts.tz) == "UTC"
    assert ts.hour == 12


def test_fingerprint_when_event_id_missing():
    row = {
        "Name": "Baker Hughes US Oil Rig Count",
        "Currency": "USD",
        "Impact": "None",
        "Date": "2025.10.03 20:00:00",
        "Event_ID": 0,
        "Actual": 999,  # must be ignored in normalize
        "Outcome": "x",
        "Strength": "y",
        "Quality": "z",
    }
    norm = normalize_event_row(row)
    assert norm is not None
    assert norm["event_id_source"] == "fingerprint_name_date_currency"
    assert norm["event_id"] == event_fingerprint(
        "Baker Hughes US Oil Rig Count", "2025.10.03 20:00:00", "USD"
    )
    assert "Actual" not in norm
    assert "Outcome" not in norm
    assert "Strength" not in norm
    assert "Quality" not in norm


def test_provider_event_id_kept():
    row = {
        "Name": "Retail Sales m/m",
        "Currency": "EUR",
        "Impact": "High",
        "Date": "2025.10.06 12:00:00",
        "Event_ID": 999030003,
    }
    norm = normalize_event_row(row)
    assert norm is not None
    assert norm["event_id"] == "999030003"
    assert norm["impact"] == "high"


def test_rows_from_api_and_gate_filters():
    payload = [
        {
            "Name": "NFP",
            "Currency": "USD",
            "Impact": "High",
            "Date": "2025.10.03 12:30:00",
            "Event_ID": 1,
        },
        {
            "Name": "Minor",
            "Currency": "USD",
            "Impact": "Low",
            "Date": "2025.10.03 13:00:00",
            "Event_ID": 2,
        },
        {
            "Name": "ECB",
            "Currency": "EUR",
            "Impact": "High",
            "Date": "2025.10.03 14:00:00",
            "Event_ID": 3,
        },
    ]
    df = pd.DataFrame(rows_from_api_payload(payload))
    hi = filter_high_impact(df)
    assert len(hi) == 2
    eurusd = events_for_symbol(df, "EURUSD")
    assert set(eurusd["currency"]) == {"USD", "EUR"}
    xau = events_for_symbol(df, "XAUUSD")
    assert set(xau["currency"]) == {"USD"}
    btc = events_for_symbol(df, "BTCUSDT")
    assert btc.empty


def test_blackout_expansion_utc_window():
    events = pd.DataFrame(rows_from_api_payload([
        {
            "Name": "NFP",
            "Currency": "USD",
            "Impact": "High",
            "Date": "2025.10.03 12:30:00",
            "Event_ID": 1,
        }
    ]))
    # 5M bars around the event
    idx = pd.date_range("2025-10-03 11:00:00", periods=40, freq="5min", tz="UTC")
    blackout = blackout_set_for_symbol(
        events, "EURUSD", idx, window_minutes=30, major_window_minutes=60
    )
    # 60 min each side → 24 bars of 5m + possibly center alignment
    assert len(blackout) > 0
    event_ts = events.iloc[0]["ts_utc"]
    assert event_ts in blackout or any(abs(b - event_ts) <= pd.Timedelta(minutes=5) for b in blackout)


def test_missing_events_mean_no_blackout():
    empty = pd.DataFrame(columns=["ts_utc", "currency", "event", "impact", "event_id"])
    idx = pd.date_range("2025-10-01", periods=10, freq="5min", tz="UTC")
    assert blackout_set_for_symbol(empty, "EURUSD", idx) == set()
