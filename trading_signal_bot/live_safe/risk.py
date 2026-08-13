"""V9 LIVE SAFE risk manager — position sizing, drawdown stop, streak pause."""

from __future__ import annotations

from dataclasses import dataclass, field

from trading_signal_bot.live_safe.filters import LiveSafeConfig


@dataclass
class RiskDecision:
    allowed: bool
    risk_pct: float
    reason: str
    drawdown_r: float = 0.0
    losing_streak: int = 0
    pause_remaining: int = 0


@dataclass
class LiveRiskManager:
    """
    risk_per_trade = 0.5% capital
    If drawdown > 10R → 0.25%
    If drawdown > 20R → STOP
    Losing streak > 5 → pause for 10 trades
    """

    capital: float = 100_000.0
    cfg: LiveSafeConfig = field(default_factory=LiveSafeConfig)
    peak_r: float = 0.0
    equity_r: float = 0.0
    losing_streak: int = 0
    pause_remaining: int = 0
    stopped: bool = False
    trade_count: int = 0

    @property
    def drawdown_r(self) -> float:
        return max(0.0, self.peak_r - self.equity_r)

    def current_risk_pct(self) -> float:
        dd = self.drawdown_r
        if dd > self.cfg.drawdown_stop_r:
            return 0.0
        if dd > self.cfg.drawdown_reduce_r:
            return float(self.cfg.risk_reduced_pct)
        return float(self.cfg.risk_per_trade_pct)

    def position_notional(self, entry: float, sl: float) -> float:
        """Dollar risk / stop distance → notional (paper sizing helper)."""
        risk_pct = self.current_risk_pct() / 100.0
        risk_dollars = self.capital * risk_pct
        stop_dist = abs(float(entry) - float(sl))
        if stop_dist <= 0 or risk_pct <= 0:
            return 0.0
        return risk_dollars / stop_dist

    def gate(self) -> RiskDecision:
        if self.stopped or self.drawdown_r > self.cfg.drawdown_stop_r:
            self.stopped = True
            return RiskDecision(
                allowed=False,
                risk_pct=0.0,
                reason=f"STOP trading: drawdown_R={self.drawdown_r:.2f} > {self.cfg.drawdown_stop_r}",
                drawdown_r=self.drawdown_r,
                losing_streak=self.losing_streak,
                pause_remaining=self.pause_remaining,
            )
        if self.pause_remaining > 0:
            return RiskDecision(
                allowed=False,
                risk_pct=0.0,
                reason=(
                    f"pause after losing streak: {self.pause_remaining} trades remaining "
                    f"(streak={self.losing_streak})"
                ),
                drawdown_r=self.drawdown_r,
                losing_streak=self.losing_streak,
                pause_remaining=self.pause_remaining,
            )
        return RiskDecision(
            allowed=True,
            risk_pct=self.current_risk_pct(),
            reason="risk_ok",
            drawdown_r=self.drawdown_r,
            losing_streak=self.losing_streak,
            pause_remaining=0,
        )

    def on_trade_attempt(self) -> RiskDecision:
        """Call before taking a trade; consumes one pause slot if pausing."""
        decision = self.gate()
        if self.pause_remaining > 0 and not decision.allowed:
            # Count skipped opportunities toward pause completion
            self.pause_remaining = max(0, self.pause_remaining - 1)
        return decision

    def on_trade_closed(self, pnl_r: float) -> None:
        """Update equity curve in R and streak/pause state after a closed trade."""
        self.trade_count += 1
        self.equity_r += float(pnl_r)
        self.peak_r = max(self.peak_r, self.equity_r)
        if self.drawdown_r > self.cfg.drawdown_stop_r:
            self.stopped = True

        if pnl_r < 0:
            self.losing_streak += 1
            if self.losing_streak > self.cfg.losing_streak_pause_at:
                self.pause_remaining = int(self.cfg.losing_streak_pause_trades)
        else:
            self.losing_streak = 0
