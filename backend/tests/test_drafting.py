"""Potential-drafting detection: import validation, rule boundaries and false-positive cases."""

import json
import math
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from vector_pacing.api import app
from vector_pacing.drafting import (
    ILLUSTRATIVE_PRESETS,
    DraftingSettings,
    ExemptSection,
    ScenarioError,
    analyze,
    load_demo_scenario,
    parse_scenario,
)

SAMPLES = Path(__file__).resolve().parents[2] / "samples"
M = 111_195.0
LAT0, LON0 = 46.0, 7.0
COS = math.cos(math.radians(LAT0))
client = TestClient(app)


def xy_to_ll(x: float, y: float) -> list[float]:
    return [LAT0 + y / M, LON0 + x / (M * COS)]


def straight_course(length=3000.0):
    return {"points": [xy_to_ll(x, 0.0) for x in np.arange(0, length + 1, 25.0)]}


def rider(rid, times, s_fn, lateral=0.0, acc=1.0, rng=None):
    pts = []
    for t in times:
        s = s_fn(t)
        if s is None:
            continue
        x, y = float(s), float(lateral(t) if callable(lateral) else lateral)
        if rng is not None:
            x += rng.normal(0, acc / math.sqrt(2))
            y += rng.normal(0, acc / math.sqrt(2))
        lat, lon = xy_to_ll(x, y)
        pts.append({"t": float(t), "lat": lat, "lon": lon, "accuracy_m": acc})
    return {"id": rid, "samples": pts}


def doc(riders, course=None):
    return {"format": "vector-pacing-riders/1", "course": course or straight_course(), "riders": riders}


def run(riders, course=None, **settings):
    return analyze(parse_scenario(doc(riders, course)), DraftingSettings(**settings))


T = np.arange(0, 200, 1.0)
LEADER = rider("L", T, lambda t: 100 + 10 * t)


def follower_with_gap(gap_fn, **kw):
    return rider("F", T, lambda t: 100 + 10 * t - gap_fn(t), **kw)


def ramp_gap(inside_from, inside_to, gap_in, gap_out=40.0, ramp=3.0):
    """Gap that ramps from gap_out to gap_in, holds over [inside_from, inside_to], ramps back."""

    def g(t):
        if t < inside_from - ramp or t > inside_to + ramp:
            return gap_out
        if t < inside_from:
            return gap_out + (gap_in - gap_out) * (t - (inside_from - ramp)) / ramp
        if t > inside_to:
            return gap_in + (gap_out - gap_in) * (t - inside_to) / ramp
        return gap_in

    return g


def classes(a, follower="F", leader="L"):
    return [iv.classification for iv in a.intervals if iv.follower == follower and iv.leader == leader]


# ---------------------------------------------------------------- import


@pytest.mark.parametrize(
    ("document", "fragment"),
    [
        ([], "JSON object"),
        ({"format": "other"}, "Unsupported format"),
        ({"format": "vector-pacing-riders/1", "riders": []}, "course.points"),
        ({"format": "vector-pacing-riders/1", "course": {"points": [[1, 2]]}, "riders": []}, "at least two points"),
        (doc([LEADER]), "at least two riders"),
        (doc([LEADER, {**LEADER}]), "Duplicate rider id"),
        (doc([LEADER, {"id": "X", "samples": [{"t": 0, "lat": 99, "lon": 1}, {"t": 1, "lat": 1, "lon": 1}]}]), "range"),
        (doc([LEADER, {"id": "X", "samples": [{"time": "x", "lat": 1, "lon": 1}]}]), "ISO"),
        (doc([LEADER, {"id": "X", "samples": {"t": [0, 1], "lat": [1]}}]), "equal length"),
        (doc([LEADER, {"id": "X", "samples": [{"lat": 1, "lon": 1}]}]), "needs 't'"),
        (doc([LEADER, {"id": "X", "samples": [{"t": 0, "lat": 1, "lon": 1, "accuracy_m": -1}]}]), "accuracy_m"),
        (doc([LEADER, {"id": "X", "samples": [{"t": 0, "lat": 1, "lon": 1}]}]), "at least two samples"),
        (doc([LEADER, {"id": "", "samples": []}]), "non-empty"),
    ],
)
def test_invalid_documents(document, fragment):
    with pytest.raises(ScenarioError, match=fragment):
        parse_scenario(document)


