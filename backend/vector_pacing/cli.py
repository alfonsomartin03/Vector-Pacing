"""Command-line drafting review of .fit rides (GPS following check + within-rider power screen).

Usage::

    vector-pacing-review R01.fit R02.fit [--course course.gpx] [--json] [--output report.txt]

Screening output only: a qualified official must review all evidence.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .course import CourseError
from .drafting import DISCLAIMER, ScenarioError
from .drafting_api import DraftingSettingsIn, payload
from .review import run_fit_review


def _clock(start: str | None, t: float | None) -> str:
    if t is None:
        return "—"
    if start is None:
        return f"{t:.0f} s"
    base = datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone(UTC)
    return (base + timedelta(seconds=t)).strftime("%H:%M:%S UTC")


def _num(v: float | None, unit: str, nd: int = 0) -> str:
    return "unavailable" if v is None else f"{v:.{nd}f} {unit}"


def to_text(report: dict[str, Any]) -> str:
    sc, start = report["scenario"], report["scenario"]["start_time"]
    flagged = [e for e in report["events"] if e["classification"] in ("potential_drafting", "inconclusive")]
    power = report.get("power_screen") or {"anomalies": [], "riders": []}
    lines = [
        "VECTOR PACING — DRAFTING REVIEW",
        "Screening result only — not a finding of a drafting violation.",
        "",
        f"Riders: {len(sc['riders'])} | Course: {sc['course']['name']} ({sc['course']['length_m'] / 1000:.2f} km)"
        f" | Window: {_clock(start, sc['t_min_s'])} – {_clock(start, sc['t_max_s'])}",
    ]
    if sc["description"]:
        lines.append(sc["description"])
    counts = ", ".join(f"{k.replace('_', ' ')}: {v}" for k, v in sorted(report["counts"].items())) or "none"
    lines += ["", f"GPS FOLLOWING CHECK — intervals: {counts}"]
    for e in flagged:
        lines += [
            f"  {e['id']}  {e['follower']} behind {e['leader']} — {e['classification'].replace('_', ' ')}",
            f"       {_clock(start, e['start_s'])} – {_clock(start, e['end_s'])} ({e['duration_s']:.0f} s)"
            f" | gap {_num(e['gap_median_m'], 'm', 1)} ± {_num(e['sigma_median_m'], 'm', 1)}"
            f" | course {(e['course_start_m'] or 0) / 1000:.2f}–{(e['course_end_m'] or 0) / 1000:.2f} km"
            + (f" | power: {', '.join(e['power_anomaly_ids'])}" if e["power_anomaly_ids"] else ""),
        ]
    if not flagged:
        lines.append("  No potential drafting or inconclusive intervals.")
    lines += ["", f"POWER SCREEN (within rider) — anomalies: {len(power['anomalies'])}"]
    for a in power["anomalies"]:
        lines += [
            f"  {a['id']}  rider {a['rider']} — review priority {a['score']:.0%}",
            f"       {_clock(start, a['start_s'])} – {_clock(start, a['end_s'])} ({a['duration_s']:.0f} s)"
            f" | {_num(a['average_speed_kmh'], 'km/h', 1)} on {_num(a['average_power_w'], 'W')}"
            f" vs {_num(a['reference_power_w'], 'W')} in {a['peer_count']} comparable sections"
            + (f" | GPS: {', '.join(a['gps_event_ids'])}" if a["gps_event_ids"] else ""),
            f"       Location: {a['lat']:.6f}, {a['lon']:.6f}",
        ]
    for r in power["riders"]:
        for w in r["warnings"]:
            lines.append(f"  Note ({r['id']}): {w}")
    lines += ["", DISCLAIMER]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="vector-pacing-review",
        description="Screen .fit rides for potential drafting (GPS following check + within-rider power screen).",
    )
    ap.add_argument("rides", nargs="+", type=Path, help="one .fit activity per rider")
    ap.add_argument("--course", type=Path, help="course line (.gpx); default: the first rider's track")
    ap.add_argument("--settings", help="detection settings as JSON, e.g. '{\"zone_length_m\": 12}'")
    ap.add_argument("--json", action="store_true", help="write the full JSON report instead of text")
    ap.add_argument("--output", type=Path, help="write the report to this file instead of stdout")
    args = ap.parse_args(argv)
    try:
        settings = DraftingSettingsIn.model_validate_json(args.settings or "{}")
        for p in args.rides:
            if p.suffix.lower() != ".fit":
                raise ScenarioError(f"{p}: not a .fit file.")
        rides = [(p.name, p.read_bytes()) for p in args.rides]
        course = (args.course.name, args.course.read_bytes()) if args.course else None
        review = run_fit_review(rides, course, settings.to_domain())
    except (OSError, ScenarioError, CourseError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    report = payload(review.analysis, settings, review.power)
    text = json.dumps(report, indent=2) if args.json else to_text(report)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
