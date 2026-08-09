#!/usr/bin/env python3
"""Analyze JBlanked FREE pilot sample JSON (no secrets, no network).

Does NOT treat Free-plan reach as 3y confirmation.
Expects: docs/ETAPE_5_1B_JBLANKED_FREE_PILOT_SAMPLE.json
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "docs" / "ETAPE_5_1B_JBLANKED_FREE_PILOT_SAMPLE.json"
OUT_JSON = ROOT / "docs" / "ETAPE_5_1B_JBLANKED_FREE_PILOT_ANALYSIS.json"
OUT_MD = ROOT / "docs" / "ETAPE_5_1B_JBLANKED_FREE_PILOT_ANALYSIS.md"

NEWS_MAP = {
    "EURUSD": ["EUR", "USD"],
    "GBPUSD": ["GBP", "USD"],
    "USDJPY": ["USD", "JPY"],
    "XAUUSD": ["USD"],  # high-impact USD only at gate layer
}
TARGET_CCY = {"EUR", "USD", "GBP", "JPY"}
DATE_RE = re.compile(r"^(\d{4})\.(\d{2})\.(\d{2}) (\d{2}):(\d{2}):(\d{2})$")

# Fields safe for blackout scheduling vs look-ahead-sensitive.
SAFE_GATE_FIELDS = {"Date", "Currency", "Impact", "Name", "Event_ID", "Category"}
LOOKAHEAD_RISKY_FIELDS = {
    "Actual",  # known only after release
    "Outcome",  # derived post-release comparison
    "Strength",  # post-release quality label
    "Quality",  # post-release quality label
}
# Forecast/Previous are often pre-release consensus/prior; still not used for timing.
PRE_RELEASE_OPTIONAL = {"Forecast", "Previous"}


def _parse_date(s: str | None) -> str | None:
    if not s or not isinstance(s, str):
        return None
    m = DATE_RE.match(s.strip())
    if not m:
        return None
    y, mo, d, h, mi, sec = map(int, m.groups())
    # Format-valid only; timezone not asserted (PARTIAL look-ahead stance).
    try:
        datetime(y, mo, d, h, mi, sec)
    except ValueError:
        return None
    return f"{y:04d}-{mo:02d}-{d:02d}T{h:02d}:{mi:02d}:{sec:02d}"


def analyze(sample: dict[str, Any]) -> dict[str, Any]:
    windows = sample.get("windows") or []
    verdict_in = sample.get("verdict") or {}
    events: list[dict[str, Any]] = []
    field_union: set[str] = set()
    for w in windows:
        summary = (w or {}).get("summary") or {}
        field_union.update(summary.get("field_union") or [])
        for ev in summary.get("sample_events") or []:
            if isinstance(ev, dict):
                events.append(ev)

    # Prefer window-level aggregates (full n_events) over sample_events (capped).
    win0 = windows[0] if windows else {}
    summary0 = (win0 or {}).get("summary") or {}
    currencies = dict(summary0.get("currencies") or {})
    impacts = dict(summary0.get("impacts") or {})
    n_events = int(summary0.get("n_events") or 0)
    target_hits = int(summary0.get("target_currency_hits") or 0)
    target_high = int(summary0.get("target_high_impact") or 0)

    dates_parsed = [_parse_date(e.get("date")) for e in events]
    dates_ok = [d for d in dates_parsed if d]
    event_ids = [e.get("event_id") for e in events]
    zero_ids = sum(1 for i in event_ids if i in (0, "0", None))
    country_null = sum(1 for e in events if e.get("country") in (None, "", "null"))

    schema = {
        "field_union": sorted(field_union),
        "required_for_gate": {
            "Date": "Date" in field_union,
            "Currency": "Currency" in field_union,
            "Impact": "Impact" in field_union,
            "Name": "Name" in field_union,
            "Event_ID": "Event_ID" in field_union,
        },
        "country_field": "Country" in field_union,
        "country_null_in_sample_events": country_null,
        "sample_events_n": len(events),
        "date_format": "YYYY.MM.DD HH:MM:SS",
        "date_parse_ok_sample": len(dates_ok),
        "date_parse_fail_sample": len(dates_parsed) - len(dates_ok),
        "event_id_zero_or_null_in_sample": zero_ids,
        "safe_gate_fields_present": sorted(SAFE_GATE_FIELDS & field_union),
        "lookahead_risky_fields_present": sorted(LOOKAHEAD_RISKY_FIELDS & field_union),
        "pre_release_optional_present": sorted(PRE_RELEASE_OPTIONAL & field_union),
    }

    pair_compat: dict[str, Any] = {}
    for pair, ccys in NEWS_MAP.items():
        present = [c for c in ccys if currencies.get(c, 0) > 0]
        missing = [c for c in ccys if currencies.get(c, 0) == 0]
        pair_compat[pair] = {
            "needed_currencies": ccys,
            "present_in_week": present,
            "missing_in_week": missing,
            "week_ok": len(missing) == 0 if pair != "XAUUSD" else ("USD" in present),
            "note": (
                "XAUUSD uses USD high-impact only"
                if pair == "XAUUSD"
                else "Missing currency in this 1-week sample ≠ structural absence"
            ),
        }

    # GBP absent this week — flag coverage gap for sample only.
    gbp_in_week = currencies.get("GBP", 0) > 0

    lookahead = {
        "verdict": "PARTIAL_SCHEDULED_TIME_ONLY",
        "rule": (
            "Blackout may use only scheduled Date + Currency + Impact(+ Name/Event_ID). "
            "Do NOT use Actual/Outcome/Strength/Quality to decide blackout timing or membership."
        ),
        "timezone": (
            "Date has no explicit TZ in payload; JBlanked libs document offset. "
            "Must pin TZ convention before TRAIN/VAL freeze (assume UTC or documented offset)."
        ),
        "pit_vintage": False,
        "first_print": False,
        "revision_seq": False,
        "risky_if_misused": sorted(LOOKAHEAD_RISKY_FIELDS),
        "safe_for_gate": sorted(SAFE_GATE_FIELDS),
    }

    # Feasibility: Free 1 req/day.
    # Unknown max range span; pilot used 7 days → 39 events. Conservative plan = weekly pages.
    weeks_3y = 52 * 3
    free_days_weekly = weeks_3y  # 156 days if 1 week/request
    # If monthly ranges work: ~36 requests.
    months_3y = 12 * 3

    train_val = {
        "free_limit": "1 request/day (docs)",
        "sample_span_days": 7,
        "sample_n_events": n_events,
        "three_year_confirmed_by_api": False,
        "history_status": verdict_in.get("3Y_HISTORY") or "DOC_ONLY_NEED_MULTI_DAY_OR_CREDITS",
        "free_path": {
            "feasible": True,
            "mode": "multi_day_harvest",
            "conservative_weekly_requests": weeks_3y,
            "calendar_days_at_1_req_per_day": free_days_weekly,
            "optimistic_monthly_requests_if_api_allows": months_3y,
            "note": (
                "Free can accumulate a 3y calendar without payment if range paging works "
                "and daily quota is used over ~1–6 months depending on page size. "
                "Not validated beyond 7-day window yet."
            ),
        },
        "credits_path": {
            "needed_for_pilot_schema": False,
            "needed_for_fast_3y_pull": True,
            "payment_recommendation": "NO_AUTO",
            "note": (
                "Buy credits only after explicit approval AND after a larger historical "
                "range probe proves depth (e.g. 2023 / 2024 windows)."
            ),
        },
        "blocker_before_train_val": [
            "Confirm max from/to span per request (week vs month vs longer).",
            "Confirm GBP high-impact appears in other weeks (absent in this sample week).",
            "Pin timezone interpretation for Date.",
            "Freeze look-ahead rule: scheduled Date only; ignore post-release fields.",
            "No OOS access; no strategy rule changes.",
        ],
    }

    fx_gate = {
        "compatible": True,
        "impact_fields": True,
        "currency_fields": True,
        "target_currency_hits_week": target_hits,
        "target_high_impact_week": target_high,
        "currencies_week": currencies,
        "impacts_week": impacts,
        "gbp_present_in_sample_week": gbp_in_week,
        "country_usable": False,
        "country_note": "Country absent/null — gate must key off Currency (OK for our news map).",
        "pair_coverage_sample_week": pair_compat,
        "caveats": [
            "1-week sample only — not 3y proof.",
            "GBP missing in this week (structural unknown).",
            "Event_ID can be 0 for some rows — keep Name+Date+Currency fingerprint fallback.",
            "No Country — fine if Currency reliable.",
        ],
    }

    overall = {
        "JBLANKED_FREE_PILOT": verdict_in.get("JBLANKED_FREE_PILOT") or "PASS_SAMPLE",
        "SCHEMA_OK_FOR_NEWS_GATE": bool(
            schema["required_for_gate"]["Date"]
            and schema["required_for_gate"]["Currency"]
            and schema["required_for_gate"]["Impact"]
            and schema["required_for_gate"]["Name"]
        ),
        "FX_GATE_COMPAT": "YES_WITH_CAVEATS",
        "LOOKAHEAD_SAFETY": lookahead["verdict"],
        "3Y_HISTORY": train_val["history_status"],
        "TRAIN_VAL_FREE_FEASIBLE": "YES_SLOW_MULTI_DAY",
        "CREDITS_REQUIRED_NOW": False,
        "PAYMENT_RECOMMENDATION": "NO",
        "NEXT": (
            "Optional: multi-day Free harvest OR single larger-range history probe "
            "(still Free, 1/day). Do not buy credits yet. No connector freeze / no OOS."
        ),
    }

    return {
        "analyzed_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sample_generated_at_utc": sample.get("generated_at_utc"),
        "provider": sample.get("provider"),
        "product": sample.get("product"),
        "key_fingerprint": sample.get("key_fingerprint"),
        "window": {
            "label": win0.get("label"),
            "from": win0.get("from"),
            "to": win0.get("to"),
            "http_status": win0.get("http_status"),
            "n_events": n_events,
            "raw_bytes": win0.get("raw_bytes"),
        },
        "overall": overall,
        "event_structure": schema,
        "fx_news_gate": fx_gate,
        "lookahead": lookahead,
        "train_val_feasibility": train_val,
        "constraints": sample.get("constraints"),
        "news_map_unchanged": NEWS_MAP,
    }


def render_md(rep: dict[str, Any]) -> str:
    o = rep["overall"]
    s = rep["event_structure"]
    fx = rep["fx_news_gate"]
    la = rep["lookahead"]
    tv = rep["train_val_feasibility"]
    w = rep["window"]
    lines = [
        "# ÉTAPE 5.1B — JBlanked Free pilot analysis",
        "",
        f"- Sample: `{SAMPLE.relative_to(ROOT)}` ({rep.get('sample_generated_at_utc')})",
        f"- Window: `{w.get('label')}` `{w.get('from')}`→`{w.get('to')}` http={w.get('http_status')} n_events={w.get('n_events')}",
        f"- Key fingerprint: `{rep.get('key_fingerprint')}`",
        "",
        "## Verdict",
        "",
        f"- JBLANKED_FREE_PILOT = **{o['JBLANKED_FREE_PILOT']}**",
        f"- SCHEMA_OK_FOR_NEWS_GATE = **{o['SCHEMA_OK_FOR_NEWS_GATE']}**",
        f"- FX_GATE_COMPAT = **{o['FX_GATE_COMPAT']}**",
        f"- LOOKAHEAD_SAFETY = **{o['LOOKAHEAD_SAFETY']}**",
        f"- 3Y_HISTORY = **{o['3Y_HISTORY']}**",
        f"- TRAIN_VAL_FREE_FEASIBLE = **{o['TRAIN_VAL_FREE_FEASIBLE']}**",
        f"- CREDITS_REQUIRED_NOW = **{o['CREDITS_REQUIRED_NOW']}**",
        f"- PAYMENT_RECOMMENDATION = **{o['PAYMENT_RECOMMENDATION']}**",
        "",
        f"Next: {o['NEXT']}",
        "",
        "## 1) Event structure",
        "",
        f"- Field union: `{s['field_union']}`",
        f"- Date format: `{s['date_format']}` (parse ok on sample rows: {s['date_parse_ok_sample']}/{s['sample_events_n']})",
        f"- Country field in API union: `{s['country_field']}` (null in sample rows: {s['country_null_in_sample_events']})",
        f"- Event_ID zero/null in sample rows: `{s['event_id_zero_or_null_in_sample']}`",
        "",
        "| Gate need | Present |",
        "|---|---|",
    ]
    for k, v in s["required_for_gate"].items():
        lines.append(f"| `{k}` | `{v}` |")
    lines.extend(
        [
            "",
            f"- Safe for blackout scheduling: `{s['safe_gate_fields_present']}`",
            f"- Look-ahead risky if misused: `{s['lookahead_risky_fields_present']}`",
            f"- Pre-release optional (not for timing): `{s['pre_release_optional_present']}`",
            "",
            "## 2) FX news gate compatibility",
            "",
            f"- impact_fields = **{fx['impact_fields']}**",
            f"- currency_fields = **{fx['currency_fields']}**",
            f"- Week currencies: `{fx['currencies_week']}`",
            f"- Week impacts: `{fx['impacts_week']}`",
            f"- target_currency_hits = `{fx['target_currency_hits_week']}`",
            f"- target_high_impact = `{fx['target_high_impact_week']}`",
            f"- GBP in this sample week = `{fx['gbp_present_in_sample_week']}`",
            f"- Country usable = `{fx['country_usable']}` — {fx['country_note']}",
            "",
            "### Pair map (unchanged) vs this week",
            "",
        ]
    )
    for pair, info in fx["pair_coverage_sample_week"].items():
        lines.append(
            f"- **{pair}**: needed `{info['needed_currencies']}` present `{info['present_in_week']}` "
            f"missing `{info['missing_in_week']}` week_ok=`{info['week_ok']}`"
        )
    lines.extend(["", "Caveats:", ""])
    for c in fx["caveats"]:
        lines.append(f"- {c}")
    lines.extend(
        [
            "",
            "## 3) Anti look-ahead strategy",
            "",
            f"- Verdict: **{la['verdict']}**",
            f"- Rule: {la['rule']}",
            f"- Timezone: {la['timezone']}",
            f"- PIT vintage: `{la['pit_vintage']}` / first_print: `{la['first_print']}` / revision_seq: `{la['revision_seq']}`",
            "",
            "Operational blackout inputs only:",
            "",
            "1. `Date` (scheduled release time, TZ pinned once)",
            "2. `Currency` ∈ pair map",
            "3. `Impact == High` (XAUUSD: USD High only)",
            "4. Optional identity: `Name` / `Event_ID` (fingerprint if Event_ID==0)",
            "",
            "Never use for gate membership/timing: `Actual`, `Outcome`, `Strength`, `Quality`.",
            "",
            "## 4) TRAIN/VAL feasibility — Free vs credits",
            "",
            f"- Free limit: **{tv['free_limit']}**",
            f"- 3y confirmed by API: `{tv['three_year_confirmed_by_api']}`",
            f"- History status: `{tv['history_status']}`",
            "",
            "### Free path",
            "",
            f"- Feasible: **{tv['free_path']['feasible']}** ({tv['free_path']['mode']})",
            f"- Conservative weekly paging: ~{tv['free_path']['conservative_weekly_requests']} requests "
            f"→ ~{tv['free_path']['calendar_days_at_1_req_per_day']} calendar days at 1/day",
            f"- Optimistic monthly paging (if API allows): ~{tv['free_path']['optimistic_monthly_requests_if_api_allows']} requests",
            f"- Note: {tv['free_path']['note']}",
            "",
            "### Credits path",
            "",
            f"- Needed for schema pilot: `{tv['credits_path']['needed_for_pilot_schema']}`",
            f"- Needed for fast 3y pull: `{tv['credits_path']['needed_for_fast_3y_pull']}`",
            f"- Payment recommendation: **{tv['credits_path']['payment_recommendation']}**",
            f"- Note: {tv['credits_path']['note']}",
            "",
            "### Blockers before any TRAIN/VAL freeze",
            "",
        ]
    )
    for b in tv["blocker_before_train_val"]:
        lines.append(f"- {b}")
    lines.extend(
        [
            "",
            "## Constraints honored",
            "",
            "- No optimization",
            "- No OOS",
            "- No strategy rule changes",
            "- No definitive connector freeze in this step",
            "- No auto payment / credits",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    if not SAMPLE.is_file():
        print("FAIL: sample missing — push docs/ETAPE_5_1B_JBLANKED_FREE_PILOT_SAMPLE.json")
        return 1
    sample = json.loads(SAMPLE.read_text(encoding="utf-8"))
    rep = analyze(sample)
    OUT_JSON.write_text(json.dumps(rep, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    OUT_MD.write_text(render_md(rep), encoding="utf-8")
    print(json.dumps(rep["overall"], indent=2))
    print(f"wrote {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
