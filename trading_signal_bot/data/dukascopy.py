"""
Dukascopy historical tick datafeed — no API key.

URL: https://datafeed.dukascopy.com/datafeed/{SYM}/{YYYY}/{MM0}/{DD}/{HH}h_ticks.bi5
Month is ZERO-BASED. Ticks are LZMA-compressed 20-byte big-endian records.
OHLC bars use MID = (bid+ask)/2 unless price_side overridden.
"""

from __future__ import annotations

import lzma
import struct
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd
import requests

from trading_signal_bot.data import MarketDataProvider, OHLCVFrame, Timeframe
from trading_signal_bot.data.providers import TF_RULES, aggregate_ohlcv


BASE = "https://datafeed.dukascopy.com/datafeed"

# Point scale: price = raw_int / 10**decimals
DECIMALS: dict[str, int] = {
    "EURUSD": 5,
    "GBPUSD": 5,
    "USDJPY": 3,
    "XAUUSD": 3,
}

PriceSide = Literal["mid", "bid", "ask"]


def _utc(ts: pd.Timestamp) -> pd.Timestamp:
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        return t.tz_localize("UTC")
    return t.tz_convert("UTC")


def hour_url(symbol: str, hour_start: datetime) -> str:
    # MONTH ZERO-BASED
    return (
        f"{BASE}/{symbol}/{hour_start.year:04d}/{hour_start.month - 1:02d}/"
        f"{hour_start.day:02d}/{hour_start.hour:02d}h_ticks.bi5"
    )


def decode_bi5(body: bytes, hour_start: datetime, decimals: int) -> pd.DataFrame:
    """Decode one hour of ticks → DataFrame[ts, bid, ask, bid_vol, ask_vol]."""
    if not body:
        return pd.DataFrame(columns=["ts", "bid", "ask", "bid_vol", "ask_vol"])
    try:
        raw = lzma.decompress(body, format=lzma.FORMAT_AUTO)
    except lzma.LZMAError:
        return pd.DataFrame(columns=["ts", "bid", "ask", "bid_vol", "ask_vol"])
    n = len(raw) // 20
    if n == 0:
        return pd.DataFrame(columns=["ts", "bid", "ask", "bid_vol", "ask_vol"])
    raw = raw[: n * 20]
    scale = 10.0**decimals
    base_ms = int(hour_start.replace(tzinfo=timezone.utc).timestamp() * 1000)
    records = []
    for i in range(n):
        chunk = raw[i * 20 : (i + 1) * 20]
        ms, ask_i, bid_i, ask_vol, bid_vol = struct.unpack(">IIIff", chunk)
        records.append(
            {
                "ts": pd.Timestamp(base_ms + ms, unit="ms", tz="UTC"),
                "ask": ask_i / scale,
                "bid": bid_i / scale,
                "ask_vol": float(ask_vol),
                "bid_vol": float(bid_vol),
            }
        )
    return pd.DataFrame.from_records(records)


def ticks_to_ohlcv(
    ticks: pd.DataFrame,
    timeframe: Timeframe,
    price_side: PriceSide = "mid",
) -> pd.DataFrame:
    if ticks.empty:
        df = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        df.index = pd.DatetimeIndex([], tz="UTC", name="ts")
        return df
    if price_side == "mid":
        px = (ticks["bid"] + ticks["ask"]) / 2.0
    elif price_side == "bid":
        px = ticks["bid"]
    else:
        px = ticks["ask"]
    vol = ticks["bid_vol"].fillna(0) + ticks["ask_vol"].fillna(0)
    tmp = pd.DataFrame({"price": px.to_numpy(), "volume": vol.to_numpy()}, index=ticks["ts"])
    tmp = tmp.sort_index()
    rule = TF_RULES[timeframe]
    # close-time labeled bars
    ohlc = tmp["price"].resample(rule, label="right", closed="right").ohlc()
    volume = tmp["volume"].resample(rule, label="right", closed="right").sum()
    out = ohlc.join(volume.rename("volume")).dropna(subset=["open", "high", "low", "close"])
    out.index.name = "ts"
    return out


