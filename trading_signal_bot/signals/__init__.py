"""Signal decision objects and anti-duplication."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import pandas as pd


DecisionType = Literal["SIGNAL_LONG", "SIGNAL_SHORT", "NO_TRADE"]


@dataclass
class SignalDecision:
    decision: DecisionType
    symbol: str
    ts_utc: pd.Timestamp
    direction: Literal["LONG", "SHORT"] | None = None
    category: str = "NO_TRADE"
    setup_score: float = 0.0
    entry: float | None = None
    sl: float | None = None
    tp1: float | None = None
    tp2: float | None = None
    rr1: float | None = None
    rr2: float | None = None
    bias_4h: str | None = None
    bias_1h: str | None = None
    conditions_validated: list[str] = field(default_factory=list)
    conditions_missing: list[str] = field(default_factory=list)
    explanation: str = ""
    sweep_id: str | None = None
    poi_id: str | None = None
    correlated_pair: bool = False
    config_hash: str = ""
    features: dict[str, float] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def duplicate_key(self) -> tuple:
        return (
            self.symbol,
            self.direction,
            self.sweep_id,
            self.meta.get("sweep_time"),
        )

    def is_alertable(self) -> bool:
        return self.decision in ("SIGNAL_LONG", "SIGNAL_SHORT") and self.category in (
            "A+",
            "A",
        )


class SignalDeduper:
    def __init__(self) -> None:
        self._seen: set[tuple] = set()

    def is_duplicate(self, decision: SignalDecision) -> bool:
        if decision.decision == "NO_TRADE":
            return False
        key = decision.duplicate_key
        if key in self._seen:
            return True
        self._seen.add(key)
        return False