def test_mixed_time_kinds_rejected():
    r = {
        "id": "X",
        "samples": [{"time": "2026-01-01T00:00:00Z", "lat": 46, "lon": 7}, {"t": 1, "lat": 46, "lon": 7}],
    }
    with pytest.raises(ScenarioError, match="consistently"):
        parse_scenario(doc([LEADER, r]))


def test_row_and_columnar_forms_are_equivalent_and_cleaned():
    rows = rider("F", T, lambda t: 10 * t)
    shuffled = [*list(reversed(rows["samples"])), rows["samples"][5], {"t": 300, "lat": None, "lon": None}]
    cols = {k: [s[k] for s in rows["samples"]] for k in ("t", "lat", "lon", "accuracy_m")}
    a = parse_scenario(doc([LEADER, {"id": "F", "samples": shuffled}]))
    b = parse_scenario(doc([LEADER, {"id": "F", "samples": cols}]))
    fa, fb = a.riders[1], b.riders[1]
    assert np.allclose(fa.t, fb.t) and np.allclose(fa.lat, fb.lat)
    assert any("sorted" in w for w in fa.warnings)
    assert any("duplicate timestamps" in w for w in fa.warnings)
    assert any("without a position" in w for w in fa.warnings)


def test_iso_times_and_missing_accuracy():
    sc = parse_scenario(json.loads((SAMPLES / "drafting_two_riders.json").read_text()))
    assert sc.start_time == "2026-07-01T09:00:00Z"
    assert sc.riders[0].t[0] == 0.0
    assert not sc.riders[0].accuracy_reported


# ---------------------------------------------------------------- rule boundaries


def test_sustained_following_is_flagged_with_details():
    a = run([LEADER, follower_with_gap(ramp_gap(50, 110, 6.0))])
    ivs = [iv for iv in a.intervals if iv.follower == "F"]
    assert [iv.classification for iv in ivs] == ["potential_drafting"]
    iv = ivs[0]
    assert 55 <= iv.duration_s <= 70
    assert iv.gap_median_m == pytest.approx(6.0, abs=0.3)
    assert iv.limitations and iv.quality == "good"
    assert iv.started_by == "approached" and iv.ended_by == "gap_opened"


def test_allowed_duration_boundary_is_inclusive():
    riders = [LEADER, follower_with_gap(ramp_gap(50, 75, 6.0))]
    dur = next(iv for iv in run(riders).intervals if iv.follower == "F").duration_s
    assert classes(run(riders, allowed_pass_s=dur)) == ["brief_encounter"]
    assert classes(run(riders, allowed_pass_s=dur - 1)) == ["potential_drafting"]


def test_zone_length_boundary_with_precise_data():
    lead = rider("L", T, lambda t: 100 + 10 * t, acc=0.1)
    riders = [lead, follower_with_gap(ramp_gap(40, 120, 9.5), acc=0.1)]
    assert classes(run(riders, zone_length_m=10.0, timestamp_tolerance_s=0)) == ["potential_drafting"]
    assert classes(run(riders, zone_length_m=9.0, timestamp_tolerance_s=0)) == []


def test_near_boundary_with_typical_uncertainty_is_inconclusive():
    # 9.5 m gap, ±2.5 m uncertainty, 10 m zone: probability inside ≈ 58 % → not enough to flag.
    a = run([LEADER, follower_with_gap(ramp_gap(40, 120, 9.5))])
    iv = next(iv for iv in a.intervals if iv.follower == "F")
    assert iv.classification == "inconclusive"
    assert 0.5 < iv.confident_inside_fraction < 0.6


