#!/usr/bin/env python3
"""Analyze FMP Free pilot sample already in repo. No network. No secrets printed."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "docs" / "ETAPE_5_1B_FMP_FREE_PILOT_SAMPLE.json"
OUT_JSON = ROOT / "docs" / "ETAPE_5_1B_FMP_FREE_PILOT_ANALYSIS.json"
OUT_MD = ROOT / "docs" / "ETAPE_5_1B_FMP_FREE_PILOT_ANALYSIS.md"
OUT_STATUS = ROOT / "docs" / "ETAPE_5_1B_FMP_STATUS.md"


def main() -> int:
    if not SAMPLE.is_file():
        print("FAIL: sample missing")
        return 1
    sample = json.loads(SAMPLE.read_text(encoding="utf-8"))
    verdict = sample.get("verdict") or {}
    windows = sample.get("windows") or []
    statuses = [w.get("http_status") for w in windows]
    n_total = sum((w.get("summary") or {}).get("n_events", 0) for w in windows)
    all_402 = bool(windows) and all(s == 402 for s in statuses)
    pay = all(bool(w.get("payment_required")) for w in windows) if windows else False

    rep = {
        "analyzed_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sample_generated_at_utc": sample.get("generated_at_utc"),
        "key_present": sample.get("key_present"),
        "key_fingerprint": sample.get("key_fingerprint"),
        "FMP_FREE_PILOT": verdict.get("FMP_FREE_PILOT"),
        "PROVIDER_STATUS": "BLOCKED_ENTITLEMENT",
        "3Y_HISTORY": verdict.get("3Y_HISTORY"),
        "LOOKAHEAD_SAFETY": verdict.get("LOOKAHEAD_SAFETY"),
        "PAYMENT_RECOMMENDATION": verdict.get("PAYMENT_RECOMMENDATION") or "NO_AUTO",
        "http_statuses": statuses,
        "all_windows_402": all_402,
        "payment_required_all": pay,
        "n_events_total": n_total,
        "error_class": "restricted_endpoint_subscription",
        "error_sample": (windows[0].get("error") if windows else None),
        "schema_events_received": False,
        "why": (
            "Live Free key authenticated (not 401) but /stable/economic-calendar is "
            "Restricted Endpoint (HTTP 402) on all pilot windows. Zero events. "
            "Do not auto-pay Starter. Classify BLOCKED_ENTITLEMENT / non validé Free. "
            "Continue ÉTAPE 5.1 with next no-pay alternative (JBlanked)."
        ),
        "next_no_pay_provider": "jblanked",
        "constraints": sample.get("constraints"),
    }
    OUT_JSON.write_text(json.dumps(rep, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    md = "\n".join(
        [
            "# ÉTAPE 5.1B — FMP Free pilot analysis",
            "",
            f"- Sample: `{SAMPLE.relative_to(ROOT)}` ({sample.get('generated_at_utc')})",
            f"- Key present: `{sample.get('key_present')}` fingerprint `{sample.get('key_fingerprint')}`",
            "",
            "## Verdict (confirmed)",
            "",
            f"- FMP_FREE_PILOT = **{rep['FMP_FREE_PILOT']}**",
            f"- PROVIDER_STATUS = **{rep['PROVIDER_STATUS']}** / Free non validé (not permanently dead if paid later)",
            f"- 3Y_HISTORY = **{rep['3Y_HISTORY']}**",
            f"- LOOKAHEAD_SAFETY = **{rep['LOOKAHEAD_SAFETY']}**",
            f"- PAYMENT_RECOMMENDATION = **{rep['PAYMENT_RECOMMENDATION']}**",
            f"- n_events_total = **{n_total}**",
            "",
            "## Evidence",
            "",
        ]
    )
    for w in windows:
        md += (
            f"- `{w.get('label')}` `{w.get('from')}`→`{w.get('to')}`: "
            f"http={w.get('http_status')} payment_required={w.get('payment_required')} "
            f"n_events={(w.get('summary') or {}).get('n_events', 0)}\n"
        )
    md += (
        "\nError (all windows): `Restricted Endpoint: This endpoint is not available "
        "under your current subscription…`\n\n"
        "## Interpretation\n\n"
        "- Auth succeeded enough to hit entitlement (402 ≠ revoked key).\n"
        "- Free Basic does **not** entitle Economic Calendar — matches prior risk note.\n"
        "- No schema/fields/history/look-ahead can be validated from this sample.\n"
        "- **Do not** auto-upgrade to FMP Starter (~$22/mo).\n"
        "- QuantGist remains `BLOCKED_AUTH`. FMP becomes `BLOCKED_ENTITLEMENT`.\n"
        "- Next no-pay pilot: **JBlanked** Calendar API (MQL5 source).\n"
    )
    OUT_MD.write_text(md, encoding="utf-8")

    status = "\n".join(
        [
            "# ÉTAPE 5.1B — FMP status (post Free pilot)",
            "",
            "## Decision",
            "",
            "1. Confirm repo sample: **FAIL_ENTITLEMENT_402** on all windows.",
            "2. **PAYMENT_RECOMMENDATION = NO_AUTO** — do not pay FMP Starter now.",
            "3. Classify as **BLOCKED_ENTITLEMENT** / Free non validé (paid path may work later).",
            "4. Keep all diagnostics (`ETAPE_5_1B_FMP_*`).",
            "5. No strategy rule changes. No OOS. No real orders.",
            "",
            "## Evidence retained",
            "",
            "- `docs/ETAPE_5_1B_FMP_FREE_PILOT_SAMPLE.json`",
            "- `docs/ETAPE_5_1B_FMP_FREE_PILOT_GATE.md`",
            "- `docs/ETAPE_5_1B_FMP_FREE_PILOT_ANALYSIS.md`",
            "",
            "## Next",
            "",
            "- Continue ÉTAPE 5.1 with **JBlanked** free Calendar API read-only pilot.",
            "- FMP may be re-evaluated later only if a paid decision is explicitly approved.",
            "",
        ]
    )
    OUT_STATUS.write_text(status, encoding="utf-8")
    print(json.dumps({k: rep[k] for k in (
        "FMP_FREE_PILOT", "PROVIDER_STATUS", "PAYMENT_RECOMMENDATION",
        "n_events_total", "next_no_pay_provider",
    )}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