class DukascopyHistoricalProvider(MarketDataProvider):
    """
    Historical ticks → OHLC. No credentials.
    Convention V1: MID price = (bid+ask)/2 for OHLC; spread retained in tick cache optionally.
    """

    def __init__(
        self,
        price_side: PriceSide = "mid",
        max_workers: int = 4,
        pause_s: float = 0.05,
        session: requests.Session | None = None,
        prefer_aggregate_from_5m: bool = True,
        cache_dir: str | Path | None = "data/cache/dukascopy_bi5",
        skip_fx_weekend: bool = True,
    ) -> None:
        from pathlib import Path as _Path

        self.price_side = price_side
        self.max_workers = max_workers
        self.pause_s = pause_s
        self.session = session or requests.Session()
        self.session.headers.setdefault(
            "User-Agent",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )
        self.session.headers.setdefault("Referer", "https://www.dukascopy.com/")
        self.session.headers.setdefault("Accept", "*/*")
        self.prefer_aggregate_from_5m = prefer_aggregate_from_5m
        self.provider_name = "dukascopy_historical_ticks"
        self.last_fetch_meta: dict = {}
        self.cache_dir = _Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.skip_fx_weekend = skip_fx_weekend

    @staticmethod
    def _is_fx_weekend_hour(hour_start: datetime) -> bool:
        """FX typically closed Fri ~21:00 UTC → Sun ~21:00 UTC."""
        wd = hour_start.weekday()  # Mon=0 ... Sun=6
        h = hour_start.hour
        if wd == 5:  # Saturday
            return True
        if wd == 6 and h < 21:  # Sunday before open
            return True
        if wd == 4 and h >= 21:  # Friday after close
            return True
        return False

    def _cache_path(self, symbol: str, hour_start: datetime):
        assert self.cache_dir is not None
        return (
            self.cache_dir
            / symbol
            / f"{hour_start.year:04d}{hour_start.month:02d}{hour_start.day:02d}{hour_start.hour:02d}.bi5"
        )

    def _fetch_hour(self, symbol: str, hour_start: datetime) -> tuple[datetime, bytes, str]:
        if self.cache_dir is not None:
            path = self._cache_path(symbol, hour_start)
            if path.exists():
                body = path.read_bytes()
                return hour_start, body, "cache" if body else "cache_empty"

        url = hour_url(symbol, hour_start)
        for attempt in range(8):
            try:
                r = self.session.get(url, timeout=90)
                if r.status_code == 404:
                    body = b""
                    status = "missing"
                elif r.status_code in (429, 503, 502, 504):
                    time.sleep(2.0 * (attempt + 1))
                    continue
                else:
                    r.raise_for_status()
                    body = r.content
                    status = "ok" if body else "empty"
                if self.cache_dir is not None:
                    path = self._cache_path(symbol, hour_start)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(body)
                return hour_start, body, status
            except (requests.RequestException, ConnectionError, OSError) as exc:
                if attempt == 7:
                    return hour_start, b"", f"error:{type(exc).__name__}:{exc}"
                time.sleep(1.5 * (attempt + 1))
        return hour_start, b"", "error:retries_exhausted"

    def download_ticks(
        self,
        symbol: str,
        start: pd.Timestamp,
        end: pd.Timestamp,
    ) -> tuple[pd.DataFrame, dict]:
        if symbol not in DECIMALS:
            raise ValueError(f"Unsupported Dukascopy symbol: {symbol}")
        start = _utc(start).floor("h")
        end = _utc(end).ceil("h")
        hours: list[datetime] = []
        skipped_weekend = 0
        cur = start.to_pydatetime().replace(tzinfo=timezone.utc)
        end_dt = end.to_pydatetime().replace(tzinfo=timezone.utc)
        while cur < end_dt:
            if self.skip_fx_weekend and self._is_fx_weekend_hour(cur):
                skipped_weekend += 1
            else:
                hours.append(cur)
            cur += timedelta(hours=1)

        status_counts: dict[str, int] = {}
        frames: list[pd.DataFrame] = []
        decimals = DECIMALS[symbol]

        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futs = [pool.submit(self._fetch_hour, symbol, h) for h in hours]
            for fut in as_completed(futs):
                hour_start, body, status = fut.result()
                status_counts[status] = status_counts.get(status, 0) + 1
                if body:
                    df_h = decode_bi5(body, hour_start, decimals)
                    if not df_h.empty:
                        frames.append(df_h)
                if self.pause_s > 0 and status not in ("cache", "cache_empty"):
                    time.sleep(self.pause_s)

        if not frames:
            ticks = pd.DataFrame(columns=["ts", "bid", "ask", "bid_vol", "ask_vol"])
        else:
            ticks = pd.concat(frames, ignore_index=True).sort_values("ts")
            ticks = ticks.drop_duplicates(subset=["ts"], keep="last")
        meta = {
            "hours_requested": len(hours),
            "hours_skipped_weekend": skipped_weekend,
            "hour_status": status_counts,
            "n_ticks": int(len(ticks)),
            "price_side": self.price_side,
            "decimals": decimals,
            "symbol": symbol,
            "cache_dir": str(self.cache_dir) if self.cache_dir else None,
        }
        return ticks, meta

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: pd.Timestamp | None = None,
        end: pd.Timestamp | None = None,
    ) -> OHLCVFrame:
        if start is None or end is None:
            raise ValueError("start and end required for Dukascopy fetch")
        # Always build from ticks → 5M then aggregate higher TF for consistency
        ticks, meta = self.download_ticks(symbol, start, end)
        self.last_fetch_meta = meta
        df5 = ticks_to_ohlcv(ticks, "5M", self.price_side)
        frame5 = OHLCVFrame(symbol=symbol, timeframe="5M", df=df5)
        if timeframe == "5M":
            # clip to range by close time
            df = frame5.df
            df = df.loc[(df.index >= _utc(start)) & (df.index <= _utc(end))]
            return OHLCVFrame(symbol=symbol, timeframe="5M", df=df)
        if self.prefer_aggregate_from_5m:
            agg = aggregate_ohlcv(frame5, timeframe)
            df = agg.df
            df = df.loc[(df.index >= _utc(start)) & (df.index <= _utc(end))]
            return OHLCVFrame(symbol=symbol, timeframe=timeframe, df=df)
        # Direct resample to target (same ticks)
        df = ticks_to_ohlcv(ticks, timeframe, self.price_side)
        df = df.loc[(df.index >= _utc(start)) & (df.index <= _utc(end))]
        return OHLCVFrame(symbol=symbol, timeframe=timeframe, df=df)