def test_following_just_outside_zone_is_not_flagged():
    a = run([LEADER, follower_with_gap(ramp_gap(40, 150, 12.0))])
    assert classes(a) == []


def test_legal_pass_and_drop_back():
    # Follower closes at 1 m/s from 40 m behind to 40 m ahead: ~10 s in the 10 m zone each way.
    f = rider("F", T, lambda t: 100 + 10 * t - (40 - 1.0 * t))
    a = run([LEADER, f])
    assert classes(a) == ["overtake"]
    assert classes(a, "L", "F") == ["drop_back"]


def test_slow_pass_is_flagged_and_noted():
    f = rider("F", T, lambda t: 100 + 10 * t - (20 - 0.25 * t))  # 40 s inside zone before passing
    a = run([LEADER, f])
    iv = next(iv for iv in a.intervals if iv.follower == "F")
    assert iv.classification == "potential_drafting"
    assert iv.ended_by == "follower_passed"
    assert any("eventually passed" in x for x in iv.limitations)


def test_slow_drop_back_is_flagged():
    # F passes L quickly (2 m/s closing) but then L sits 4 m behind F for a minute.
    f = rider("F", T, lambda t: 100 + 10 * t + (min(-40 + 2.0 * t, 4.0)))
    a = run([LEADER, f])
    assert classes(a, "F", "L") == ["overtake"]
    after = [iv for iv in a.intervals if iv.follower == "L"]
    assert [iv.classification for iv in after] == ["potential_drafting"]
    assert after[0].started_by == "overtaken"


def test_brief_encounter_without_pass():
    a = run([LEADER, follower_with_gap(ramp_gap(60, 70, 8.0))])
    assert classes(a) == ["brief_encounter"]


# ---------------------------------------------------------------- false-positive cases


def test_opposite_direction_is_not_following():
    b = rider("B", T, lambda t: 2100 - 10 * t if 2100 - 10 * t > 0 else None)
    a = run([LEADER, b])
    assert a.intervals == []
    assert any(e.type == "opposite_direction" for e in a.encounters)


def out_and_back(length=2000.0, lane=4.0):
    out = [xy_to_ll(x, -lane) for x in np.arange(0, length + 1, 10.0)]
    back = [xy_to_ll(x, lane) for x in np.arange(length, -1, -10.0)]
    return {"points": out + back}


def test_separate_course_sections_not_flagged():
    """Outbound and returning riders pass 8 m apart; their first samples are ambiguous between lanes."""
    course = out_and_back()
    out = rider("OUT", T, lambda t: 1000 + 5 * t, lateral=-4.0)
    back = {"id": "BACK", "samples": []}
    for t in T:
        x = 1800 - 5 * t
        lat, lon = xy_to_ll(x, 4.0)
        back["samples"].append({"t": t, "lat": lat, "lon": lon, "accuracy_m": 2.0})
    # BACK starts near the turnaround on the return lane (course position ~2200 m).
    a = run([out, back], course=course)
    assert a.tracks["BACK"].s[0] > 2000
    assert a.intervals == []
    assert any(e.type == "separate_course_section" for e in a.encounters)


def test_lateral_separation_with_precise_data():
    side = follower_with_gap(ramp_gap(30, 150, 3.0), lateral=3.0, acc=0.3)
    lead = rider("L", T, lambda t: 100 + 10 * t, acc=0.3)
    a = run([lead, side])
    assert classes(a) == []
    assert any(e.type == "lateral_separation" for e in a.encounters)
    # Same geometry but ordinary GNSS: lateral data unusable → treated as in line.
    side2 = follower_with_gap(ramp_gap(30, 150, 3.0), lateral=3.0, acc=2.0)
    lead2 = rider("L", T, lambda t: 100 + 10 * t, acc=2.0)
    iv = next(iv for iv in run([lead2, side2]).intervals if iv.follower == "F")
    assert not iv.lateral_used
    assert any("Lateral separation could not be used" in x for x in iv.limitations)


