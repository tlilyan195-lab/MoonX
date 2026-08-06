"""Unit tests for data providers (decode / quality / snapshot) — no live downloads required."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import lzma
import struct

import pandas as pd

from trading_signal_bot.data.dukascopy import decode_bi5, ticks_to_ohlcv
from trading_signal_bot.data.quality import check_fx_dst_pdh, count_ohlc_anomalies, gap_analysis
from trading_signal_bot.data.snapshot import SnapshotBuilder, verify_snapshot_hashes
from trading_signal_bot.data import OHLCVFrame


def _fake_bi5_hour() -> bytes:
    """One synthetic tick compressed as Dukascopy bi5."""
    # ms=1000, ask=110000, bid=109990 (5 decimals → 1.10000 / 1.09990), vols
    rec = struct.pack(">IIIff", 1000, 110000, 109990, 1.0, 1.0)
    return lzma.compress(rec, format=lzma.FORMAT_ALONE)


def test_dukascopy_bi5_decode_mid():
    body = _fake_bi5_hour()
    hour = datetime(2025, 10, 1, 12, tzinfo=timezone.utc)
    ticks = decode_bi5(body, hour, decimals=5)
    assert len(ticks) == 1
    assert abs(ticks.iloc[0]["ask"] - 1.10000) < 1e-9
    assert abs(ticks.iloc[0]["bid"] - 1.09990) < 1e-9
    ohlc = ticks_to_ohlcv(ticks, "5M", "mid")
    assert not ohlc.empty
    mid = (1.10000 + 1.09990) / 2
    assert abs(float(ohlc.iloc[0]["close"]) - mid) < 1e-12


def test_dst_helper_ok():
    out = check_fx_dst_pdh(pd.DataFrame(), pd.Timestamp("2025-11-02 06:00:00+00:00"))
    assert out["ok"] is True


def test_snapshot_hash_roundtrip(tmp_path: Path):
    idx = pd.date_range("2025-10-01", periods=10, freq="5min", tz="UTC")
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
    frame = OHLCVFrame("EURUSD", "5M", df)
    builder = SnapshotBuilder(
        root=tmp_path,
        providers={"FX": "dukascopy_historical_ticks"},
        range_start="2025-10-01",
        range_end="2025-10-02",
    )
    h = builder.add_frame(frame)
    sid, _ = builder.finalize({"EURUSD_5M.parquet": h}, {"ok": True})
    assert sid.startswith("sha256:")
    assert all(verify_snapshot_hashes(tmp_path).values())
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["data_snapshot_id"] == sid


def test_gap_analysis_crypto_detects_hole():
    idx = list(pd.date_range("2025-10-01", periods=20, freq="5min", tz="UTC"))
    # remove some bars in the middle
    idx = idx[:5] + idx[10:]
    df = pd.DataFrame(
        {"open": 1, "high": 1, "low": 1, "close": 1, "volume": 1},
        index=pd.DatetimeIndex(idx),
    )
    n_gaps, details, missing_pct, extras = gap_analysis(df, "5M", "CRYPTO")
    assert n_gaps >= 1
    assert missing_pct > 0
    assert extras["unexpected_missing_bars"] >= 1
