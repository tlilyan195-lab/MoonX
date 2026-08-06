#!/usr/bin/env python3
"""ÉTAPE 5.1B — validate Trading Economics + alternatives. No paid subscribe. No 3y OHLC."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from trading_signal_bot.data.news_provider_validation import assess_news_providers_5_1b


def main() -> int:
    report = assess_news_providers_5_1b(run_live_probe=True)
    out_dir = ROOT / "docs"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "ETAPE_5_1B_NEWS_PROVIDER.json"
    json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    te = report["trading_economics"]
    alts = report["alternatives"]
    rec = report["recommended_v1"]
    action = report["action_required"]

    lines = [
        "# ÉTAPE 5.1B — NEWS provider validation (Trading Economics)",
        "",
        "**STOP.** No 3y OHLC download. No TRAIN/VAL. No OOS. No SMC changes. No paid subscribe by agent.",
        "",
        "## 1. TRADING_ECONOMICS verdict: **REJECT**",
        "",
        f"- usable_free_for_pilot: `{te['usable_free_for_pilot']}`",
        f"- usable_for_3y_without_paid: `{te['usable_for_3y_without_paid']}`",
        f"- Live guest probe status: `{te['probe'].get('guest_status')}` (discontinued)",
        "",
        "### Reasons",
        "",
    ]
    for r in te["reasons"]:
        lines.append(f"- {r}")

    lines += [
        "",
        "## 2. Historical depth (TE)",
        "",
        te["historical_depth"],
        "",
        "## 3. Fields (TE)",
        "",
        "| Need | TE |",
        "|---|---|",
    ]
    for k, v in te["fields"].items():
        lines.append(f"| {k} | {v} |")

    lines += [
        "",
        "## 4. Plan / cost (TE)",
        "",
        f"```json\n{json.dumps(te['plan_cost'], indent=2)}\n```",
        "",
        "## 5. API limits (TE)",
        "",
        f"```json\n{json.dumps(te['api_limits'], indent=2)}\n```",
        "",
        f"- Pagination: {te['pagination']}",
        f"- Timezone: {te['timezone']}",
        f"- Event IDs: {te['event_ids']}",
        f"- Immutable snapshot: {te['immutable_snapshot']}",
        f"- No look-ahead: {te['no_lookahead']}",
        "",
        "## 6. Two alternatives (TE rejected for free/pilot)",
        "",
    ]
    for alt in alts:
        lines += [
            f"### {alt['provider']} — `{alt['verdict']}`",
            "",
            f"- Depth: {alt['historical_depth']}",
            f"- Plan for 3y: `{alt['plan_cost'].get('plan_needed_for_3y')}`",
            f"- Free pilot usable: `{alt['usable_free_for_pilot']}`",
            f"- Fields: {alt['fields']}",
            f"- Limits: {alt['api_limits']}",
            "",
        ]

    lines += [
        "## 7. Recommended NEWS provider for V1",
        "",
        f"**{rec['provider']}** — plan `{rec['plan_for_v1_3y']}`",
        "",
        rec["why"],
        "",
        f"- Runner-up: {rec['runner_up']}",
        f"- Second alt: {rec['second_alternative']}",
        "",
        "## 8. Action required from you",
        "",
        f"- **Now:** {action['now']}",
        f"- **Before 3y news snapshot:** {action['before_3y_news_snapshot']}",
        "",
        "Do not:",
        "",
    ]
    for d in action["do_not"]:
        lines.append(f"- {d}")

    lines += [
        "",
        "## News currency map (unchanged CDC blackout rules)",
        "",
        "```json",
        json.dumps(report["news_currency_map"], indent=2),
        "```",
        "",
        "BTCUSDT/ETHUSDT: no macro FX news gate in V1.",
        "",
        "Unexpected OHLC gaps remain explicit (no interpolation).",
        "",
    ]
    md_path = out_dir / "ETAPE_5_1B_NEWS_PROVIDER.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"TE verdict: {te['verdict']}", flush=True)
    print(f"Recommended: {rec['provider']} / {rec['plan_for_v1_3y']}", flush=True)
    print(f"Wrote {json_path}", flush=True)
    print(f"Wrote {md_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