def test_poor_accuracy_is_inconclusive_not_flagged():
    rng = np.random.default_rng(1)
    lead = rider("L", T, lambda t: 100 + 10 * t, acc=10.0, rng=rng)
    f = rider("F", T, lambda t: 100 + 10 * t - ramp_gap(30, 170, 6.0)(t), acc=10.0, rng=rng)
    a = run([lead, f])
    found = [iv.classification for iv in a.intervals]
    assert "potential_drafting" not in found
    assert "inconclusive" in found


def test_stationary_riders_not_following():
    lead = rider("L", T, lambda t: 500.0)
    f = rider("F", T, lambda t: 495.0)
    a = run([lead, f])
    assert a.intervals == []
    assert any(e.type == "stationary" for e in a.encounters)


# ---------------------------------------------------------------- data quality


def test_short_dropout_bridged_and_reported():
    g = ramp_gap(40, 140, 6.0)
    # 6 s dropout (> 5 s interpolation limit) → missing; velocity estimates widen it by a sample each side.
    f = rider("F", [t for t in T if not (80 <= t < 86)], lambda t: 100 + 10 * t - g(t))
    a = run([LEADER, f], merge_gap_s=10.0)
    ivs = [iv for iv in a.intervals if iv.follower == "F"]
    assert len(ivs) == 1 and ivs[0].classification == "potential_drafting"
    assert ivs[0].missing_s >= 3
    assert any("bridged" in x for x in ivs[0].limitations)


def test_long_dropout_splits_intervals():
    g = ramp_gap(40, 160, 6.0)
    f = rider("F", [t for t in T if not (80 <= t < 110)], lambda t: 100 + 10 * t - g(t))
    a = run([LEADER, f])
    assert len([iv for iv in a.intervals if iv.follower == "F"]) == 2
    q = next(q for q in a.rider_quality if q.id == "F")
    assert q.max_interval_s >= 30


def test_timestamp_misalignment_is_interpolated():
    g = ramp_gap(40, 140, 6.0)
    f = rider("F", T + 0.5, lambda t: 100 + 10 * t - g(t))
    a = run([LEADER, f])
    iv = next(iv for iv in a.intervals if iv.follower == "F")
    assert iv.classification == "potential_drafting"
    assert iv.gap_median_m == pytest.approx(6.0, abs=0.5)
    # A larger assumed clock tolerance widens the uncertainty and lowers quality.
    loose = next(iv for iv in run([LEADER, f], timestamp_tolerance_s=2.0).intervals if iv.follower == "F")
    assert loose.sigma_median_m > iv.sigma_median_m


def test_exempt_section_reduces_counted_time():
    riders = [LEADER, follower_with_gap(ramp_gap(50, 100, 6.0))]
    full = next(iv for iv in run(riders).intervals if iv.follower == "F")
    # Exempt the whole stretch where following happens → not flagged.
    lo, hi = full.course_m
    exempt = run(riders, exempt_sections=(ExemptSection(lo - 50, hi + 50, "aid station"),))
    assert classes(exempt) == ["exempt"]
    partial = next(
        iv for iv in run(riders, exempt_sections=(ExemptSection(lo, lo + 100, "x"),)).intervals if iv.follower == "F"
    )
    assert 0 < partial.exempt_s < partial.duration_s


# ---------------------------------------------------------------- bundled demo


@pytest.fixture(scope="module")
def demo():
    return analyze(
        load_demo_scenario(),
        DraftingSettings(exempt_sections=(ExemptSection(3950, 4085, "turnaround"),)),
    )


