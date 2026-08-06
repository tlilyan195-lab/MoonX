"""Data quality gates, gap analysis, DST checks, aggregation comparison."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from trading_signal_bot.data import OHLCVFrame, Timeframe, validate_ohlcv
from trading_signal_bot.data.providers import aggregate_ohlcv
from trading_signal_bot.data.sessions import fx_trading_day_id, previous_fx_trading_day_bounds
from trading_signal_bot.data.trading_calendars import calendar_quality_stats


TF_EXPECTED_DELTA = {
    "5M": pd.Timedelta(minutes=5),
    "15M": pd.Timedelta(minutes=15),
    "1H": pd.Timedelta(hours=1),
    "4H": pd.Timedelta(hours=4),
}


@dataclass
class SymbolQualityReport:
    symbol: str
    asset_class: str
    provider: str
    timeframe: str
    period_start: str
    period_end: str
    n_bars: int
    missing_pct_estimate: float
    n_gaps: int
    gap_details: list[dict[str, Any]] = field(default_factory=list)
    n_ohlc_anomalies: int = 0
    ohlc_anomaly_samples: list[str] = field(default_factory=list)
    utc_close_time_ok: bool = True
    dst_check: dict[str, Any] = field(default_factory=dict)
    aggregation_compare: dict[str, Any] = field(default_factory=dict)
    rejected: bool = False
    reject_reason: str = ""
    extras: dict[str, Any] = field(default_factory=dict)
    expected_trading_bars: int = 0
    scheduled_closed_bars: int = 0
    unexpected_missing_bars: int = 0
    unexpected_missing_pct: float = 0.0
    unexpected_gap_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "asset_class": self.asset_class,
            "provider": self.provider,
            "timeframe": self.timeframe,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "n_bars": self.n_bars,
            "missing_pct_estimate": self.missing_pct_estimate,
            "n_gaps": self.n_gaps,
            "gap_details": self.gap_details[:20],
            "n_ohlc_anomalies": self.n_ohlc_anomalies,
            "ohlc_anomaly_samples": self.ohlc_anomaly_samples[:10],
            "utc_close_time_ok": self.utc_close_time_ok,
            "dst_check": self.dst_check,
            "aggregation_compare": self.aggregation_compare,
            "rejected": self.rejected,
            "reject_reason": self.reject_reason,
            "extras": self.extras,
            "expected_trading_bars": self.expected_trading_bars,
            "observed_bars": self.n_bars,
            "scheduled_closed_bars": self.scheduled_closed_bars,
            "unexpected_missing_bars": self.unexpected_missing_bars,
            "unexpected_missing_pct": self.unexpected_missing_pct,
            "unexpected_gap_count": self.unexpected_gap_count,
        }


def count_ohlc_anomalies(df: pd.DataFrame) -> tuple[int, list[str]]:
    if df.empty:
        return 0, []
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    bad = (h < l) | (h < o) | (h < c) | (l > o) | (l > c) | ~np.isfinite(o) | ~np.isfinite(h)
    idxs = list(df.index[bad][:10])
    return int(bad.sum()), [str(i) for i in idxs]


def gap_analysis(
    df: pd.DataFrame,
    timeframe: Timeframe,
    asset_class: str,
    period_start: pd.Timestamp | None = None,
    period_end: pd.Timestamp | None = None,
) -> tuple[int, list[dict[str, Any]], float, dict[str, Any]]:
    """
    Calendar-aware gap analysis (no interpolation).

    missing_pct / gap counts use only bars expected while the instrument is
    tradable (Dukascopy FX/XAU calendars; crypto 24/7). Scheduled weekend,
    daily-break, and holiday closures are excluded from the denominator.
    """
    if df.empty:
        return 0, [], 100.0, {}
    if period_start is None:
        period_start = df.index[0]
    if period_end is None:
        period_end = df.index[-1]
    stats = calendar_quality_stats(
        df,
        asset_class=asset_class,
        timeframe=timeframe,
        period_start=period_start,
        period_end=period_end,
    )
    extras = {
        "expected_trading_bars": stats.expected_trading_bars,
        "observed_bars": stats.observed_bars,
        "observed_in_expected": stats.observed_in_expected,
        "scheduled_closed_bars": stats.scheduled_closed_bars,
        "unexpected_missing_bars": stats.unexpected_missing_bars,
        "unexpected_missing_pct": stats.unexpected_missing_pct,
        "unexpected_gap_count": stats.unexpected_gap_count,
        "closed_reason_counts": stats.closed_reason_counts,
    }
    return (
        stats.unexpected_gap_count,
        stats.unexpected_gap_samples,
        float(stats.unexpected_missing_pct),
        extras,
    )


def check_utc_close_index(df: pd.DataFrame, timeframe: Timeframe) -> bool:
    if df.empty:
        return False
    if df.index.tz is None:
        return False
    # close-time alignment: 5M closes on :00/:05/... 
    minutes = df.index.minute.to_numpy()
    if timeframe == "5M":
        return bool(np.all(minutes % 5 == 0))
    if timeframe == "15M":
        return bool(np.all(minutes % 15 == 0))
    if timeframe == "1H":
        return bool(np.all(minutes == 0))
    if timeframe == "4H":
        return bool(np.all(np.isin(df.index.hour % 4, [0])) or np.all(minutes == 0))
    return True


def check_fx_dst_pdh(df_5m: pd.DataFrame, around: pd.Timestamp) -> dict[str, Any]:
    """
    Verify PDH day-id changes correctly around a US DST transition sample.
    Uses America/New_York via fx_trading_day_id.
    """
    around = pd.Timestamp(around)
    if around.tzinfo is None:
        around = around.tz_localize("UTC")
    else:
        around = around.tz_convert("UTC")
    samples = [
        around - pd.Timedelta(hours=12),
        around,
        around + pd.Timedelta(hours=12),
    ]
    day_ids = [str(fx_trading_day_id(t)) for t in samples]
    # Bounds for previous day must be contiguous 24h NY trading day labels
    b0 = previous_fx_trading_day_bounds(around)
    ok = b0[1] > b0[0]
    return {
        "around_utc": str(around),
        "day_ids": day_ids,
        "prev_day_bounds": [str(b0[0]), str(b0[1])],
        "ok": ok and len(set(day_ids)) >= 1,
        "note": "PDH uses America/New_York 17:00 boundary (EST/EDT).",
    }


def compare_native_vs_aggregated(
    frame_5m: OHLCVFrame,
    native: OHLCVFrame | None,
    target: Timeframe,
    tol_rel: float = 1e-6,
) -> dict[str, Any]:
    agg = aggregate_ohlcv(frame_5m, target)
    if native is None or native.df.empty:
        return {
            "target": target,
            "native_available": False,
            "agg_bars": len(agg.df),
            "max_close_rel_diff": None,
        }
    joined = agg.df.join(native.df, how="inner", lsuffix="_agg", rsuffix="_nat")
    if joined.empty:
        return {
            "target": target,
            "native_available": True,
            "overlap_bars": 0,
            "max_close_rel_diff": None,
            "warning": "no overlapping timestamps",
        }
    close_agg = joined["close_agg"]
    close_nat = joined["close_nat"]
    rel = ((close_agg - close_nat).abs() / close_nat.replace(0, np.nan)).dropna()
    max_diff = float(rel.max()) if len(rel) else None
    return {
        "target": target,
        "native_available": True,
        "overlap_bars": int(len(joined)),
        "max_close_rel_diff": max_diff,
        "within_tol": bool(max_diff is not None and max_diff <= tol_rel),
    }


def evaluate_symbol_frame(
    frame: OHLCVFrame,
    *,
    asset_class: str,
    provider: str,
    period_start: pd.Timestamp,
    period_end: pd.Timestamp,
    frame_5m: OHLCVFrame | None = None,
    native_higher: dict[str, OHLCVFrame] | None = None,
    reject_missing_pct_above: float = 40.0,
) -> SymbolQualityReport:
    df = frame.df
    n_anom, anom_samples = count_ohlc_anomalies(df)
    n_gaps, gap_details, missing_pct, cal_extras = gap_analysis(
        df,
        frame.timeframe,
        asset_class,
        period_start=period_start,
        period_end=period_end,
    )
    utc_ok = check_utc_close_index(df, frame.timeframe)
    basic = validate_ohlcv(df)
    dst = {}
    if asset_class in ("FX", "XAU") and frame.timeframe == "5M" and not df.empty:
        # US DST fall-back 2025-11-02 06:00 UTC approx (01:00 EST after fall)
        dst = check_fx_dst_pdh(df, pd.Timestamp("2025-11-02 06:00:00+00:00"))

    agg_cmp: dict[str, Any] = {}
    if frame_5m is not None and frame.timeframe == "5M":
        for tf in ("15M", "1H", "4H"):
            native = (native_higher or {}).get(tf)
            agg_cmp[tf] = compare_native_vs_aggregated(frame_5m, native, tf)  # type: ignore[arg-type]

    unexpected_pct = float(cal_extras.get("unexpected_missing_pct", missing_pct))
    rejected = False
    reason = ""
    if df.empty:
        rejected, reason = True, "empty_frame"
    elif n_anom > 0:
        rejected, reason = True, f"ohlc_anomalies={n_anom}"
    elif not utc_ok:
        rejected, reason = True, "utc_close_time_alignment_failed"
    elif not basic.ok and basic.status.value != "GAP":
        # GAP alone is reported; severe invalid rejects
        if basic.status.value == "INVALID_BAR":
            rejected, reason = True, basic.reason
    elif asset_class == "XAU" and unexpected_pct > reject_missing_pct_above:
        rejected, reason = True, f"xau_unexpected_missing_pct={unexpected_pct:.1f}>threshold"

    return SymbolQualityReport(
        symbol=frame.symbol,
        asset_class=asset_class,
        provider=provider,
        timeframe=frame.timeframe,
        period_start=str(period_start),
        period_end=str(period_end),
        n_bars=len(df),
        missing_pct_estimate=missing_pct,
        n_gaps=n_gaps,
        gap_details=gap_details,
        n_ohlc_anomalies=n_anom,
        ohlc_anomaly_samples=anom_samples,
        utc_close_time_ok=utc_ok,
        dst_check=dst,
        aggregation_compare=agg_cmp,
        rejected=rejected,
        reject_reason=reason,
        extras={
            "basic_status": basic.status.value,
            "basic_reason": basic.reason,
            **cal_extras,
        },
        expected_trading_bars=int(cal_extras.get("expected_trading_bars", 0)),
        scheduled_closed_bars=int(cal_extras.get("scheduled_closed_bars", 0)),
        unexpected_missing_bars=int(cal_extras.get("unexpected_missing_bars", 0)),
        unexpected_missing_pct=unexpected_pct,
        unexpected_gap_count=int(cal_extras.get("unexpected_gap_count", n_gaps)),
    )
