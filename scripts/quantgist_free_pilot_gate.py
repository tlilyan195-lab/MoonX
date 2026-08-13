#!/usr/bin/env python3
"""
ÉTAPE 5.1B′ — QuantGist FREE pilot gate.

Exits immediately if QUANTGIST_API_KEY is missing (do not invent/register here).
When a key is present in .env, runs a small calendar sample only (no 3y OHLC).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import requests

# Load .env if present without printing secrets
_env = ROOT / ".env"
if _env.exists():
    for line in _env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main() -> int:
    key = os.environ.get("QUANTGIST_API_KEY", "").strip()
    stop_doc = ROOT / "docs" / "ETAPE_5_1B_QUANTGIST_FREE_PILOT_STOP.md"
    if not key:
        print("QUANTGIST_FREE_PILOT = FAIL")
        print("REASON = QUANTGIST_API_KEY absent — calendar/events require auth (HTTP 401).")
        print("ACTION = Create free account + put key in .env only (never in chat).")
        print(f"SEE = {stop_doc}")
        # Anonymous evidence only
        evidence = []
        for path in (
            "/v1/health",
            "/v1/calendar/range?start=2025-12-01&end=2025-12-07&currencies=USD,EUR&impact=high&limit=5",
        ):
            r = requests.get(f"https://api.quantgist.com{path}", timeout=20)
            body = r.text[:300]
            try:
                body = r.json()
            except Exception:
                pass
            evidence.append({"path": path, "status": r.status_code, "body": body})
        out = ROOT / "docs" / "ETAPE_5_1B_QUANTGIST_AUTH_EVIDENCE.json"
        out.write_text(json.dumps(evidence, indent=2, default=str), encoding="utf-8")
        print(f"Wrote {out}")
        return 2

    # Key present: small free-plan sample (caller continues in a later turn)
    headers = {"X-API-Key": key, "Accept": "application/json"}
    sess = requests.Session()
    samples = {}
    # Recent week high-impact multi-currency (small)
    r = sess.get(
        "https://api.quantgist.com/v1/calendar/range",
        headers=headers,
        params={
            "start": "2025-12-01",
            "end": "2025-12-07",
            "currencies": "USD,EUR,GBP,JPY",
            "impact": "high",
            "limit": 50,
        },
        timeout=30,
    )
    samples["calendar_range"] = {"status": r.status_code, "body": _safe_json(r)}
    # Oldest reachable probe on Free (attempt ~400 days back — expect clamp/402/empty)
    r2 = sess.get(
        "https://api.quantgist.com/v1/calendar/range",
        headers=headers,
        params={
            "start": "2024-01-01",
            "end": "2024-01-07",
            "currencies": "USD",
            "impact": "high",
            "limit": 20,
        },
        timeout=30,
    )
    samples["history_probe_2024"] = {"status": r2.status_code, "body": _safe_json(r2)}
    r3 = sess.get(
        "https://api.quantgist.com/v1/events",
        headers=headers,
        params={
            "event_type": "economic_release",
            "currency": "USD",
            "impact": "high",
            "per_page": 10,
            "backtest_safe": "true",
            "from_date": "2025-11-01",
            "to_date": "2025-12-01",
        },
        timeout=30,
    )
    samples["events_backtest_safe"] = {"status": r3.status_code, "body": _safe_json(r3)}

    out = ROOT / "docs" / "ETAPE_5_1B_QUANTGIST_FREE_PILOT_SAMPLE.json"
    # Never write the API key
    out.write_text(json.dumps(samples, indent=2, default=str)[:200000], encoding="utf-8")

    statuses = {
        name: block.get("status")
        for name, block in samples.items()
        if isinstance(block, dict)
    }
    auth_fail = [n for n, s in statuses.items() if s in (401, 403)]
    ok_data = [
        n
        for n, block in samples.items()
        if isinstance(block, dict)
        and block.get("status") == 200
        and _body_has_events(block.get("body"))
    ]
    print(f"Wrote {out}")
    print(f"http_statuses = {statuses}")
    if auth_fail:
        print("QUANTGIST_FREE_PILOT = FAIL")
        print(
            f"REASON = auth failed on {auth_fail} "
            "(key missing/revoked/invalid — regenerate at dashboard/keys)."
        )
        print("Do NOT treat env var presence as API success.")
        return 2
    if not ok_data:
        print("QUANTGIST_FREE_PILOT = FAIL")
        print("REASON = no section returned HTTP 200 with events.")
        return 2
    print("QUANTGIST_FREE_PILOT = PASS_SAMPLE_WRITTEN")
    print("Next: python scripts/analyze_quantgist_free_pilot.py")
    print("Inspect sample; do not treat Free as 3y confirmation.")
    return 0


def _body_has_events(body) -> bool:  # noqa: ANN001
    if body is None:
        return False
    if isinstance(body, list):
        return len(body) > 0
    if isinstance(body, dict):
        if body.get("error"):
            return False
        for key in ("data", "items", "events", "results"):
            if isinstance(body.get(key), list) and body[key]:
                return True
    return False


def _safe_json(r: requests.Response):
    try:
        return r.json()
    except Exception:
        return r.text[:500]


if __name__ == "__main__":
    raise SystemExit(main())
