#!/usr/bin/env python3
"""ÉTAPE 5.1 — write NEWS alternative comparison docs + unauth probes."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trading_signal_bot.data.news_alt_evaluation import (  # noqa: E402
    comparison_payload,
    recommendation,
    run_unauth_probes,
)

OUT_JSON = ROOT / "docs" / "ETAPE_5_1_NEWS_ALT_COMPARISON.json"
OUT_MD = ROOT / "docs" / "ETAPE_5_1_NEWS_ALT_COMPARISON.md"
OUT_QG_STATUS = ROOT / "docs" / "ETAPE_5_1B_QUANTGIST_STATUS.md"


def _md(payload: dict) -> str:
    rec = payload["recommendation"]
    lines = [
        "# ÉTAPE 5.1 — NEWS GATE provider alternatives",
        "",
        f"- Generated (UTC): `{payload['generated_at_utc']}`",
        "- Goal: free / cheapest provider for FX/XAU news blackout calendar (backtest-safe).",
        "- Constraints: no QuantGist/FMP auto-pay, no QG key regen, no strategy changes, no OOS, no real orders.",
        "",
        "## Recommendation (ONE next pilot)",
        "",
        f"**Provider:** `{rec['recommended_provider_for_next_pilot']}` — {rec['product']}",
        "",
        "### Why",
        "",
    ]
    for w in rec["why"]:
        lines.append(f"- {w}")
    lines.extend(
        [
            "",
            "### Pilot plan",
            "",
            f"- Script: `{rec['pilot_plan']['script']}`",
            f"- Env: `{rec['pilot_plan']['required_env']}`",
            "- Documentary validation + small read-only pilot (same pattern as QuantGist).",
            "- **Do not** code the definitive connector yet.",
            "",
            "## Comparison matrix",
            "",
            "| Provider | Status | Cost pilot | History | TS | CCY/Country | Impact | Event id | Look-ahead | Depth |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
    )
    for r in payload["providers"]:
        lines.append(
            f"| `{r['provider_id']}` | `{r['status']}` | {r['cost_pilot']} | "
            f"{r['historical_calendar']} | {r['timestamps']} | {r['currency_country']} | "
            f"{r['impact']} | {r['event_name_id']} | {r['lookahead_avoidance']} | "
            f"{r['train_val_depth']} |"
        )
    lines.extend(["", "## Notes per provider", ""])
    for r in payload["providers"]:
        lines.extend(
            [
                f"### `{r['provider_id']}` — {r['product']}",
                "",
                f"- Fit: {r['fit_for_fx_xau_news_gate']}",
                f"- API stability: {r['api_stability']}",
                f"- Notes: {r['notes']}",
                "",
            ]
        )
    probes = payload.get("unauthenticated_probes") or {}
    lines.extend(["## Unauthenticated probes (evidence)", ""])
    for k, v in probes.items():
        if k in ("probed_at_utc", "note"):
            continue
        if isinstance(v, dict):
            lines.append(
                f"- `{k}`: http={v.get('http_status')} ok={v.get('ok')} "
                f"n_items={v.get('n_items')} snippet=`{(v.get('snippet') or v.get('error') or '')[:120]}`"
            )
    if probes.get("note"):
        lines.extend(["", probes["note"], ""])
    lines.extend(
        [
            "",
            "## News map (unchanged)",
            "",
            "```json",
            json.dumps(payload["news_map_unchanged"], indent=2),
            "```",
            "",
            "## Blocked / rejected (retained)",
            "",
            "- QuantGist: **BLOCKED_AUTH** — `docs/ETAPE_5_1B_QUANTGIST_*`",
            "- FMP Free: **BLOCKED_ENTITLEMENT** (402) — `docs/ETAPE_5_1B_FMP_*` — PAYMENT=NO_AUTO",
            "- EODHD: **REJECT_FREE_SHAPE_ENTITLEMENT** (demo 403, no impact field)",
            "",
        ]
    )
    return "\n".join(lines)


def _qg_status_md() -> str:
    return "\n".join(
        [
            "# ÉTAPE 5.1B — QuantGist status (post-validation)",
            "",
            "## Decision (user)",
            "",
            "1. Do **not** pay QuantGist.",
            "2. Do **not** regenerate other keys for now.",
            "3. Classify as **BLOCKED_AUTH** / provider non validé — **not** permanently unusable.",
            "4. Keep all results and diagnostics.",
            "5. Do not modify any strategy rules.",
            "6. No OOS access.",
            "7. No real orders.",
            "",
            "## Evidence retained",
            "",
            "- `docs/ETAPE_5_1B_QUANTGIST_FREE_PILOT_SAMPLE.json` — all calendar/events → HTTP 401",
            "- `docs/ETAPE_5_1B_QUANTGIST_FREE_PILOT_ANALYSIS.md`",
            "- `docs/ETAPE_5_1B_QUANTGIST_FREE_PILOT_GATE.md` (if present)",
            "- Scripts: `scripts/quantgist_free_pilot_gate.py`, `scripts/analyze_quantgist_free_pilot.py`",
            "",
            "## Live outcome summary",
            "",
            "- `calendar_range` = 401",
            "- `history_probe_2024` = 401",
            "- `events_backtest_safe` = 401",
            "- `n_events_total` = 0",
            "- Error class: `authentication_failed` / API key not found or has been revoked",
            "",
            "## Next",
            "",
            "- Continue ÉTAPE 5.1 with **FMP Free** as the next read-only pilot.",
            "- QuantGist may be re-evaluated later separately.",
            "",
        ]
    )


def main() -> int:
    probes = run_unauth_probes()
    payload = comparison_payload(probes)
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    OUT_MD.write_text(_md(payload), encoding="utf-8")
    OUT_QG_STATUS.write_text(_qg_status_md(), encoding="utf-8")
    print(
        json.dumps(
            {
                "json": str(OUT_JSON),
                "md": str(OUT_MD),
                "qg_status": str(OUT_QG_STATUS),
                "recommended": recommendation()["recommended_provider_for_next_pilot"],
                "probe_summary": {
                    k: (v.get("http_status") if isinstance(v, dict) else v)
                    for k, v in probes.items()
                    if k not in ("note",)
                },
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
