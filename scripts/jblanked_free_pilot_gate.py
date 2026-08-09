#!/usr/bin/env python3
"""ÉTAPE 5.1 — JBlanked Free Calendar read-only pilot gate.

Requires JBLANKED_API_KEY in .env (never printed).
Default: ONE range request (Free = 1 req/day). No strategy/OOS/orders.
Does not build the definitive news connector.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

USER_AGENT = "MoonX-JBlanked-FreePilot/1.0 (+signals-only; no-trading)"
BASE = "https://www.jblanked.com/news/api/mql5/calendar/range/"
SAMPLE_OUT = ROOT / "docs" / "ETAPE_5_1B_JBLANKED_FREE_PILOT_SAMPLE.json"
REPORT_OUT = ROOT / "docs" / "ETAPE_5_1B_JBLANKED_FREE_PILOT_GATE.md"

# Single default window — Free tier is 1 request/day.
DEFAULT_WINDOW = ("pilot_week_2025q4", "2025-10-01", "2025-10-07")
TARGET_CURRENCIES = frozenset({"USD", "EUR", "GBP", "JPY"})


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def _mask_key(key: str) -> str:
    if len(key) <= 8:
        return "***"
    return f"{key[:4]}…{key[-4:]} (len={len(key)})"


def _get_json(url: str, api_key: str, timeout: float = 45.0) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Api-Key {api_key}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            try:
                parsed: Any = json.loads(body) if body else None
            except json.JSONDecodeError:
                return {
                    "ok": False,
                    "http_status": int(resp.status),
                    "error": "non_json",
                    "snippet": body[:400],
                }
            return {
                "ok": True,
                "http_status": int(resp.status),
                "data": parsed,
                "raw_bytes": len(body.encode("utf-8")),
            }
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        return {
            "ok": False,
            "http_status": int(exc.code),
            "error": raw[:800],
            "snippet": raw[:400],
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "http_status": None, "error": f"{type(exc).__name__}: {exc}"}


def _norm_row(row: dict[str, Any]) -> dict[str, Any]:
    # Accept both TitleCase (docs) and lower variants.
    def g(*keys: str) -> Any:
        for k in keys:
            if k in row:
                return row[k]
        lower = {str(k).lower(): v for k, v in row.items()}
        for k in keys:
            if k.lower() in lower:
                return lower[k.lower()]
        return None

    return {
        "name": g("Name", "name", "event"),
        "currency": g("Currency", "currency"),
        "country": g("Country", "country"),
        "impact": g("Impact", "impact"),
        "date": g("Date", "date", "datetime"),
        "event_id": g("EventID", "eventID", "event_id", "id"),
        "category": g("Category", "category"),
        "keys": sorted(row.keys()),
    }


def _fingerprint(norm: dict[str, Any]) -> str:
    raw = "|".join(
        [
            str(norm.get("date") or ""),
            str(norm.get("currency") or ""),
            str(norm.get("name") or ""),
            str(norm.get("impact") or ""),
            str(norm.get("event_id") or ""),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _summarize(rows: list[Any]) -> dict[str, Any]:
    currencies: dict[str, int] = {}
    impacts: dict[str, int] = {}
    target_hits = 0
    high_target = 0
    sample: list[dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        n = _norm_row(r)
        ccy = str(n.get("currency") or "").upper()
        imp = str(n.get("impact") or "")
        currencies[ccy] = currencies.get(ccy, 0) + 1
        impacts[imp] = impacts.get(imp, 0) + 1
        if ccy in TARGET_CURRENCIES:
            target_hits += 1
            if imp.lower() == "high":
                high_target += 1
        if len(sample) < 8:
            item = dict(n)
            item["fingerprint"] = _fingerprint(n)
            sample.append(item)
    return {
        "n_events": len([r for r in rows if isinstance(r, dict)]),
        "currencies": dict(sorted(currencies.items(), key=lambda x: -x[1])),
        "impacts": impacts,
        "target_currency_hits": target_hits,
        "target_high_impact": high_target,
        "sample_events": sample,
        "field_union": sorted({k for r in rows if isinstance(r, dict) for k in r.keys()}),
    }


def _probe(api_key: str, label: str, date_from: str, date_to: str) -> dict[str, Any]:
    qs = urllib.parse.urlencode({"from": date_from, "to": date_to})
    url = f"{BASE}?{qs}"
    safe = f"{BASE}?from={date_from}&to={date_to}"
    result = _get_json(url, api_key)
    out: dict[str, Any] = {
        "label": label,
        "from": date_from,
        "to": date_to,
        "url_safe": safe,
        "source": "mql5",
        "http_status": result.get("http_status"),
        "ok": bool(result.get("ok")),
    }
    if not result.get("ok"):
        out["error"] = result.get("error") or result.get("snippet")
        status = result.get("http_status")
        out["auth_failed"] = status in (401, 403)
        out["rate_limited"] = status == 429
        out["summary"] = {"n_events": 0}
        return out
    data = result.get("data")
    if isinstance(data, dict) and ("message" in data or "detail" in data):
        # Some APIs wrap errors in 200; treat empty list-less payloads carefully.
        if not any(isinstance(data.get(k), list) for k in ("data", "events", "results")):
            out["ok"] = False
            out["error"] = json.dumps(data)[:400]
            out["summary"] = {"n_events": 0}
            return out
        for k in ("data", "events", "results"):
            if isinstance(data.get(k), list):
                data = data[k]
                break
    if not isinstance(data, list):
        out["ok"] = False
        out["error"] = f"unexpected_body_type={type(data).__name__}"
        out["summary"] = {"n_events": 0}
        return out
    out["summary"] = _summarize(data)
    out["raw_bytes"] = result.get("raw_bytes")
    return out


def _verdict(probes: list[dict[str, Any]], has_key: bool) -> dict[str, str]:
    if not has_key:
        return {
            "JBLANKED_FREE_PILOT": "FAIL_NO_KEY",
            "3Y_HISTORY": "NOT_TESTED",
            "LOOKAHEAD_SAFETY": "DOC_PARTIAL",
            "PAYMENT_RECOMMENDATION": "NO",
            "NEXT": "Add JBLANKED_API_KEY to .env then re-run gate (1 free req/day)",
        }
    ok = [p for p in probes if p.get("ok") and (p.get("summary") or {}).get("n_events", 0) > 0]
    if any(p.get("auth_failed") for p in probes) and not ok:
        return {
            "JBLANKED_FREE_PILOT": "FAIL_AUTH",
            "3Y_HISTORY": "NOT_CONFIRMED",
            "LOOKAHEAD_SAFETY": "UNKNOWN",
            "PAYMENT_RECOMMENDATION": "NO",
            "NEXT": "Inspect auth; do not buy credits yet",
        }
    if any(p.get("rate_limited") for p in probes) and not ok:
        return {
            "JBLANKED_FREE_PILOT": "FAIL_RATE_LIMIT",
            "3Y_HISTORY": "NOT_CONFIRMED",
            "LOOKAHEAD_SAFETY": "UNKNOWN",
            "PAYMENT_RECOMMENDATION": "NO",
            "NEXT": "Wait for next free daily request; do not buy credits yet",
        }
    if not ok:
        return {
            "JBLANKED_FREE_PILOT": "FAIL_EMPTY",
            "3Y_HISTORY": "NOT_CONFIRMED",
            "LOOKAHEAD_SAFETY": "UNKNOWN",
            "PAYMENT_RECOMMENDATION": "NO",
            "NEXT": "Inspect empty/error body",
        }
    summary = ok[0].get("summary") or {}
    has_impact = bool(summary.get("impacts"))
    has_ccy = bool(summary.get("currencies"))
    return {
        "JBLANKED_FREE_PILOT": "PASS_SAMPLE",
        "3Y_HISTORY": "DOC_ONLY_NEED_MULTI_DAY_OR_CREDITS",
        "LOOKAHEAD_SAFETY": "PARTIAL_SCHEDULED_TIME_ONLY",
        "PAYMENT_RECOMMENDATION": "NO",
        "NEXT": (
            "Analyze schema/coverage; Free=1 req/day so 3y paging is slow without credits — "
            f"impact_fields={has_impact} currency_fields={has_ccy}"
        ),
    }


def main() -> int:
    _load_dotenv(ROOT / ".env")
    api_key = (os.environ.get("JBLANKED_API_KEY") or "").strip()
    has_key = bool(api_key)

    probes: list[dict[str, Any]] = []
    if has_key:
        label, d0, d1 = DEFAULT_WINDOW
        probes.append(_probe(api_key, label, d0, d1))

    verdict = _verdict(probes, has_key)
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "provider": "jblanked",
        "product": "mql5/calendar/range",
        "key_present": has_key,
        "key_fingerprint": _mask_key(api_key) if has_key else None,
        "free_note": "Docs: free usage decreased to 1 request/day; default gate uses ONE call.",
        "windows": probes,
        "verdict": verdict,
        "constraints": {
            "no_strategy_rule_changes": True,
            "no_oos": True,
            "no_real_orders": True,
            "no_definitive_connector": True,
            "no_payment": True,
            "fmp_status": "BLOCKED_ENTITLEMENT",
            "quantgist_status": "BLOCKED_AUTH",
        },
        "docs_refs": [
            "https://www.jblanked.com/news/api/docs/calendar/",
            "https://www.jblanked.com/news/api/docs/",
        ],
    }
    SAMPLE_OUT.parent.mkdir(parents=True, exist_ok=True)
    SAMPLE_OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    lines = [
        "# ÉTAPE 5.1B — JBlanked Free Calendar pilot gate",
        "",
        f"- Generated (UTC): `{payload['generated_at_utc']}`",
        f"- Key present: `{has_key}`",
        f"- Key fingerprint: `{payload['key_fingerprint']}`",
        "- Free limit: **1 request/day** (docs) — this gate uses one range call by default.",
        "",
        "## Verdict",
        "",
    ]
    for k, v in verdict.items():
        lines.append(f"- **{k}**: `{v}`")
    lines.extend(["", "## Windows", ""])
    if not probes:
        lines.append("- No live probes (missing `JBLANKED_API_KEY`).")
    for p in probes:
        n = (p.get("summary") or {}).get("n_events", 0)
        lines.append(
            f"- `{p['label']}` `{p['from']}`→`{p['to']}`: "
            f"http={p.get('http_status')} ok={p.get('ok')} n_events={n}"
        )
        if p.get("error"):
            err = str(p["error"]).replace(api_key, "***") if has_key else str(p["error"])
            lines.append(f"  - error: `{err[:220]}`")
        s = p.get("summary") or {}
        if s.get("impacts") is not None:
            lines.append(f"  - impacts: `{s.get('impacts')}`")
            lines.append(f"  - currencies: `{s.get('currencies')}`")
            lines.append(f"  - target_high_impact: `{s.get('target_high_impact')}`")
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- Read-only pilot only — not the definitive NEWS connector.",
            "- Prefer MQL5 source path (documented official calendar feed via JBlanked).",
            "- No PIT vintage → blackout uses scheduled `Date` only.",
            "- FMP = `BLOCKED_ENTITLEMENT` (402). QuantGist = `BLOCKED_AUTH`.",
            "- Do not buy JBlanked credits until Free sample proves field fit.",
            "",
        ]
    )
    REPORT_OUT.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"sample": str(SAMPLE_OUT), "report": str(REPORT_OUT), "verdict": verdict}, indent=2))
    return 0 if verdict.get("JBLANKED_FREE_PILOT") == "PASS_SAMPLE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
