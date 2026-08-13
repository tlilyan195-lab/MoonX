"""Synthetic / CSV market data for backtests and tests (no live trading)."""

from __future__ import annotations

from pathlib import Path
import zlib

import numpy as np
import pandas as pd

from trading_signal_bot.data import MarketDataProvider, OHLCVFrame, Timeframe


TF_RULES: dict[str, str] = {
    "5M": "5min",
    "15M": "15min",
    "1H": "1h",
    "4H": "4h",
}


def _effective_seed(symbol: str, seed: int) -> int:
    """Mix symbol into seed so identical base seeds still yield independent series."""
    return int(seed) ^ (zlib.adler32(symbol.encode("utf-8")) & 0xFFFFFFFF)


def _asset_defaults(symbol: str) -> tuple[float, float]:
    """Return (start_price, volatility) defaults by asset class."""
    s = symbol.upper()
    if s.endswith("USDT") or (
        s.endswith("USD") and any(x in s for x in ("BTC", "ETH", "SOL"))
    ):
        if "BTC" in s:
            return 40000.0, 0.002
        if "ETH" in s:
            return 3000.0, 0.002
        return 100.0, 0.002
    if s.startswith("XAU"):
        return 2000.0, 0.0012
    return 1.1000, 0.0008


def generate_synthetic_ohlcv(
    symbol: str,
    timeframe: Timeframe,
    n_bars: int,
    start: str | pd.Timestamp = "2024-01-01 00:00:00+00:00",
    seed: int = 42,
    start_price: float | None = None,
    volatility: float | None = None,
) -> OHLCVFrame:
    """Deterministic random-walk OHLC for unit/integration tests.

    Series are independent per ``symbol`` even when the same base ``seed`` is used.
    """
    default_sp, default_vol = _asset_defaults(symbol)
    sp = default_sp if start_price is None else float(start_price)
    vol = default_vol if volatility is None else float(volatility)
    rng = np.random.default_rng(_effective_seed(symbol, seed))
    start_ts = pd.Timestamp(start)
    if start_ts.tzinfo is None:
        start_ts = start_ts.tz_localize("UTC")
    idx = pd.date_range(start=start_ts, periods=n_bars, freq=TF_RULES[timeframe], tz="UTC")
    # Use close times as index (end of each bar).
    rets = rng.normal(0.0, vol, size=n_bars)
    close = sp * np.cumprod(1.0 + rets)
    open_ = np.roll(close, 1)
    open_[0] = sp
    wick = np.abs(rng.normal(0.0, vol * sp, size=n_bars))
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
    start_price: float | None = None,
    volatility: float | None = None,
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
