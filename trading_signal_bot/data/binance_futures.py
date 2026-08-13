"""Binance USD-M Futures OHLC — public, no trading permissions.

Primary live: /fapi/v1/klines (may be geo-restricted).
Fallback historical: https://data.binance.vision/data/futures/um/daily/klines/...
"""

from __future__ import annotations

import io
import time
import zipfile
from datetime import timedelta
from typing import Any

import pandas as pd
import requests

from trading_signal_bot.data import MarketDataProvider, OHLCVFrame, Timeframe


BINANCE_FAPI = "https://fapi.binance.com"
BINANCE_VISION = "https://data.binance.vision"
INTERVAL_MAP: dict[str, str] = {
    "5M": "5m",
    "15M": "15m",
    "1H": "1h",
    "4H": "4h",
}
_LIMIT = 1500


def _as_utc_ts(ts: pd.Timestamp) -> pd.Timestamp:
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        return t.tz_localize("UTC")
    return t.tz_convert("UTC")


INTERVAL_MS: dict[str, int] = {
    "5M": 5 * 60 * 1000,
    "15M": 15 * 60 * 1000,
    "1H": 60 * 60 * 1000,
    "4H": 4 * 60 * 60 * 1000,
}


class BinanceFuturesPublicProvider(MarketDataProvider):
    """READ-ONLY USD-M futures klines. No order endpoints. No API key."""

    def __init__(
        self,
        base_url: str = BINANCE_FAPI,
        vision_url: str = BINANCE_VISION,
        session: requests.Session | None = None,
        pause_s: float = 0.05,
        prefer_vision_historical: bool = True,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.vision_url = vision_url.rstrip("/")
        self.session = session or requests.Session()
        self.session.headers.setdefault(
            "User-Agent", "Mozilla/5.0 (compatible; MoonXSignalBot/1.0; research)"
        )
        self.pause_s = pause_s
        self.prefer_vision_historical = prefer_vision_historical
        self.provider_name = "binance_usdm_futures_public"
        self.last_source: str = ""

    def _fapi_available(self) -> bool:
        try:
            r = self.session.get(f"{self.base_url}/fapi/v1/ping", timeout=10)
            return r.status_code == 200
        except requests.RequestException:
            return False

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: pd.Timestamp | None = None,
        end: pd.Timestamp | None = None,
    ) -> OHLCVFrame:
        if symbol not in ("BTCUSDT", "ETHUSDT"):
            raise ValueError(f"Unsupported Binance futures symbol for V1: {symbol}")
        if start is None or end is None:
            raise ValueError("start and end required")
        start = _as_utc_ts(start)
        end = _as_utc_ts(end)

        if self.prefer_vision_historical or not self._fapi_available():
            frame = self._fetch_vision_daily_zips(symbol, timeframe, start, end)
            self.last_source = "data.binance.vision/futures/um/daily/klines"
            return frame
        try:
            frame = self._fetch_fapi(symbol, timeframe, start, end)
            self.last_source = "fapi.binance.com/fapi/v1/klines"
            return frame
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code in (403, 451):
                frame = self._fetch_vision_daily_zips(symbol, timeframe, start, end)
                self.last_source = "data.binance.vision/futures/um/daily/klines (fapi geo-fallback)"
                return frame
            raise

    def _fetch_fapi(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: pd.Timestamp,
        end: pd.Timestamp,
    ) -> OHLCVFrame:
        interval = INTERVAL_MAP[timeframe]
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        rows: list[list[Any]] = []
        cursor = start_ms
        while cursor < end_ms:
            params = {
                "symbol": symbol,
                "interval": interval,
                "startTime": cursor,
                "endTime": end_ms,
                "limit": _LIMIT,
            }
            r = self.session.get(f"{self.base_url}/fapi/v1/klines", params=params, timeout=30)
            r.raise_for_status()
            batch = r.json()
            if not batch:
                break
            rows.extend(batch)
            last_open = int(batch[-1][0])
            next_cursor = last_open + 1
            if next_cursor <= cursor:
                break
            cursor = next_cursor
            if len(batch) < _LIMIT:
                break
            time.sleep(self.pause_s)
        return self._rows_to_frame(symbol, timeframe, rows, start_ms, end_ms)

    def _fetch_vision_daily_zips(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: pd.Timestamp,
        end: pd.Timestamp,
    ) -> OHLCVFrame:
        interval = INTERVAL_MAP[timeframe]
        rows: list[list[Any]] = []
        day = start.normalize()
        end_day = end.normalize()
        while day <= end_day:
            day_str = day.strftime("%Y-%m-%d")
            # BTCUSDT-5m-2025-10-01.zip
            path = (
                f"/data/futures/um/daily/klines/{symbol}/{interval}/"
                f"{symbol}-{interval}-{day_str}.zip"
            )
            url = self.vision_url + path
            r = self.session.get(url, timeout=60)
            if r.status_code == 404:
                day += timedelta(days=1)
                continue
            r.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
                names = zf.namelist()
                if not names:
                    day += timedelta(days=1)
                    continue
                with zf.open(names[0]) as fh:
                    text = fh.read().decode("utf-8", errors="replace")
            for line in text.splitlines():
                if not line.strip() or line.startswith("open_time"):
                    continue
                parts = line.split(",")
                if len(parts) < 7:
                    continue
                # open_time, open, high, low, close, volume, close_time, ...
                rows.append(
                    [
                        int(float(parts[0])),
                        parts[1],
                        parts[2],
                        parts[3],
                        parts[4],
                        parts[5],
                        int(float(parts[6])),
                    ]
                )
            time.sleep(self.pause_s)
            day += timedelta(days=1)
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        return self._rows_to_frame(symbol, timeframe, rows, start_ms, end_ms)

    @staticmethod
    def _rows_to_frame(
        symbol: str,
        timeframe: Timeframe,
        rows: list[list[Any]],
        start_ms: int,
        end_ms: int,
    ) -> OHLCVFrame:
        if not rows:
            df = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
            df.index = pd.DatetimeIndex([], tz="UTC", name="ts")
            return OHLCVFrame(symbol=symbol, timeframe=timeframe, df=df)
        interval_ms = INTERVAL_MS[timeframe]
        records = []
        seen: set[int] = set()
        for k in rows:
            open_ms = int(k[0])
            # Normalize to exclusive period end (= conventional close-time boundary)
            close_boundary_ms = open_ms + interval_ms
            if close_boundary_ms in seen:
                continue
            if not (start_ms <= close_boundary_ms <= end_ms + interval_ms):
                # keep bars whose close boundary intersects requested window
                if close_boundary_ms < start_ms or open_ms > end_ms:
                    continue
            seen.add(close_boundary_ms)
            records.append(
                {
                    "ts": pd.Timestamp(close_boundary_ms, unit="ms", tz="UTC"),
                    "open": float(k[1]),
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                    "volume": float(k[5]),
                }
            )
        df = pd.DataFrame.from_records(records).set_index("ts").sort_index()
        df = df[~df.index.duplicated(keep="first")]
        # Final clip by close boundary inside [start, end]
        df = df.loc[(df.index >= pd.Timestamp(start_ms, unit="ms", tz="UTC")) & (df.index <= pd.Timestamp(end_ms, unit="ms", tz="UTC"))]
        return OHLCVFrame(symbol=symbol, timeframe=timeframe, df=df)
