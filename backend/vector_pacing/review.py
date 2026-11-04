"""Drafting review of uploaded .fit rides: GPS following check + within-rider power screen.

Shared by the HTTP API (``POST /api/drafting/analyze-fit``) and the command-line tool. The GPS
following check (:mod:`vector_pacing.drafting`) needs two or more riders; the power screen
(:mod:`vector_pacing.power_screen`, ported from RaceGuard) runs on every rider with power data, so a
single uploaded ride still gets screened.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePath
from typing import Any

import numpy as np

from .course import CourseError, haversine_m, parse_gpx_points
from .drafting import (
    FORMAT_ID,
    MAX_RIDERS,
    DraftingAnalysis,
    DraftingSettings,
    Scenario,
    ScenarioError,
    analyze,
    parse_scenario,
)
from .fit import FitTrack, read_fit
from .power_screen import (
    POWER_SCREEN_VERSION,
    PowerScreenConfig,
    PowerScreenResult,
    RiderProfile,
    points_from_track,
    screen_rider,
)

COURSE_FROM_TRACK_SPACING_M = 5.0
POWER_SCREEN_NOTE = (
    "Within-rider power screen (from RaceGuard): sections where a rider held speed on clearly less power "
    "than in their own comparable sections. Tailwind, riding position, road surface and sensor error can "
    "look the same. Scores are heuristic review priorities, not probabilities."
)


@dataclass
class FitScenario:
    scenario: Scenario
    tracks: list[tuple[str, str, FitTrack]]  # (rider id, file name, track)
    t0: float  # Unix time of scenario t = 0


@dataclass
class PowerReview:
    results: list[PowerScreenResult]
    speed_from_gps: dict[str, bool]


@dataclass
class FitReview:
    fit: FitScenario
    analysis: DraftingAnalysis
    power: PowerReview


def _unique_id(stem: str, seen: set[str]) -> str:
    base = (stem.strip() or "rider")[:36]
    rid, n = base, 2
    while rid in seen:
        rid, n = f"{base}-{n}", n + 1
    seen.add(rid)
    return rid


def scenario_from_fit(rider_files: list[tuple[str, bytes]], course_file: tuple[str, bytes] | None) -> FitScenario:
    """Build a scenario from one FIT activity per rider and an optional GPX course line.

    Without a course file the first rider's track (thinned to ~5 m spacing) is used as the course line.
    """
    if not rider_files:
        raise ScenarioError("Upload at least one .fit file (one per rider).")
    if len(rider_files) > MAX_RIDERS:
        raise ScenarioError(f"At most {MAX_RIDERS} riders are supported.")
    tracks = []
    seen: set[str] = set()
    for fname, data in rider_files:
        tr = read_fit(data, fname)
        if len(tr.t) < 2:
            raise ScenarioError(f"{fname}: fewer than two GPS positions were found; was GPS recording enabled?")
        tracks.append((_unique_id(PurePath(fname).stem, seen), fname, tr))
    t0 = min(min(tr.t) for _, _, tr in tracks)

    notes: list[str] = []
    if len(tracks) == 1:
        notes.append(
            "Only one ride was uploaded, so the GPS following check (which needs two or more riders) "
            "cannot run; only the within-rider power screen applies."
        )
    if course_file is not None:
        cname, cdata = course_file
        try:
            gname, pts, _, _ = parse_gpx_points(cdata)
        except CourseError as exc:
            raise ScenarioError(f"{cname}: {exc}") from None
        course_pts = [[lat, lon] for lat, lon, _ in pts]
        course_name = (gname or PurePath(cname).stem)[:120]
        notes.append(f"Course line from {cname}.")
    else:
        rid, fname, first = tracks[0]
        lat, lon = np.array(first.lat), np.array(first.lon)
        cum = np.concatenate([[0.0], np.cumsum(haversine_m(lat[:-1], lon[:-1], lat[1:], lon[1:]))])
        bucket = np.floor(cum / COURSE_FROM_TRACK_SPACING_M)
        keep = np.flatnonzero(np.concatenate([[True], np.diff(bucket) > 0])).tolist()
        if keep[-1] != len(lat) - 1:
            keep.append(len(lat) - 1)
        course_pts = [[float(lat[i]), float(lon[i])] for i in keep]
        course_name = f"Track of {rid}"
        if len(tracks) > 1:
            notes.append(
                f"No course .gpx was uploaded, so {fname}'s own track is used as the course line. "
                "Upload the course .gpx for a neutral reference."
            )

    riders = [
        {
            "id": rid,
            "label": rid,
            "samples": {"t": [t - t0 for t in tr.t], "lat": tr.lat, "lon": tr.lon, "accuracy_m": tr.accuracy_m},
        }
        for rid, _, tr in tracks
    ]
    doc = {
        "format": FORMAT_ID,
        "name": f"{len(tracks)} rider{'s' if len(tracks) != 1 else ''} from .fit files",
        "description": " ".join(notes),
        "start_time": datetime.fromtimestamp(t0, UTC).isoformat().replace("+00:00", "Z"),
        "course": {"name": course_name, "points": course_pts},
        "riders": riders,
    }
    scenario = parse_scenario(doc, source=", ".join(f for f, _ in rider_files)[:200], min_riders=1)
    for rider, (_, _, tr) in zip(scenario.riders, tracks, strict=True):
        if tr.records_without_position:
            rider.warnings.append(f"{tr.records_without_position} record(s) without a GPS position were ignored.")
    return FitScenario(scenario, tracks, t0)


def screen_power(fs: FitScenario, config: PowerScreenConfig | None = None) -> PowerReview:
    """Run the within-rider power screen on every uploaded ride (times relative to ``fs.t0``)."""
    results, from_gps = [], {}
    for rid, _, tr in fs.tracks:
        pts, derived = points_from_track(
            [t - fs.t0 for t in tr.t], tr.lat, tr.lon, tr.speed_mps, tr.power_w, tr.cadence_rpm, tr.altitude_m,
            tr.distance_m,
        )  # fmt: skip
        res = screen_rider(rid, pts, RiderProfile(rid), config)
        if derived and res.has_power:
            res.warnings.insert(0, "No speed recorded; speed was derived from GPS positions, which is noisier.")
        results.append(res)
        from_gps[rid] = derived
    return PowerReview(results, from_gps)


def run_fit_review(
    rider_files: list[tuple[str, bytes]],
    course_file: tuple[str, bytes] | None,
    settings: DraftingSettings,
    power_config: PowerScreenConfig | None = None,
) -> FitReview:
    fs = scenario_from_fit(rider_files, course_file)
    return FitReview(fs, analyze(fs.scenario, settings), screen_power(fs, power_config))


def _f(v: float | None, nd: int = 1) -> float | None:
    return None if v is None else round(float(v), nd)


def power_payload(power: PowerReview, events: list[dict[str, Any]]) -> dict[str, Any]:
    """Serialise the power screen and link each anomaly with overlapping GPS intervals (same follower).

    Adds ``power_anomaly_ids`` to each GPS event in place.
    """
    anomalies: list[dict[str, Any]] = []
    flat = sorted((a for r in power.results for a in r.anomalies), key=lambda a: (a.start_s, a.rider_id))
    for e in events:
        e["power_anomaly_ids"] = []
    for n, a in enumerate(flat, start=1):
        aid = f"P{n:03d}"
        linked = []
        for e in events:
            if (
                e["follower"] == a.rider_id
                and e["start_s"] is not None
                and e["end_s"] is not None
                and e["start_s"] <= a.end_s
                and e["end_s"] >= a.start_s
            ):
                linked.append(e["id"])
                e["power_anomaly_ids"].append(aid)
        anomalies.append(
            {
                "id": aid,
                "rider": a.rider_id,
                "start_s": _f(a.start_s),
                "end_s": _f(a.end_s),
                "duration_s": _f(a.duration_s),
                "score": a.score,
                "power_score": a.power_score,
                "duration_score": a.duration_score,
                "average_speed_kmh": _f(a.average_speed_mps * 3.6),
                "average_power_w": _f(a.average_power_w),
                "reference_power_w": _f(a.reference_power_w),
                "deficit_w": _f(a.deficit_w),
                "peer_count": a.peer_count,
                "speed_normalised": a.speed_normalised,
                "heading_deg": _f(a.heading_deg, 0),
                "lat": round(a.latitude, 6),
                "lon": round(a.longitude, 6),
                "distance_m": _f(a.distance_m),
                "notes": list(a.notes),
                "gps_event_ids": linked,
            }
        )
    return {
        "version": POWER_SCREEN_VERSION,
        "note": POWER_SCREEN_NOTE,
        "riders": [
            {
                "id": r.rider_id,
                "has_power": r.has_power,
                "speed_from_gps": power.speed_from_gps.get(r.rider_id, False),
                "anomaly_count": len(r.anomalies),
                "warnings": r.warnings,
            }
            for r in power.results
        ],
        "anomalies": anomalies,
    }
