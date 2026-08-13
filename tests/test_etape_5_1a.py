"""ÉTAPE 5.1A — trading calendars, snapshot_id sensitivity, reproducibility."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from trading_signal_bot.config import StrategyConfig
from trading_signal_bot.data import MultiTimeframeBundle, OHLCVFrame
from trading_signal_bot.data.news_coverage import assess_finnhub_historical_coverage
from trading_signal_bot.data.providers import aggregate_ohlcv
from trading_signal_bot.data.quality import gap_analysis
from trading_signal_bot.data.snapshot import SnapshotBuilder, verify_snapshot_hashes
from trading_signal_bot.data.trading_calendars import (
    ClosedReason,
    calendar_quality_stats,
    classify_xau_bar_close,
    dukascopy_settlement_hour_utc,
)
from trading_signal_bot.strategy.engine import evaluate
from tests.fixtures.golden_ohlc import build_golden_long_bundle


def test_xau_daily_break_summer_and_winter():
    # Summer (EDT): 2025-10-06 Monday — break 17:00–18:00 NY = 21:00–22:00 UTC
    assert classify_xau_bar_close(pd.Timestamp("2025-10-06 21:00:00+00:00")) == ClosedReason.TRADABLE
    assert classify_xau_bar_close(pd.Timestamp("2025-10-06 21:05:00+00:00")) == ClosedReason.DAILY_BREAK
    assert classify_xau_bar_close(pd.Timestamp("2025-10-06 22:00:00+00:00")) == ClosedReason.DAILY_BREAK
    assert classify_xau_bar_close(pd.Timestamp("2025-10-06 22:05:00+00:00")) == ClosedReason.TRADABLE
    # Winter (EST): 2025-11-10 Monday — break 17:00–18:00 NY = 22:00–23:00 UTC
    assert dukascopy_settlement_hour_utc(pd.Timestamp("2025-11-10 12:00:00+00:00")) == 22
    assert classify_xau_bar_close(pd.Timestamp("2025-11-10 22:00:00+00:00")) == ClosedReason.TRADABLE
    assert classify_xau_bar_close(pd.Timestamp("2025-11-10 22:05:00+00:00")) == ClosedReason.DAILY_BREAK
    assert classify_xau_bar_close(pd.Timestamp("2025-11-10 23:00:00+00:00")) == ClosedReason.DAILY_BREAK
    assert classify_xau_bar_close(pd.Timestamp("2025-11-10 23:05:00+00:00")) == ClosedReason.TRADABLE


def test_xau_weekend_and_christmas_full_holiday():
    assert classify_xau_bar_close(pd.Timestamp("2025-11-08 12:00:00+00:00")) == ClosedReason.WEEKEND
    assert classify_xau_bar_close(pd.Timestamp("2025-12-25 12:00:00+00:00")) == ClosedReason.FULL_HOLIDAY


def test_gap_analysis_excludes_scheduled_xau_break():
    # Build Mon with intentional daily-break hole only
    idx = list(pd.date_range("2025-10-06 00:00", "2025-10-06 21:00", freq="5min", tz="UTC"))
    idx += list(pd.date_range("2025-10-06 22:05", "2025-10-06 23:55", freq="5min", tz="UTC"))
    df = pd.DataFrame(
        {"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0},
        index=pd.DatetimeIndex(idx),
    )
    n_gaps, details, missing_pct, extras = gap_analysis(df, "5M", "XAU")
    assert extras["unexpected_missing_bars"] == 0
    assert missing_pct == 0.0
    assert n_gaps == 0


def test_gap_analysis_crypto_detects_hole():
    idx = list(pd.date_range("2025-10-01", periods=20, freq="5min", tz="UTC"))
    idx = idx[:5] + idx[10:]
    df = pd.DataFrame(
        {"open": 1, "high": 1, "low": 1, "close": 1, "volume": 1},
        index=pd.DatetimeIndex(idx),
    )
    n_gaps, details, missing_pct, extras = gap_analysis(df, "5M", "CRYPTO")
    assert n_gaps >= 1
    assert missing_pct > 0
    assert extras["unexpected_missing_bars"] >= 1


def test_calendar_stats_fields_present():
    idx = pd.date_range("2025-10-06", periods=50, freq="5min", tz="UTC")
    df = pd.DataFrame(
        {"open": 1, "high": 1, "low": 1, "close": 1, "volume": 1},
        index=idx,
    )
    stats = calendar_quality_stats(
        df,
        asset_class="FX",
        timeframe="5M",
        period_start=idx[0],
        period_end=idx[-1],
    )
    assert stats.expected_trading_bars > 0
    assert stats.observed_bars == 50


def _mini_frame(symbol: str = "EURUSD") -> OHLCVFrame:
    idx = pd.date_range("2025-10-01", periods=12, freq="5min", tz="UTC")
    df = pd.DataFrame(
        {
            "open": 1.0,
            "high": 1.1,
            "low": 0.9,
            "close": 1.05,
            "volume": 1.0,
        },
        index=idx,
    )
    return OHLCVFrame(symbol, "5M", df)


def test_snapshot_id_changes_when_data_file_changes(tmp_path: Path):
    frame = _mini_frame()
    b1 = SnapshotBuilder(
        root=tmp_path / "a",
        providers={"EURUSD": "dukascopy_historical_ticks"},
        range_start="2025-10-01",
        range_end="2025-10-02",
        bar_timestamp="close",
        aggregation={"FX_XAU": "from_5M_right_closed_right"},
        git_commit="abc",
    )
    h1 = b1.add_frame(frame)
    sid1, _ = b1.finalize({"EURUSD_5M.parquet": h1}, {"ok": True})

    # Mutate file content
    frame2 = _mini_frame()
    frame2.df.iloc[0, frame2.df.columns.get_loc("close")] = 1.99
    b2 = SnapshotBuilder(
        root=tmp_path / "b",
        providers={"EURUSD": "dukascopy_historical_ticks"},
        range_start="2025-10-01",
        range_end="2025-10-02",
        bar_timestamp="close",
        aggregation={"FX_XAU": "from_5M_right_closed_right"},
        git_commit="abc",
    )
    h2 = b2.add_frame(frame2)
    sid2, _ = b2.finalize({"EURUSD_5M.parquet": h2}, {"ok": True})
    assert sid1 != sid2
    assert h1 != h2


def test_snapshot_id_changes_when_period_provider_timestamp_agg_change(tmp_path: Path):
    frame = _mini_frame()
    base_kwargs = dict(
        providers={"EURUSD": "dukascopy_historical_ticks"},
        range_start="2025-10-01",
        range_end="2025-10-02",
        bar_timestamp="close",
        aggregation={"FX_XAU": "from_5M_right_closed_right"},
        git_commit="abc",
    )
    ids = []
    variants = [
        {},
        {"range_end": "2025-10-03"},
        {"providers": {"EURUSD": "other_provider"}},
        {"bar_timestamp": "open"},
        {"aggregation": {"FX_XAU": "different_rule"}},
    ]
    for i, override in enumerate(variants):
        kw = {**base_kwargs, **override}
        b = SnapshotBuilder(root=tmp_path / f"v{i}", **kw)
        h = b.add_frame(frame)
        sid, _ = b.finalize({"EURUSD_5M.parquet": h}, {"ok": True})
        ids.append(sid)
    # First is baseline; each subsequent single-field change must differ from baseline
    baseline = ids[0]
    for sid in ids[1:]:
        assert sid != baseline


def test_reproducibility_same_snapshot_config_commit_same_engine_outputs():
    bundle, ts, cfg = build_golden_long_bundle()
    a = evaluate(bundle, ts, cfg)
    b = evaluate(bundle, ts, cfg)
    assert a.decision == b.decision
    assert a.category == b.category
    assert a.setup_score == b.setup_score
    assert a.entry == b.entry and a.sl == b.sl and a.tp1 == b.tp1
    assert a.poi_id == b.poi_id
    # Config identity + commit tag in a frozen payload must match
    payload_a = {
        "decision": a.decision,
        "entry": a.entry,
        "sl": a.sl,
        "tp1": a.tp1,
        "poi_id": a.poi_id,
        "category": a.category,
        "setup_score": a.setup_score,
        "rr_min": cfg.rr_min,
        "commit": "fixed-commit-for-test",
        "snapshot_id": "sha256:deadbeef",
    }
    payload_b = {
        "decision": b.decision,
        "entry": b.entry,
        "sl": b.sl,
        "tp1": b.tp1,
        "poi_id": b.poi_id,
        "category": b.category,
        "setup_score": b.setup_score,
        "rr_min": cfg.rr_min,
        "commit": "fixed-commit-for-test",
        "snapshot_id": "sha256:deadbeef",
    }
    assert payload_a == payload_b


def test_finnhub_rejected_without_relying_only_on_missing_key():
    v = assess_finnhub_historical_coverage(api_key="")
    assert v.key_present is False
    assert v.usable_for_3y_blackout is False
    assert v.verdict == "REJECT"
    assert v.alternative is not None
    assert v.alternative["provider"] == "trading_economics_calendar_api"
    # Must cite pricing/coverage, not only missing key
    blob = " ".join(v.reasons).lower()
    assert "free" in blob or "pricing" in blob or "all-in-one" in blob
