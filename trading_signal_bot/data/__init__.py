"""Core data models and provider interfaces (D1/D2 open)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, Sequence

import numpy as np
import pandas as pd


Timeframe = Literal["5M", "15M", "1H", "4H"]
Direction = Literal["LONG", "SHORT"]
DecisionType = Literal["SIGNAL_LONG", "SIGNAL_SHORT", "NO_TRADE"]
Category = Literal["A+", "A", "B", "NO_TRADE"]
AssetClass = Literal["FX", "XAU", "CRYPTO"]


class DataQualityStatus(str, Enum):
    OK = "OK"
    STALE = "STALE"
    GAP = "GAP"
    INVALID_BAR = "INVALID_BAR"
    MISSING = "MISSING"


@dataclass(frozen=True)
class Bar:
    ts: pd.Timestamp  # close time UTC
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    def __post_init__(self) -> None:
        if self.high < self.low:
            raise ValueError("high < low")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("OHLC inconsistency")


@dataclass
class DataQualityReport:
    status: DataQualityStatus
    reason: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == DataQualityStatus.OK


@dataclass
class OHLCVFrame:
    """Normalized OHLCV indexed by UTC close timestamp."""

    symbol: str
    timeframe: Timeframe
    df: pd.DataFrame  # columns: open,high,low,close,volume

    def __post_init__(self) -> None:
        if not isinstance(self.df.index, pd.DatetimeIndex):
            raise TypeError("df index must be DatetimeIndex")
        if self.df.index.tz is None:
            self.df.index = self.df.index.tz_localize("UTC")
        else:
            self.df.index = self.df.index.tz_convert("UTC")
        self.df = self.df.sort_index()
        required = {"open", "high", "low", "close"}
        missing = required - set(self.df.columns)
        if missing:
            raise ValueError(f"missing columns: {missing}")
        if "volume" not in self.df.columns:
            self.df["volume"] = 0.0

    def up_to(self, ts: pd.Timestamp) -> OHLCVFrame:
        ts = _as_utc(ts)
        return OHLCVFrame(self.symbol, self.timeframe, self.df.loc[self.df.index <= ts].copy())

    def closes(self) -> np.ndarray:
        return self.df["close"].to_numpy(dtype=float)


def _as_utc(ts: pd.Timestamp) -> pd.Timestamp:
    if not isinstance(ts, pd.Timestamp):
        ts = pd.Timestamp(ts)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def validate_ohlcv(df: pd.DataFrame, max_gap: pd.Timedelta | None = None) -> DataQualityReport:
    if df.empty:
        return DataQualityReport(DataQualityStatus.MISSING, "empty frame")
    if not df.index.is_monotonic_increasing:
        return DataQualityReport(DataQualityStatus.INVALID_BAR, "non-monotonic index")
    if df.index.has_duplicates:
        return DataQualityReport(DataQualityStatus.INVALID_BAR, "duplicate timestamps")
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    bad = (h < l) | (h < o) | (h < c) | (l > o) | (l > c) | ~np.isfinite(o) | ~np.isfinite(h)
    if bool(bad.any()):
        return DataQualityReport(
            DataQualityStatus.INVALID_BAR,
            "ohlc inconsistency",
            {"n_bad": int(bad.sum())},
        )
    if max_gap is not None and len(df) > 1:
        deltas = df.index.to_series().diff().iloc[1:]
        if (deltas > max_gap).any():
            return DataQualityReport(DataQualityStatus.GAP, "gap exceeds max_gap")
    return DataQualityReport(DataQualityStatus.OK)


class MarketDataProvider(ABC):
    """D1 open: concrete vendor chosen after quality comparison. READ-ONLY."""

    @abstractmethod
    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: pd.Timestamp | None = None,
        end: pd.Timestamp | None = None,
    ) -> OHLCVFrame:
        raise NotImplementedError


class EconomicCalendarProvider(ABC):
    """D2 open: concrete vendor chosen before paper/live. READ-ONLY."""

    @abstractmethod
    def high_impact_events(
        self,
        start: pd.Timestamp,
        end: pd.Timestamp,
        currencies: Sequence[str] | None = None,
    ) -> pd.DataFrame:
        """Return columns: ts_utc, currency, event, impact."""
        raise NotImplementedError


@dataclass(frozen=True)
class MultiTimeframeBundle:
    symbol: str
    asset_class: AssetClass
    m5: OHLCVFrame
    m15: OHLCVFrame
    h1: OHLCVFrame
    h4: OHLCVFrame

    def quality(self) -> DataQualityReport:
        for frame, gap in (
            (self.m5, pd.Timedelta(minutes=15)),
            (self.m15, pd.Timedelta(minutes=45)),
            (self.h1, pd.Timedelta(hours=3)),
            (self.h4, pd.Timedelta(hours=12)),
        ):
            report = validate_ohlcv(frame.df, max_gap=gap)
            if not report.ok:
                return report
        return DataQualityReport(DataQualityStatus.OK)
