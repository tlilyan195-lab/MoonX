"""V9 LIVE SAFE filters — symbol gate, regime, confidence overlay, OOS readiness."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from trading_signal_bot.signals import SignalDecision


@dataclass(frozen=True)
class LiveSafeConfig:
    """Tunable LIVE SAFE thresholds (post-strategy only)."""

    # Symbol performance filter (VAL)
    min_val_expectancy_r: float = 0.3
    max_val_p_exp_le_0: float = 0.4
    max_val_drawdown_r: float = 15.0

    # Regime anti-drawdown
    bad_regime_expectancy_r: float = 0.5
    losing_streak_pause_at: int = 5
    losing_streak_pause_trades: int = 10

    # Confidence overlay (does not mutate V8 base scoring)
    min_confidence_score: float = 3.0
    require_aplus: bool = True
    min_regime_winrate: float = 0.55
    min_rr_bonus: float = 2.0

    # OOS live-ready gate
    min_oos_expectancy_r: float = 0.0
    min_oos_signals: int = 30

    # Risk
    risk_per_trade_pct: float = 0.5
    drawdown_reduce_r: float = 10.0
    risk_reduced_pct: float = 0.25
    drawdown_stop_r: float = 20.0


@dataclass
class SymbolFilterResult:
    symbol: str
    allowed: bool
    reasons: list[str] = field(default_factory=list)
    val_expectancy_r: float | None = None
    p_exp_le_0: float | None = None
    max_drawdown_r: float | None = None


@dataclass
class TradeFilterResult:
    allowed: bool
    confidence_score: float
    reasons: list[str] = field(default_factory=list)
    decision_reason: str = ""


def filter_symbol_performance(
    symbol: str,
    val_metrics: dict[str, Any],
    val_monte_carlo: dict[str, Any] | None = None,
    cfg: LiveSafeConfig | None = None,
    train_metrics: dict[str, Any] | None = None,
) -> SymbolFilterResult:
    """
    Reject symbol if VAL expectancy_R < 0.3 OR p_exp_le_0 > 0.4 OR max_drawdown_R > 15.

    If VAL sample is too small (n_signals < 15) and TRAIN metrics are provided,
    expectancy/drawdown gates use TRAIN (MC still uses VAL when available).
    """
    cfg = cfg or LiveSafeConfig()
    mc = val_monte_carlo or {}
    val_n = int(val_metrics.get("n_signals") or 0)
    use_train = bool(train_metrics) and val_n < 15
    src = train_metrics if use_train else val_metrics
    src_name = "TRAIN" if use_train else "VAL"

    exp = float((src or {}).get("expectancy_R") or 0.0)
    dd = float((src or {}).get("max_drawdown_R") or 0.0)
    p_le0 = float(mc.get("p_exp_le_0") if mc.get("p_exp_le_0") is not None else 1.0)

    reasons: list[str] = []
    if exp < cfg.min_val_expectancy_r:
        reasons.append(
            f"{src_name}_expectancy_R={exp:.4f} < {cfg.min_val_expectancy_r}"
        )
    if p_le0 > cfg.max_val_p_exp_le_0:
        reasons.append(
            f"val_monte_carlo.p_exp_le_0={p_le0:.4f} > {cfg.max_val_p_exp_le_0}"
        )
    if dd > cfg.max_val_drawdown_r:
        reasons.append(
            f"{src_name}_max_drawdown_R={dd:.4f} > {cfg.max_val_drawdown_r}"
        )
    # Stability: TRAIN must not be net-negative (blocks BTC-like TRAIN collapse)
    if train_metrics is not None:
        train_exp = float(train_metrics.get("expectancy_R") or 0.0)
        if train_exp < 0.0:
            reasons.append(f"TRAIN_expectancy_R={train_exp:.4f} < 0")

    return SymbolFilterResult(
        symbol=symbol,
        allowed=not reasons,
        reasons=reasons,
        val_expectancy_r=exp,
        p_exp_le_0=p_le0,
        max_drawdown_r=dd,
    )


def check_oos_live_ready(
    oos_metrics: dict[str, Any],
    cfg: LiveSafeConfig | None = None,
) -> tuple[bool, list[str]]:
    """Block strategy if OOS expectancy <= 0 OR n_signals < 30."""
    cfg = cfg or LiveSafeConfig()
    exp = float(oos_metrics.get("expectancy_R") or 0.0)
    n = int(oos_metrics.get("n_signals") or 0)
    reasons: list[str] = []
    if exp <= cfg.min_oos_expectancy_r:
        reasons.append(f"OOS expectancy_R={exp:.4f} <= {cfg.min_oos_expectancy_r}")
    if n < cfg.min_oos_signals:
        reasons.append(f"OOS n_signals={n} < {cfg.min_oos_signals}")
    return (not reasons), reasons


def _regime_parts(market_regime: str) -> tuple[str, str]:
    """Parse 'HIGH_VOL|RANGE' → ('HIGH_VOL', 'RANGE')."""
    if not market_regime or "|" not in market_regime:
        return "", ""
    vol, trend = market_regime.split("|", 1)
    return vol.upper(), trend.upper()


def _regime_stats(
    by_regime: dict[str, dict[str, float]] | None,
    market_regime: str,
) -> tuple[float | None, float, float]:
    """Return (expectancy_R|None, win_rate, n). expectancy None if unknown."""
    by_regime = by_regime or {}
    bucket = by_regime.get(market_regime)
    if not bucket:
        return None, 0.0, 0.0
    n = float(bucket.get("n", 0.0) or 0.0)
    if n <= 0:
        return None, 0.0, 0.0
    exp = float(bucket.get("expectancy_R", 0.0) or 0.0)
    wr = float(bucket.get("win_rate", 0.0) or 0.0)
    return exp, wr, n


def compute_confidence_score(
    decision: SignalDecision,
    market_regime: str,
    regime_winrate: float,
    cfg: LiveSafeConfig | None = None,
) -> tuple[float, list[str]]:
    """
    V9 confidence overlay (does not change V8 base scoring):
      score = base_score
        +1 LOW_VOL
        +1 TRENDING
        +1 RR > 2
        +1 winrate regime > 55%
    """
    cfg = cfg or LiveSafeConfig()
    base = float(decision.setup_score or decision.meta.get("score") or 0.0)
    score = base
    notes: list[str] = [f"base={base:.0f}"]
    vol, trend = _regime_parts(market_regime)
    # Prefer engine volatility_state when market_regime missing
    vol_state = str(decision.meta.get("volatility_state") or "").lower()
    if vol == "LOW_VOL" or vol_state == "low":
        score += 1
        notes.append("+1 LOW_VOL")
    if trend == "TRENDING" or str(decision.meta.get("regime") or "").startswith("trend"):
        score += 1
        notes.append("+1 TRENDING")
    rr = float(decision.rr1 or decision.meta.get("rr") or 0.0)
    if rr > cfg.min_rr_bonus:
        score += 1
        notes.append(f"+1 RR>{cfg.min_rr_bonus}")
    if regime_winrate > cfg.min_regime_winrate:
        score += 1
        notes.append(f"+1 regime_wr>{cfg.min_regime_winrate:.0%}")
    return score, notes


def apply_trade_filters(
    decision: SignalDecision,
    *,
    market_regime: str = "",
    by_regime: dict[str, dict[str, float]] | None = None,
    cfg: LiveSafeConfig | None = None,
) -> TradeFilterResult:
    """
    Post-strategy trade gates:
    - Reject HIGH_VOL + RANGE when known regime expectancy_R < 0.5
    - Confidence score >= 3 AND category == A+
    """
    cfg = cfg or LiveSafeConfig()
    reasons: list[str] = []
    if decision.decision == "NO_TRADE":
        return TradeFilterResult(
            allowed=False,
            confidence_score=0.0,
            reasons=["no_trade"],
            decision_reason="no_trade",
        )

    regime_exp, regime_wr, _n = _regime_stats(by_regime, market_regime)
    vol, trend = _regime_parts(market_regime)
    if (
        vol == "HIGH_VOL"
        and trend == "RANGE"
        and regime_exp is not None
        and regime_exp < cfg.bad_regime_expectancy_r
    ):
        reasons.append(
            f"regime_block HIGH_VOL|RANGE expectancy_R={regime_exp:.4f} "
            f"< {cfg.bad_regime_expectancy_r}"
        )

    conf, conf_notes = compute_confidence_score(
        decision, market_regime, regime_wr, cfg=cfg
    )
    if cfg.require_aplus and decision.category != "A+":
        reasons.append(f"category={decision.category} (require A+)")
    if conf < cfg.min_confidence_score:
        reasons.append(
            f"confidence_score={conf:.1f} < {cfg.min_confidence_score} ({', '.join(conf_notes)})"
        )

    allowed = not reasons
    decision_reason = "live_safe_pass" if allowed else "; ".join(reasons)
    return TradeFilterResult(
        allowed=allowed,
        confidence_score=conf,
        reasons=reasons,
        decision_reason=decision_reason,
    )
