"""V9 paper_live mode — causal bar walk, no future data, real-time trade logs."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

import pandas as pd

from trading_signal_bot.backtesting.metrics import simulate_trade_path
from trading_signal_bot.backtesting.regimes import (
    RegimeThresholds,
    fit_regime_thresholds_from_train,
    label_regime_at,
)
from trading_signal_bot.config import StrategyConfig
from trading_signal_bot.data import MultiTimeframeBundle
from trading_signal_bot.live_safe.filters import LiveSafeConfig, apply_trade_filters
from trading_signal_bot.live_safe.risk import LiveRiskManager
from trading_signal_bot.signals import SignalDeduper
from trading_signal_bot.strategy.engine import evaluate


@dataclass
class PaperTrade:
    symbol: str
    timestamp: str
    direction: str
    entry: float
    sl: float
    tp: float
    rr: float
    score: float
    confidence_score: float
    regime: str
    market_regime: str
    category: str
    risk_pct: float
    position_notional: float
    decision_reason: str
    outcome: str | None = None
    pnl_r: float | None = None
    exit_ts: str | None = None


@dataclass
class PaperLiveResult:
    trades: list[PaperTrade] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    risk_summary: dict[str, Any] = field(default_factory=dict)
    live_ready: bool = False
    messages: list[str] = field(default_factory=list)


LogFn = Callable[[dict[str, Any]], None]


def run_paper_live(
    bundle: MultiTimeframeBundle,
    cfg: StrategyConfig,
    *,
    live_cfg: LiveSafeConfig | None = None,
    risk_mgr: LiveRiskManager | None = None,
    by_regime: dict[str, dict[str, float]] | None = None,
    regime_thresholds: RegimeThresholds | None = None,
    warmup_bars: int | None = None,
    capital: float = 100_000.0,
    log: LogFn | None = None,
) -> PaperLiveResult:
    """
    Simulate real execution causally on 5M closes.
    - No future bars used for entry decision (evaluate is as-of ts)
    - Outcomes simulated only after entry
    - Logs entry/SL/TP/RR/timestamp for each accepted trade
    """
    live_cfg = live_cfg or LiveSafeConfig()
    risk_mgr = risk_mgr or LiveRiskManager(capital=capital, cfg=live_cfg)
    df5 = bundle.m5.df
    if df5.empty:
        return PaperLiveResult(messages=["empty_bundle"])

    atr_period = cfg.atr_period
    w_vol = int(cfg.get("volatility_filter", "W_vol", default=100))
    if regime_thresholds is None:
        fit_end = min(len(bundle.m15.df) - 1, max(w_vol + 10, len(bundle.m15.df) // 3))
        regime_thresholds = fit_regime_thresholds_from_train(
            bundle.m15.df, fit_end, atr_period, w_vol
        )

    risk_cfg = cfg.get("risk", default={}) or {}
    partial = float(risk_cfg.get("partial_tp1_fraction", 0.5))
    path_mode = str(risk_cfg.get("intrabar_path", "worst_case_sl_first"))
    if bundle.asset_class == "CRYPTO":
        max_hold = int(cfg.get("risk", "max_hold_bars_5m_crypto", default=96))
    else:
        max_hold = int(cfg.get("risk", "max_hold_bars_5m", default=48))

    timestamps = list(df5.index)
    if warmup_bars is None:
        warmup = min(500, max(0, len(timestamps) // 10))
    else:
        warmup = min(int(warmup_bars), max(0, len(timestamps) - 1))

    deduper = SignalDeduper()
    out = PaperLiveResult()
    printer = log or (lambda payload: print(payload, flush=True))

    for ts in timestamps[warmup:]:
        # Causal: only bars <= ts are visible inside evaluate()
        decision = evaluate(bundle, ts, cfg, news_blackout=False)
        if decision.decision == "NO_TRADE":
            continue
        if deduper.is_duplicate(decision):
            continue

        market_regime = label_regime_at(
            regime_thresholds,
            bundle.m15.df,
            bundle.h4.df,
            ts,
            atr_period,
            w_vol,
        )
        filt = apply_trade_filters(
            decision,
            market_regime=market_regime,
            by_regime=by_regime,
            cfg=live_cfg,
        )
        risk_gate = risk_mgr.on_trade_attempt()
        if not filt.allowed or not risk_gate.allowed:
            reason = filt.decision_reason if not filt.allowed else risk_gate.reason
            reject = {
                "symbol": decision.symbol,
                "timestamp": str(ts),
                "regime": decision.meta.get("regime"),
                "market_regime": market_regime,
                "score": decision.setup_score,
                "confidence_score": filt.confidence_score,
                "rr": decision.rr1,
                "decision_reason": reason,
            }
            out.rejected.append(reject)
            continue

        entry = float(decision.entry)
        sl = float(decision.sl)
        tp = float(decision.tp1)
        rr = float(decision.rr1 or 0.0)
        notional = risk_mgr.position_notional(entry, sl)
        trade = PaperTrade(
            symbol=decision.symbol,
            timestamp=str(ts),
            direction=decision.direction or "",
            entry=entry,
            sl=sl,
            tp=tp,
            rr=rr,
            score=float(decision.setup_score),
            confidence_score=filt.confidence_score,
            regime=str(decision.meta.get("regime") or ""),
            market_regime=market_regime,
            category=decision.category,
            risk_pct=risk_gate.risk_pct,
            position_notional=notional,
            decision_reason=filt.decision_reason,
        )

        # Real-time entry log (before outcome — no lookahead for logging entry)
        entry_log = {
            "event": "paper_entry",
            "symbol": trade.symbol,
            "timestamp": trade.timestamp,
            "direction": trade.direction,
            "entry": trade.entry,
            "SL": trade.sl,
            "TP": trade.tp,
            "RR": trade.rr,
            "score": trade.score,
            "confidence_score": trade.confidence_score,
            "regime": trade.regime,
            "market_regime": trade.market_regime,
            "risk_pct": trade.risk_pct,
            "decision_reason": trade.decision_reason,
        }
        printer(entry_log)

        entry_idx = int(df5.index.get_loc(ts))
        outcome, _bars, pnl, _mfe, _mae, exit_ts = simulate_trade_path(
            df5,
            entry_idx,
            decision.direction or "LONG",
            entry,
            sl,
            tp,
            decision.tp2,
            float(decision.meta.get("risk_distance") or abs(entry - sl)),
            partial,
            max_hold,
            path_mode,
            max_index=None,
        )
        trade.outcome = outcome
        trade.pnl_r = float(pnl)
        trade.exit_ts = str(exit_ts) if exit_ts is not None else None
        risk_mgr.on_trade_closed(float(pnl))
        out.trades.append(trade)
        printer(
            {
                "event": "paper_exit",
                "symbol": trade.symbol,
                "timestamp": trade.timestamp,
                "exit_ts": trade.exit_ts,
                "outcome": trade.outcome,
                "pnl_R": trade.pnl_r,
                "drawdown_R": risk_mgr.drawdown_r,
            }
        )
        if risk_mgr.stopped:
            msg = f"STOP trading: drawdown_R={risk_mgr.drawdown_r:.2f}"
            out.messages.append(msg)
            printer({"event": "risk_stop", "message": msg})
            break

    out.risk_summary = {
        "capital": risk_mgr.capital,
        "equity_r": risk_mgr.equity_r,
        "drawdown_r": risk_mgr.drawdown_r,
        "trade_count": risk_mgr.trade_count,
        "stopped": risk_mgr.stopped,
        "losing_streak": risk_mgr.losing_streak,
        "n_accepted": len(out.trades),
        "n_rejected": len(out.rejected),
        "trades": [asdict(t) for t in out.trades],
    }
    return out
