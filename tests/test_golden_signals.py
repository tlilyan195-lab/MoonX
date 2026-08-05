"""P0 golden SIGNAL fixtures — must fail if NO_TRADE."""

from __future__ import annotations

from tests.fixtures.golden_ohlc import build_golden_long_bundle, build_golden_short_bundle
from trading_signal_bot.strategy.engine import evaluate


def test_golden_signal_long_fields():
    bundle, ts, cfg = build_golden_long_bundle()
    d = evaluate(bundle, ts, cfg)
    assert d.decision == "SIGNAL_LONG", d.explanation
    assert d.direction == "LONG"
    assert d.entry is not None and d.sl is not None and d.tp1 is not None
    assert d.sl < d.entry < d.tp1
    assert d.rr1 is not None and d.rr1 >= cfg.rr_min
    assert d.poi_id is not None and len(d.poi_id) > 0
    assert d.category in ("A+", "A", "B")
    assert d.setup_score >= 0


def test_golden_signal_short_fields():
    bundle, ts, cfg = build_golden_short_bundle()
    d = evaluate(bundle, ts, cfg)
    assert d.decision == "SIGNAL_SHORT", d.explanation
    assert d.direction == "SHORT"
    assert d.entry is not None and d.sl is not None and d.tp1 is not None
    assert d.tp1 < d.entry < d.sl
    assert d.rr1 is not None and d.rr1 >= cfg.rr_min
    assert d.poi_id is not None and len(d.poi_id) > 0
    assert d.category in ("A+", "A", "B")
    assert d.setup_score >= 0


def test_golden_long_deterministic():
    bundle, ts, cfg = build_golden_long_bundle()
    a = evaluate(bundle, ts, cfg)
    b = evaluate(bundle, ts, cfg)
    assert a.decision == b.decision == "SIGNAL_LONG"
    assert a.entry == b.entry and a.sl == b.sl and a.tp1 == b.tp1
    assert a.poi_id == b.poi_id and a.category == b.category
