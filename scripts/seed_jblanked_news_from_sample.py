#!/usr/bin/env python3
"""Seed normalized news parquet from JBlanked Free pilot SAMPLE (real API rows only).

Does NOT invent events. Only persists sample_events already returned by the API.
 complementary weekly Free harvests can append later.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from trading_signal_bot.data.jblanked_calendar import (  # noqa: E402
    normalize_event_row,
    save_events_parquet,
)

SAMPLE = ROOT / "docs" / "ETAPE_5_1B_JBLANKED_FREE_PILOT_SAMPLE.json"
OUT = ROOT / "data" / "cache" / "jblanked" / "events_pilot.parquet"


def main() -> int:
    if not SAMPLE.is_file():
        print(f"FAIL: missing {SAMPLE}")
        return 1
    sample = json.loads(SAMPLE.read_text(encoding="utf-8"))
    rows = []
    for w in sample.get("windows") or []:
        for ev in ((w.get("summary") or {}).get("sample_events") or []):
            # Rebuild provider-shaped row from normalized sample fields
            raw = {
                "Name": ev.get("name"),
                "Currency": ev.get("currency"),
                "Impact": ev.get("impact"),
                "Date": ev.get("date"),
                "Event_ID": ev.get("event_id"),
                "Category": ev.get("category"),
            }
            norm = normalize_event_row(raw)
            if norm:
                rows.append(norm)
    if not rows:
        print("FAIL: no sample_events to seed")
        return 1
    import pandas as pd

    df = pd.DataFrame(rows).drop_duplicates(subset=["ts_utc", "currency", "event", "event_id"])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    save_events_parquet(df, OUT)
    print(json.dumps({"out": str(OUT), "n_events": int(len(df)), "note": "partial_week_from_pass_sample"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
