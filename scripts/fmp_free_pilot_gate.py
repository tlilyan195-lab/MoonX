#!/usr/bin/env python3
"""ÉTAPE 5.1 — FMP Free Economic Calendar read-only pilot gate.

Requires FMP_API_KEY in .env (never printed).
No strategy changes. No OOS. No real orders. No scraping.
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

USER_AGENT = "MoonX-FMP-FreePilot/1.0 (+signals-only; no-trading)"
STABLE_CALENDAR = "https://financialmodelingprep.com/stable/economic-calendar"
SAMPLE_OUT = ROOT / "docs" / "ETAPE_5_1B_FMP_FREE_PILOT_SAMPLE.json"
REPORT_OUT = ROOT / "docs" / "ETAPE_5_1B_FMP_FREE_PILOT_GATE.md"

# Pilot windows — ≤90 days per FMP docs; read-only; no 3y bulk yet.
WINDOWS = (
    ("pilot_2025q4_overlap", "2025-10-01", "2025-12-29"),  # overlaps OHLC pilot
    ("history_probe_2024", "2024-01-01", "2024-03-30"),
    ("history_probe_2023", "2023-01-01", "2023-03-30"),
)

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


def _get_json(url: str, timeout: float = 45.0) -> dict[str, Any]:
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
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


def _event_fingerprint(row: dict[str, Any]) -> str:
    raw = "|".join(
        [
            str(row.get("date") or ""),
            str(row.get("country") or ""),
            str(row.get("currency") or ""),
            str(row.get("event") or ""),
            str(row.get("impact") or ""),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _summarize_events(rows: list[dict[str, Any]]) -> dict[str, Any]:
    currencies: dict[str, int] = {}
    impacts: dict[str, int] = {}
    countries: dict[str, int] = {}
    target_hits = 0
    high_usd = 0
    sample: list[dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        ccy = str(r.get("currency") or "").upper()
        imp = str(r.get("impact") or "")
        ctry = str(r.get("country") or "")
        currencies[ccy] = currencies.get(ccy, 0) + 1
        impacts[imp] = impacts.get(imp, 0) + 1
        countries[ctry] = countries.get(ctry, 0) + 1
        if ccy in TARGET_CURRENCIES:
            target_hits += 1
        if ccy == "USD" and imp.lower() == "high":
            high_usd += 1
        if len(sample) < 5:
            sample.append(
                {
                    "date": r.get("date"),
                    "country": r.get("country"),
                    "currency": r.get("currency"),
                    "event": r.get("event"),
                    "impact": r.get("impact"),
                    "fingerprint": _event_fingerprint(r),
                    "keys": sorted(r.keys()),
                }
            )
    return {
        "n_events": len(rows),
        "currencies_top": dict(sorted(currencies.items(), key=lambda x: -x[1])[:15]),
        "impacts": impacts,
        "countries_top": dict(sorted(countries.items(), key=lambda x: -x[1])[:15]),
        "target_currency_hits": target_hits,
        "usd_high_impact": high_usd,
        "sample_events": sample,
    }


def _probe_window(api_key: str, label: str, date_from: str, date_to: str) -> dict[str, Any]:
    qs = urllib.parse.urlencode(
        {"from": date_from, "to": date_to, "apikey": api_key}
    )
    url = f"{STABLE_CALENDAR}?{qs}"
    # Never log full URL with key
    safe_url = f"{STABLE_CALENDAR}?from={date_from}&to={date_to}&apikey=***"
    result = _get_json(url)
    out: dict[str, Any] = {
        "label": label,
        "from": date_from,
        "to": date_to,
        "url_safe": safe_url,
        "http_status": result.get("http_status"),
        "ok": bool(result.get("ok")),
    }
    if not result.get("ok"):
        out["error"] = result.get("error") or result.get("snippet")
        status = result.get("http_status")
        out["auth_failed"] = status in (401, 403)
        out["payment_required"] = status == 402
        out["summary"] = {"n_events": 0}
        return out
    data = result.get("data")
    if isinstance(data, dict) and "Error Message" in data:
        out["ok"] = False
        out["error"] = str(data.get("Error Message"))
        err_l = out["error"].lower()
        out["auth_failed"] = "key" in err_l or "auth" in err_l
        out["payment_required"] = "premium" in err_l or "upgrade" in err_l or "subscribe" in err_l
        out["summary"] = {"n_events": 0}
        return out
    if not isinstance(data, list):
        out["ok"] = False
        out["error"] = f"unexpected_body_type={type(data).__name__}"
        out["summary"] = {"n_events": 0}
        return out
    out["summary"] = _summarize_events(data)
    out["raw_bytes"] = result.get("raw_bytes")
    return out


def _verdict(probes: list[dict[str, Any]], has_key: bool) -> dict[str, str]:
    if not has_key:
        return {
            "FMP_FREE_PILOT": "FAIL_NO_KEY",
            "3Y_HISTORY": "NOT_TESTED",
            "LOOKAHEAD_SAFETY": "DOC_PARTIAL",
            "PAYMENT_RECOMMENDATION": "NO",
            "NEXT": "Add FMP_API_KEY to .env then re-run gate",
        }
    ok_probes = [p for p in probes if p.get("ok") and (p.get("summary") or {}).get("n_events", 0) > 0]
    auth_fails = [p for p in probes if p.get("auth_failed")]
    pay_fails = [p for p in probes if p.get("payment_required")]
    if pay_fails and not ok_probes:
        return {
            "FMP_FREE_PILOT": "FAIL_ENTITLEMENT_402",
            "3Y_HISTORY": "NOT_CONFIRMED",
            "LOOKAHEAD_SAFETY": "UNKNOWN",
            "PAYMENT_RECOMMENDATION": "NO_AUTO",
            "NEXT": "Free not entitled — report before any Starter (~$22) decision",
        }
    if auth_fails and not ok_probes:
        return {
            "FMP_FREE_PILOT": "FAIL_AUTH",
            "3Y_HISTORY": "NOT_CONFIRMED",
            "LOOKAHEAD_SAFETY": "FAIL",
            "PAYMENT_RECOMMENDATION": "NO",
            "NEXT": "Do not pay yet — inspect auth/entitlement error",
        }
    if not ok_probes:
        return {
            "FMP_FREE_PILOT": "FAIL_EMPTY",
            "3Y_HISTORY": "NOT_CONFIRMED",
            "LOOKAHEAD_SAFETY": "UNKNOWN",
            "PAYMENT_RECOMMENDATION": "NO",
            "NEXT": "Inspect empty responses / plan limits",
        }
    hist_ok = any(
        p.get("ok") and p.get("label", "").startswith("history_probe") for p in ok_probes
    )
    return {
        "FMP_FREE_PILOT": "PASS_SAMPLE",
        "3Y_HISTORY": "DOC_ONLY_NEED_PAGING" if hist_ok else "PARTIAL",
        "LOOKAHEAD_SAFETY": "PARTIAL_SCHEDULED_TIME_ONLY",
        "PAYMENT_RECOMMENDATION": "NO",
        "NEXT": "Analyze sample schema; still no definitive connector",
    }


def main() -> int:
    _load_dotenv(ROOT / ".env")
    api_key = (os.environ.get("FMP_API_KEY") or "").strip()
    has_key = bool(api_key)

    probes: list[dict[str, Any]] = []
    if has_key:
        for label, d0, d1 in WINDOWS:
            probes.append(_probe_window(api_key, label, d0, d1))

    verdict = _verdict(probes, has_key)
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "provider": "fmp",
        "product": "stable/economic-calendar",
        "key_present": has_key,
        "key_fingerprint": _mask_key(api_key) if has_key else None,
        "windows": probes,
        "verdict": verdict,
        "constraints": {
            "no_strategy_rule_changes": True,
            "no_oos": True,
            "no_real_orders": True,
            "no_definitive_connector": True,
            "quantgist_status": "BLOCKED_AUTH",
        },
        "docs_refs": [
            "https://site.financialmodelingprep.com/developer/docs/stable/economics-calendar",
            "https://financialmodelingprep.com/stable/economic-calendar",
            "https://site.financialmodelingprep.com/developer/docs/pricing",
        ],
    }

    SAMPLE_OUT.parent.mkdir(parents=True, exist_ok=True)
    SAMPLE_OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    lines = [
        "# ÉTAPE 5.1B — FMP Free Economic Calendar pilot gate",
        "",
        f"- Generated (UTC): `{payload['generated_at_utc']}`",
        f"- Key present: `{has_key}`",
        f"- Key fingerprint: `{payload['key_fingerprint']}`",
        "",
        "## Verdict",
        "",
    ]
    for k, v in verdict.items():
        lines.append(f"- **{k}**: `{v}`")
    lines.extend(["", "## Windows", ""])
    if not probes:
        lines.append("- No live probes (missing `FMP_API_KEY`).")
    for p in probes:
        n = (p.get("summary") or {}).get("n_events", 0)
        lines.append(
            f"- `{p['label']}` `{p['from']}`→`{p['to']}`: "
            f"http={p.get('http_status')} ok={p.get('ok')} n_events={n}"
        )
        if p.get("error"):
            err = str(p["error"]).replace(api_key, "***") if has_key else str(p["error"])
            lines.append(f"  - error: `{err[:200]}`")
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- Read-only pilot only — not the definitive NEWS connector.",
            "- FMP docs: max **~90 days** per `from`/`to` request; page for longer spans.",
            "- Free Basic entitlement for this endpoint is **unconfirmed** until live key "
            "(community reports of HTTP 402 on Free are possible — do not pay until proved).",
            "- No PIT vintage → blackout must use scheduled `date` only (no actual-release timing).",
            "- QuantGist remains `BLOCKED_AUTH` (not permanently dead; do not pay / no new keys now).",
            "- Prior QuantGist diagnostics preserved under `docs/ETAPE_5_1B_QUANTGIST_*`.",
            "",
        ]
    )
    REPORT_OUT.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"sample": str(SAMPLE_OUT), "report": str(REPORT_OUT), "verdict": verdict}, indent=2))
    return 0 if verdict.get("FMP_FREE_PILOT") == "PASS_SAMPLE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
