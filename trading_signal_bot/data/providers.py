"""Synthetic / CSV market data for backtests and tests (no live trading)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from trading_signal_bot.data import MarketDataProvider, OHLCVFrame, Timeframe


TF_RULES: dict[str, str] = {
    "5M": "5min",
    "15M": "15min",
    "1H": "1h",
    "4H": "4h",
}


def generate_synthetic_ohlcv(
    symbol: str,
    timeframe: Timeframe,
    n_bars: int,
    start: str | pd.Timestamp = "2024-01-01 00:00:00+00:00",
    seed: int = 42,
    start_price: float = 1.1000,
    volatility: float = 0.0008,
) -> OHLCVFrame:
    """Deterministic random-walk OHLC for unit/integration tests."""
    rng = np.random.default_rng(seed)
    start_ts = pd.Timestamp(start)
    if start_ts.tzinfo is None:
        start_ts = start_ts.tz_localize("UTC")
    idx = pd.date_range(start=start_ts, periods=n_bars, freq=TF_RULES[timeframe], tz="UTC")
    # Use close times as index (end of each bar).
    rets = rng.normal(0.0, volatility, size=n_bars)
    close = start_price * np.cumprod(1.0 + rets)
    open_ = np.roll(close, 1)
    open_[0] = start_price
    wick = np.abs(rng.normal(0.0, volatility * start_price, size=n_bars))
    high = np.maximum(open_, close) + wick
    low = np.minimum(open_, close) - wick
    volume = rng.uniform(100, 1000, size=n_bars)
    df = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )
    return OHLCVFrame(symbol=symbol, timeframe=timeframe, df=df)


def aggregate_ohlcv(base: OHLCVFrame, target: Timeframe) -> OHLCVFrame:
    rule = TF_RULES[target]
    df = base.df
    agg = df.resample(rule, label="right", closed="right").agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }
    )
    agg = agg.dropna()
    return OHLCVFrame(symbol=base.symbol, timeframe=target, df=agg)


def make_mtf_synthetic(
    symbol: str,
    n_5m: int = 2000,
    seed: int = 42,
    start_price: float = 1.10,
    volatility: float = 0.0008,
) -> dict[str, OHLCVFrame]:
    m5 = generate_synthetic_ohlcv(
        symbol, "5M", n_5m, seed=seed, start_price=start_price, volatility=volatility
    )
    return {
        "5M": m5,
        "15M": aggregate_ohlcv(m5, "15M"),
        "1H": aggregate_ohlcv(m5, "1H"),
        "4H": aggregate_ohlcv(m5, "4H"),
    }


class CSVMarketDataProvider(MarketDataProvider):
    """Load OHLCV from local CSV files: {root}/{symbol}_{timeframe}.csv"""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: pd.Timestamp | None = None,
        end: pd.Timestamp | None = None,
    ) -> OHLCVFrame:
        path = self.root / f"{symbol}_{timeframe}.csv"
        df = pd.read_csv(path, parse_dates=["ts"])
        df = df.set_index("ts")
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        if start is not None:
            df = df.loc[df.index >= pd.Timestamp(start, tz="UTC")]
        if end is not None:
            df = df.loc[df.index <= pd.Timestamp(end, tz="UTC")]
        return OHLCVFrame(symbol=symbol, timeframe=timeframe, df=df)
