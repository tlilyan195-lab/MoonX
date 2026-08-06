#!/usr/bin/env python3
"""
Analyze QuantGist FREE pilot sample JSON (no secrets).

Does NOT treat Free-plan reach as 3y confirmation.
Expects: docs/ETAPE_5_1B_QUANTGIST_FREE_PILOT_SAMPLE.json
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "docs" / "ETAPE_5_1B_QUANTGIST_FREE_PILOT_SAMPLE.json"
OUT_JSON = ROOT / "docs" / "ETAPE_5_1B_QUANTGIST_FREE_PILOT_ANALYSIS.json"
OUT_MD = ROOT / "docs" / "ETAPE_5_1B_QUANTGIST_FREE_PILOT_ANALYSIS.md"

HIGH_IMPACT_ALIASES = {"high", "3", "High", "HIGH"}
NEEDED_CURRENCIES = {"EUR", "USD", "GBP", "JPY"}
SCHEMA_FIELDS = (
    "id",
    "event_id",
    "canonical_id",
    "release_time",
    "date",
    "timestamp",
    "time",
    "timezone",
    "tz",
    "currency",
    "country",
    "event",
    "title",
    "name",
    "impact",
    "importance",
    "impact_score",
    "first_print",
    "revision_seq",
    "actual",
    "forecast",
    "previous",
)


def _as_list(body: Any) -> list[dict]:
    if body is None:
        return []
    if isinstance(body, list):
        return [x for x in body if isinstance(x, dict)]
    if isinstance(body, dict):
        for key in ("data", "items", "events", "results", "calendar"):
            if isinstance(body.get(key), list):
                return [x for x in body[key] if isinstance(x, dict)]
        # single event object
        if any(k in body for k in ("title", "event", "release_time", "date", "currency")):
            return [body]
    return []


def _pick(ev: dict, *keys: str) -> Any:
    for k in keys:
        if k in ev and ev[k] not in (None, ""):
            return ev[k]
    return None


def analyze(sample: dict) -> dict[str, Any]:
    sections: dict[str, Any] = {}
    all_events: list[dict] = []

    for name, block in sample.items():
        if not isinstance(block, dict):
            sections[name] = {"error": "unexpected_block_shape", "raw_type": type(block).__name__}
            continue
        status = block.get("status")
        body = block.get("body")
        events = _as_list(body)
        field_counter: Counter[str] = Counter()
        for ev in events:
            field_counter.update(ev.keys())
            all_events.append({**ev, "_section": name})
        sections[name] = {
            "http_status": status,
            "n_events": len(events),
            "top_level_body_keys": sorted(body.keys()) if isinstance(body, dict) else None,
            "event_field_union": sorted(field_counter.keys()),
            "event_field_counts": dict(field_counter),
            "sample_event": events[0] if events else None,
            "ok_http": status == 200,
        }

    # Schema presence across events
    present_fields = set()
    for ev in all_events:
        present_fields |= set(ev.keys()) - {"_section"}

    schema_check = {
        f: ("PRESENT" if f in present_fields else "ABSENT") for f in SCHEMA_FIELDS
    }

    # Normalize extracts
    currencies: Counter[str] = Counter()
    countries: Counter[str] = Counter()
    impacts: Counter[str] = Counter()
    timestamps: list[str] = []
    ids: list[str] = []
    titles: list[str] = []
    first_print_vals: list[Any] = []
    revision_vals: list[Any] = []

    for ev in all_events:
        cur = _pick(ev, "currency", "Currency")
        if cur:
            currencies[str(cur).upper()] += 1
        ctry = _pick(ev, "country", "Country")
        if ctry:
            countries[str(ctry)] += 1
        imp = _pick(ev, "impact", "importance", "Importance")
        if imp is not None:
            impacts[str(imp)] += 1
        ts = _pick(ev, "release_time", "date", "timestamp", "time", "Date")
        if ts is not None:
            timestamps.append(str(ts))
        eid = _pick(ev, "id", "event_id", "canonical_id", "CalendarId")
        if eid is not None:
            ids.append(str(eid))
        title = _pick(ev, "title", "event", "Event", "name")
        if title is not None:
            titles.append(str(title))
        if "first_print" in ev:
            first_print_vals.append(ev.get("first_print"))
        if "revision_seq" in ev:
            revision_vals.append(ev.get("revision_seq"))

    high_events = [
        ev
        for ev in all_events
        if str(_pick(ev, "impact", "importance") or "").lower() in {"high", "3"}
    ]
    high_by_ccy: dict[str, int] = {c: 0 for c in sorted(NEEDED_CURRENCIES)}
    for ev in high_events:
        cur = str(_pick(ev, "currency") or "").upper()
        if cur in high_by_ccy:
            high_by_ccy[cur] += 1

    # Timestamp / TZ heuristics
    tz_notes = []
    if timestamps:
        z_count = sum(1 for t in timestamps if t.endswith("Z") or "+00:00" in t or t.endswith("+0000"))
        tz_notes.append(f"n_timestamps={len(timestamps)}")
        tz_notes.append(f"looks_utc_suffix={z_count}/{len(timestamps)}")
        # bare 'YYYY-MM-DD HH:MM:SS' without offset → ambiguous
        bare = sum(
            1
            for t in timestamps
            if "T" not in t and "+" not in t and not t.endswith("Z") and " " in t
        )
        tz_notes.append(f"naive_space_datetime={bare}")

    oldest = min(timestamps) if timestamps else None
    newest = max(timestamps) if timestamps else None

    # Pagination signals in bodies
    pagination = {}
    for name, block in sample.items():
        body = block.get("body") if isinstance(block, dict) else None
        if isinstance(body, dict):
            pagination[name] = {
                k: body.get(k)
                for k in (
                    "page",
                    "per_page",
                    "total",
                    "next_cursor",
                    "cursor",
                    "has_more",
                    "limit",
                )
                if k in body
            }

    # History probe section (gate uses history_probe_2024)
    hist = sections.get("history_probe_2024") or sections.get("history_probe") or {}
    history_api = {
        "probe_http_status": hist.get("http_status"),
        "probe_n_events": hist.get("n_events"),
        "oldest_timestamp_in_sample": oldest,
        "newest_timestamp_in_sample": newest,
        "three_year_confirmed_by_api": False,  # Free pilot must never claim this
        "note": (
            "Free pilot sample must NOT be treated as 3y confirmation. "
            "3y remains DOC_ONLY until Starter (or equivalent) is API-verified."
        ),
    }

    lookahead = {
        "backtest_safe_section_present": "events_backtest_safe" in sample,
        "backtest_safe_http": (sections.get("events_backtest_safe") or {}).get("http_status"),
        "backtest_safe_n_events": (sections.get("events_backtest_safe") or {}).get("n_events"),
        "first_print_field_seen": "first_print" in present_fields,
        "first_print_values_sample": first_print_vals[:10],
        "revision_seq_field_seen": "revision_seq" in present_fields,
        "revision_seq_values_sample": revision_vals[:10],
        "verdict": "UNCONFIRMED",
    }
    if lookahead["backtest_safe_http"] == 200 and lookahead["backtest_safe_n_events"]:
        if lookahead["first_print_field_seen"] or lookahead["backtest_safe_n_events"] > 0:
            lookahead["verdict"] = "PASS_PARTIAL"
            lookahead["note"] = (
                "backtest_safe endpoint returned rows on Free; still verify that blackout "
                "uses only release_time known pre-trade and ignores revised actuals."
            )
    elif lookahead["backtest_safe_http"] in (401, 402, 403):
        lookahead["verdict"] = "FAIL"
        lookahead["note"] = "backtest_safe not available on this plan/key."
    else:
        lookahead["note"] = "Insufficient evidence in sample for look-ahead safety."

    currencies_ok = NEEDED_CURRENCIES.issubset(set(currencies)) or any(
        high_by_ccy[c] > 0 for c in NEEDED_CURRENCIES
    )
    # Prefer high-impact coverage check
    high_coverage = {c: high_by_ccy[c] > 0 for c in sorted(NEEDED_CURRENCIES)}

    free_pilot = "PASS" if any(s.get("ok_http") and s.get("n_events", 0) > 0 for s in sections.values()) else "FAIL"

    # Critical payment criteria (Free cannot satisfy 3y)
    payment = {
        "history_ge_3y_api_confirmed": False,
        "eur_usd_gbp_jpy_high_impact": all(high_coverage.values()),
        "timestamp_reliable": bool(timestamps) and ("looks_utc_suffix=" in ";".join(tz_notes)),
        "impact_exploitable": bool(impacts),
        "lookahead_ok": lookahead["verdict"] in ("PASS", "PASS_PARTIAL"),
        "cost_le_25": True,  # Free=$0; Starter doc $19 — not paying yet
        "PAYMENT_RECOMMENDATION": "NO",
        "why": (
            "Free pilot must not unlock payment. 3y history not API-confirmed on Free. "
            "Recommend payment only after Starter (or ≤$25 plan) API proves ≥3y depth "
            "plus high-impact EUR/USD/GBP/JPY and look-ahead-safe reconstruction."
        ),
    }
    if not payment["eur_usd_gbp_jpy_high_impact"]:
        payment["why"] += " High-impact coverage incomplete on this sample."
    if lookahead["verdict"] not in ("PASS", "PASS_PARTIAL"):
        payment["why"] += " Look-ahead safety not confirmed."

    return {
        "QUANTGIST_FREE_PILOT": free_pilot,
        "3Y_HISTORY": "DOC_ONLY",
        "LOOKAHEAD_SAFETY": lookahead["verdict"],
        "STARTER_PRICE_DOC": "$19/month (https://quantgist.com/pricing.md); JSON-LD conflict $29 on /terms",
        "PAYMENT_RECOMMENDATION": payment["PAYMENT_RECOMMENDATION"],
        "sections": sections,
        "schema_fields": schema_check,
        "present_fields": sorted(present_fields),
        "currencies": dict(currencies),
        "countries": dict(countries),
        "impacts": dict(impacts),
        "high_impact_by_currency": high_by_ccy,
        "high_impact_coverage": high_coverage,
        "timestamp_notes": tz_notes,
        "oldest_ts": oldest,
        "newest_ts": newest,
        "event_ids_sample": ids[:15],
        "titles_sample": titles[:15],
        "pagination": pagination,
        "history_api": history_api,
        "lookahead": lookahead,
        "payment_gate": payment,
        "n_events_total": len(all_events),
        "currencies_needed_subset_seen": currencies_ok,
    }


def render_md(rep: dict) -> str:
    lines = [
        "# ÉTAPE 5.1B′ — QuantGist FREE pilot analysis",
        "",
        "**Free ≠ 3y confirmation.**",
        "",
        f"- QUANTGIST_FREE_PILOT = **{rep['QUANTGIST_FREE_PILOT']}**",
        f"- 3Y_HISTORY = **{rep['3Y_HISTORY']}**",
        f"- LOOKAHEAD_SAFETY = **{rep['LOOKAHEAD_SAFETY']}**",
        f"- STARTER_PRICE (doc) = {rep['STARTER_PRICE_DOC']}",
        f"- PAYMENT_RECOMMENDATION = **{rep['PAYMENT_RECOMMENDATION']}**",
        "",
        "## Sections (HTTP / counts)",
        "",
    ]
    for name, sec in rep["sections"].items():
        lines.append(
            f"- `{name}`: status={sec.get('http_status')} n_events={sec.get('n_events')} "
            f"fields={sec.get('event_field_union')}"
        )
    lines += [
        "",
        "## Schema field presence",
        "",
        "| Field | Status |",
        "|---|---|",
    ]
    for k, v in rep["schema_fields"].items():
        lines.append(f"| `{k}` | {v} |")
    lines += [
        "",
        "## High-impact coverage (sample)",
        "",
        f"```json\n{json.dumps(rep['high_impact_by_currency'], indent=2)}\n```",
        "",
        f"Timestamps: oldest=`{rep['oldest_ts']}` newest=`{rep['newest_ts']}` ({rep['timestamp_notes']})",
        "",
        "## History",
        "",
        f"```json\n{json.dumps(rep['history_api'], indent=2)}\n```",
        "",
        "## Look-ahead",
        "",
        f"```json\n{json.dumps(rep['lookahead'], indent=2)}\n```",
        "",
        "## Payment gate",
        "",
        f"```json\n{json.dumps(rep['payment_gate'], indent=2)}\n```",
        "",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    if not SAMPLE.exists():
        print(f"MISSING sample: {SAMPLE}")
        print(
            "Cloud workspace cannot see your local Windows path. "
            "Copy only the sample JSON into docs/ (never .env) and re-run."
        )
        missing = {
            "QUANTGIST_FREE_PILOT": "BLOCKED_NO_SAMPLE_IN_REPO",
            "3Y_HISTORY": "DOC_ONLY",
            "LOOKAHEAD_SAFETY": "UNCONFIRMED",
            "PAYMENT_RECOMMENDATION": "NO",
            "action": (
                "From your local moonx folder: copy "
                "docs/ETAPE_5_1B_QUANTGIST_FREE_PILOT_SAMPLE.json into the git repo, "
                "commit/push that file only (no .env), then tell the agent to continue."
            ),
        }
        OUT_JSON.write_text(json.dumps(missing, indent=2), encoding="utf-8")
        OUT_MD.write_text(
            "# QuantGist FREE pilot — sample missing in cloud repo\n\n"
            + json.dumps(missing, indent=2)
            + "\n",
            encoding="utf-8",
        )
        return 2
    sample = json.loads(SAMPLE.read_text(encoding="utf-8"))
    # Strip accidental secrets
    blob = json.dumps(sample)
    if "qg_live_" in blob or "qg_test_" in blob:
        print("REFUSING: sample appears to contain an API key. Remove it and retry.")
        return 3
    rep = analyze(sample)
    OUT_JSON.write_text(json.dumps(rep, indent=2, default=str), encoding="utf-8")
    OUT_MD.write_text(render_md(rep), encoding="utf-8")
    print(f"QUANTGIST_FREE_PILOT = {rep['QUANTGIST_FREE_PILOT']}")
    print(f"3Y_HISTORY = {rep['3Y_HISTORY']}")
    print(f"LOOKAHEAD_SAFETY = {rep['LOOKAHEAD_SAFETY']}")
    print(f"PAYMENT_RECOMMENDATION = {rep['PAYMENT_RECOMMENDATION']}")
    print(f"Wrote {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
