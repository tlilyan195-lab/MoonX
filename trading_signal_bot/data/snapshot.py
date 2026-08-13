"""Immutable data snapshots + data_snapshot_id."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from trading_signal_bot.data import OHLCVFrame


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_frame_parquet(frame: OHLCVFrame, path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    df = frame.df.copy()
    df = df.reset_index()
    # standardize column name
    if df.columns[0] != "ts":
        df = df.rename(columns={df.columns[0]: "ts"})
    df.to_parquet(path, index=False)
    return _file_sha256(path)


def read_frame_parquet(symbol: str, timeframe: str, path: Path) -> OHLCVFrame:
    df = pd.read_parquet(path)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df = df.set_index("ts").sort_index()
    from trading_signal_bot.data import Timeframe

    return OHLCVFrame(symbol=symbol, timeframe=timeframe, df=df)  # type: ignore[arg-type]


@dataclass
class SnapshotBuilder:
    root: Path
    providers: dict[str, str]
    range_start: str
    range_end: str
    bar_timestamp: str = "close"
    timezone_storage: str = "UTC"
    aggregation: dict[str, Any] | None = None
    git_commit: str | None = None

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        if self.aggregation is None:
            self.aggregation = {
                "FX_XAU": "from_5M_right_closed_right",
                "CRYPTO": "native_binance_klines",
            }

    def add_frame(self, frame: OHLCVFrame, filename: str | None = None) -> str:
        fname = filename or f"{frame.symbol}_{frame.timeframe}.parquet"
        return write_frame_parquet(frame, self.root / fname)

    def finalize(
        self,
        file_hashes: dict[str, str],
        quality_report: dict[str, Any],
        extras: dict[str, Any] | None = None,
    ) -> tuple[str, Path]:
        manifest: dict[str, Any] = {
            "created_at_utc": pd.Timestamp.now(tz="UTC").isoformat(),
            "providers": self.providers,
            "range_utc": {"start": self.range_start, "end": self.range_end},
            "bar_timestamp": self.bar_timestamp,
            "timezone_storage": self.timezone_storage,
            "aggregation": self.aggregation,
            "file_hashes": file_hashes,
            "git_commit": self.git_commit,
            "quality_report_sha256": None,
            "extras": extras or {},
            "signals_only": True,
            "pilot": True,
        }
        # Write quality report first
        qpath = self.root / "quality_report.json"
        qpath.write_text(json.dumps(quality_report, indent=2, default=str), encoding="utf-8")
        qhash = _file_sha256(qpath)
        manifest["quality_report_sha256"] = qhash
        # data_snapshot_id from canonical manifest without the id field
        payload = json.dumps(manifest, sort_keys=True, default=str).encode("utf-8")
        snapshot_id = "sha256:" + hashlib.sha256(payload).hexdigest()
        manifest["data_snapshot_id"] = snapshot_id
        mpath = self.root / "manifest.json"
        mpath.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
        return snapshot_id, mpath


def load_snapshot_manifest(root: Path) -> dict[str, Any]:
    return json.loads((Path(root) / "manifest.json").read_text(encoding="utf-8"))


def verify_snapshot_hashes(root: Path) -> dict[str, bool]:
    root = Path(root)
    manifest = load_snapshot_manifest(root)
    out: dict[str, bool] = {}
    for fname, expected in manifest.get("file_hashes", {}).items():
        path = root / fname
        out[fname] = path.exists() and _file_sha256(path) == expected
    return out
