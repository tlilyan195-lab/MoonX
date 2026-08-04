"""Backtest metrics and trade simulation helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
import pandas as pd


Outcome = Literal["tp1", "tp2", "sl", "timeout", "unknown"]


@dataclass
class SimulatedTrade:
    signal_id: str
    symbol: str
    direction: str
    category: str
    entry_ts: pd.Timestamp
    entry: float
    sl: float
    tp1: float
    tp2: float | None
    rr1: float
    risk: float
    partial_tp1_fraction: float
    outcome: Outcome = "unknown"
    exit_ts: pd.Timestamp | None = None
    pnl_R: float = 0.0
    mfe_R: float = 0.0
    mae_R: float = 0.0
    bars_held: int = 0
    session_label: str = ""
    weekday: int = -1
    market_regime: str = ""
    setup_score: float = 0.0


@dataclass
class MetricsReport:
    n_signals: int = 0
    n_wins: int = 0
    n_losses: int = 0
    win_rate: float = 0.0
    loss_rate: float = 0.0
    profit_factor: float = 0.0
    expectancy_R: float = 0.0
    avg_win_R: float = 0.0
    avg_loss_R: float = 0.0
    max_drawdown_R: float = 0.0
    max_consec_wins: int = 0
    max_consec_losses: int = 0
    by_symbol: dict[str, dict[str, float]] = field(default_factory=dict)
    by_session: dict[str, dict[str, float]] = field(default_factory=dict)
    by_weekday: dict[str, dict[str, float]] = field(default_factory=dict)
    by_category: dict[str, dict[str, float]] = field(default_factory=dict)
    by_regime: dict[str, dict[str, float]] = field(default_factory=dict)
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_signals": self.n_signals,
            "n_wins": self.n_wins,
            "n_losses": self.n_losses,
            "win_rate": self.win_rate,
            "loss_rate": self.loss_rate,
            "profit_factor": self.profit_factor,
            "expectancy_R": self.expectancy_R,
            "avg_win_R": self.avg_win_R,
            "avg_loss_R": self.avg_loss_R,
            "max_drawdown_R": self.max_drawdown_R,
            "max_consec_wins": self.max_consec_wins,
            "max_consec_losses": self.max_consec_losses,
            "by_symbol": self.by_symbol,
            "by_session": self.by_session,
            "by_weekday": self.by_weekday,
            "by_category": self.by_category,
            "by_regime": self.by_regime,
            "extras": self.extras,
        }


def simulate_trade_path(
    df_5m: pd.DataFrame,
    entry_idx: int,
    direction: str,
    entry: float,
    sl: float,
    tp1: float,
    tp2: float | None,
    risk: float,
    partial_tp1_fraction: float,
    max_hold_bars: int,
    intrabar_path: str = "worst_case_sl_first",
) -> tuple[Outcome, int, float, float, float, pd.Timestamp | None]:
    """
    Walk forward from entry_idx+1.
    If SL and TP both touched in same bar: worst_case => SL first.
    PnL model: partial_tp1_fraction at TP1, remainder at TP2 (or SL after TP1).
    """
    if risk <= 0:
        return "unknown", 0, 0.0, 0.0, 0.0, None

    mfe = 0.0
    mae = 0.0
    tp1_hit = False
    end = min(len(df_5m) - 1, entry_idx + max_hold_bars)
    for i in range(entry_idx + 1, end + 1):
        row = df_5m.iloc[i]
        high = float(row["high"])
        low = float(row["low"])
        ts = df_5m.index[i]
        bars = i - entry_idx

        if direction == "LONG":
            mfe = max(mfe, (high - entry) / risk)
            mae = min(mae, (low - entry) / risk)
            hit_sl = low <= sl
            hit_tp1 = high >= tp1
            hit_tp2 = tp2 is not None and high >= tp2
        else:
            mfe = max(mfe, (entry - low) / risk)
            mae = min(mae, (entry - high) / risk)
            hit_sl = high >= sl
            hit_tp1 = low <= tp1
            hit_tp2 = tp2 is not None and low <= tp2

        if not tp1_hit:
            if hit_sl and hit_tp1:
                if intrabar_path == "worst_case_sl_first":
                    return "sl", bars, -1.0, mfe, mae, ts
                # optimistic variant (separate runs only)
                tp1_hit = True
            elif hit_sl:
                return "sl", bars, -1.0, mfe, mae, ts
            elif hit_tp1:
                tp1_hit = True
                if hit_tp2:
                    pnl = partial_tp1_fraction * ((tp1 - entry) / risk if direction == "LONG" else (entry - tp1) / risk)
                    rem = 1.0 - partial_tp1_fraction
                    tp2_r = ((tp2 - entry) / risk if direction == "LONG" else (entry - tp2) / risk)  # type: ignore[operator]
                    return "tp2", bars, pnl + rem * tp2_r, mfe, mae, ts
            continue

        # After TP1: remainder managed to TP2 or SL
        if hit_sl and hit_tp2:
            if intrabar_path == "worst_case_sl_first":
                r1 = (tp1 - entry) / risk if direction == "LONG" else (entry - tp1) / risk
                pnl = partial_tp1_fraction * r1 + (1.0 - partial_tp1_fraction) * (-1.0)
                return "sl", bars, pnl, mfe, mae, ts
        if hit_sl:
            r1 = (tp1 - entry) / risk if direction == "LONG" else (entry - tp1) / risk
            pnl = partial_tp1_fraction * r1 + (1.0 - partial_tp1_fraction) * (-1.0)
            return "sl", bars, pnl, mfe, mae, ts
        if hit_tp2:
            r1 = (tp1 - entry) / risk if direction == "LONG" else (entry - tp1) / risk
            r2 = (tp2 - entry) / risk if direction == "LONG" else (entry - tp2) / risk  # type: ignore[operator]
            pnl = partial_tp1_fraction * r1 + (1.0 - partial_tp1_fraction) * r2
            return "tp2", bars, pnl, mfe, mae, ts

    # timeout
    bars = end - entry_idx
    ts = df_5m.index[end] if end > entry_idx else None
    if tp1_hit:
        r1 = (tp1 - entry) / risk if direction == "LONG" else (entry - tp1) / risk
        # remainder marked to market at last close
        last = float(df_5m.iloc[end]["close"])
        rem_r = (last - entry) / risk if direction == "LONG" else (entry - last) / risk
        pnl = partial_tp1_fraction * r1 + (1.0 - partial_tp1_fraction) * rem_r
        return "timeout", bars, pnl, mfe, mae, ts
    last = float(df_5m.iloc[end]["close"])
    pnl = (last - entry) / risk if direction == "LONG" else (entry - last) / risk
    return "timeout", bars, pnl, mfe, mae, ts


def _streaks(pnls: list[float]) -> tuple[int, int]:
    max_w = max_l = cur_w = cur_l = 0
    for p in pnls:
        if p > 0:
            cur_w += 1
            cur_l = 0
            max_w = max(max_w, cur_w)
        elif p < 0:
            cur_l += 1
            cur_w = 0
            max_l = max(max_l, cur_l)
        else:
            cur_w = cur_l = 0
    return max_w, max_l


def max_drawdown_R(pnls: list[float]) -> float:
    equity = np.cumsum(pnls) if pnls else np.array([0.0])
    peak = -np.inf
    dd = 0.0
    for x in equity:
        peak = max(peak, x)
        dd = min(dd, x - peak)
    return float(abs(dd))


def compute_metrics(trades: list[SimulatedTrade]) -> MetricsReport:
    if not trades:
        return MetricsReport()
    pnls = [t.pnl_R for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    n = len(trades)
    n_w, n_l = len(wins), len(losses)
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    pf = (gross_win / gross_loss) if gross_loss > 0 else float("inf") if gross_win > 0 else 0.0
    max_w, max_l = _streaks(pnls)

    report = MetricsReport(
        n_signals=n,
        n_wins=n_w,
        n_losses=n_l,
        win_rate=n_w / n if n else 0.0,
        loss_rate=n_l / n if n else 0.0,
        profit_factor=pf if np.isfinite(pf) else 999.0,
        expectancy_R=float(np.mean(pnls)),
        avg_win_R=float(np.mean(wins)) if wins else 0.0,
        avg_loss_R=float(np.mean(losses)) if losses else 0.0,
        max_drawdown_R=max_drawdown_R(pnls),
        max_consec_wins=max_w,
        max_consec_losses=max_l,
    )

    def _bucket(key_fn) -> dict[str, dict[str, float]]:
        buckets: dict[str, list[float]] = {}
        for t in trades:
            buckets.setdefault(str(key_fn(t)), []).append(t.pnl_R)
        out: dict[str, dict[str, float]] = {}
        for k, vals in buckets.items():
            out[k] = {
                "n": float(len(vals)),
                "expectancy_R": float(np.mean(vals)),
                "win_rate": float(np.mean([1.0 if v > 0 else 0.0 for v in vals])),
            }
        return out

    report.by_symbol = _bucket(lambda t: t.symbol)
    report.by_session = _bucket(lambda t: t.session_label or "NA")
    report.by_weekday = _bucket(lambda t: str(t.weekday))
    report.by_category = _bucket(lambda t: t.category)
    report.by_regime = _bucket(lambda t: t.market_regime or "NA")
    return report