def test_demo_cases(demo):
    by_pair = {}
    for iv in demo.intervals:
        by_pair.setdefault((iv.follower, iv.leader), []).append(iv.classification)
    flagged = [(iv.follower, iv.leader) for iv in demo.intervals if iv.classification == "potential_drafting"]
    assert flagged == [("R02", "R01")]
    assert by_pair[("R04", "R03")] == ["overtake"]
    assert by_pair[("R03", "R04")] == ["drop_back"]
    assert by_pair[("R06", "R05")] == ["brief_encounter"]
    ambiguous = by_pair.get(("R08", "R07"), []) + by_pair.get(("R07", "R08"), [])
    assert "inconclusive" in ambiguous
    assert set(ambiguous) <= {"inconclusive", "exempt"}  # never flagged; one fragment falls in the turnaround
    types = {(e.rider_a, e.rider_b, e.type) for e in demo.encounters}
    assert any(t[2] == "opposite_direction" and "R09" in t[:2] for t in types)
    assert ("R11", "R12", "lateral_separation") in types
    assert any(t[2] == "separate_course_section" for t in types)


def test_demo_is_deterministic(demo):
    again = analyze(load_demo_scenario(), demo.settings)
    assert [(i.follower, i.leader, i.classification, i.i0, i.i1) for i in again.intervals] == [
        (i.follower, i.leader, i.classification, i.i0, i.i1) for i in demo.intervals
    ]


def test_presets_are_labelled_illustrative():
    for p in ILLUSTRATIVE_PRESETS.values():
        assert "Illustrative" in p["label"] and "Not taken from any governing body" in p["description"]


# ---------------------------------------------------------------- API


def test_api_presets_and_demo():
    p = client.get("/api/drafting/presets").json()
    assert "illustrative" in p["note"].lower()
    r = client.post(
        "/api/drafting/analyze",
        json={"scenario": {"kind": "demo"}, "settings": {"exempt_sections": p["demo_exempt_sections"]}},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["counts"]["potential_drafting"] == 1
    ev = next(e for e in body["events"] if e["flagged"])
    assert ev["follower"] == "R02" and ev["leader"] == "R01"
    assert ev["series"]["gap_m"] and ev["limitations"]
    assert body["meta"]["drafting_model_version"] == "vp-draft-1.0"
    assert "not confirmed" in body["meta"]["disclaimer"]
    assert len(body["scenario"]["riders"]) == 12


def test_api_upload_sample_and_settings_change():
    document = json.loads((SAMPLES / "drafting_two_riders.json").read_text())
    base = {"scenario": {"kind": "upload", "document": document, "filename": "two.json"}}
    r = client.post("/api/drafting/analyze", json=base).json()
    # No accuracy reported → the assumed 5 m applies → too uncertain for a 10 m zone.
    assert r["counts"] == {"inconclusive": 1}
    assert r["scenario"]["riders"][0]["accuracy_reported"] is False
    assert any("Accuracy not reported" in w for w in r["scenario"]["riders"][0]["warnings"])
    precise = {**base, "settings": {"assumed_accuracy_m": 1.0}}
    assert client.post("/api/drafting/analyze", json=precise).json()["counts"] == {"potential_drafting": 1}
    short = {**base, "settings": {"assumed_accuracy_m": 1.0, "zone_length_m": 4}}
    assert client.post("/api/drafting/analyze", json=short).json()["counts"] == {}


@pytest.mark.parametrize(
    "payload",
    [
        {"scenario": {"kind": "upload"}},
        {"scenario": {"kind": "demo"}, "settings": {"zone_length_m": 0}},
        {"scenario": {"kind": "demo"}, "settings": {"exempt_sections": [{"start_m": 10, "end_m": 5}]}},
        {"scenario": {"kind": "demo"}, "settings": {"bogus": 1}},
    ],
)
def test_api_validation(payload):
    r = client.post("/api/drafting/analyze", json=payload)
    assert r.status_code == 422 and r.json()["detail"].startswith("Invalid input")


def test_api_invalid_document():
    document = json.loads((SAMPLES / "drafting_invalid.json").read_text())
    r = client.post("/api/drafting/analyze", json={"scenario": {"kind": "upload", "document": document}})
    assert r.status_code == 422 and r.json()["kind"] == "scenario_error"
