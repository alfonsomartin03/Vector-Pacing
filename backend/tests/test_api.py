from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vector_pacing import MODEL_VERSION, __version__
from vector_pacing.api import app

client = TestClient(app)
SAMPLES = Path(__file__).resolve().parents[2] / "samples"
DEMO = {"course": {"kind": "demo"}}


def test_health_and_defaults():
    r = client.get("/api/health").json()
    assert r == {"status": "ok", "app_version": __version__, "model_version": MODEL_VERSION}
    d = client.get("/api/defaults").json()
    assert d["rider"]["cp_w"] == 280 and d["optimizer"]["n_segments"] == 12


def test_demo_course_profile():
    r = client.get("/api/courses/demo")
    assert r.status_code == 200
    body = r.json()
    assert body["length_m"] == pytest.approx(32_000, rel=0.01)
    assert len(body["profile"]["distance_m"]) == body["n_steps"] + 1
    assert len(body["profile"]["grade_pct"]) == body["n_steps"]


def test_simulate_demo_auto_baseline():
    r = client.post("/api/simulate", json=DEMO)
    assert r.status_code == 200
    b = r.json()["baseline"]
    assert b["feasible"] is True and b["min_w_bal_j"] >= 0
    assert b["power_w"] == int(b["power_w"])
    assert "Auto" in b["selection"]
    assert r.json()["meta"]["model_version"] == MODEL_VERSION
    assert "estimates" in r.json()["meta"]["disclaimer"]


def test_simulate_manual_infeasible_baseline_is_reported():
    r = client.post("/api/simulate", json={**DEMO, "baseline": {"mode": "manual", "power_w": 400}})
    assert r.status_code == 200
    b = r.json()["baseline"]
    assert b["feasible"] is False
    assert b["min_w_bal_j"] < 0
    assert b["violations"]


def test_optimize_demo_end_to_end_and_repeatable():
    r1 = client.post("/api/optimize", json=DEMO).json()
    r2 = client.post("/api/optimize", json=DEMO).json()
    o = r1["optimization"]
    assert o["status"] == "improved"
    assert o["result"]["feasible"] is True
    assert o["time_saved_s"] == pytest.approx(r1["baseline"]["total_time_s"] - o["result"]["total_time_s"], abs=0.02)
    assert o["time_saved_s"] > 0.5
    assert len(o["segments"]) == 12
    assert all("planned_power_w" in s for s in o["segments"])
    assert r2["optimization"]["result"]["total_time_s"] == o["result"]["total_time_s"]


def test_optimize_with_infeasible_manual_baseline_fails_honestly():
    r = client.post("/api/optimize", json={**DEMO, "baseline": {"mode": "manual", "power_w": 400}})
    assert r.status_code == 200
    o = r.json()["optimization"]
    assert o["status"] == "failed" and o["result"] is None and o["time_saved_s"] is None


def test_upload_and_run_gpx():
    data = (SAMPLES / "hill_climb_two_segments.gpx").read_bytes()
    r = client.post("/api/courses/parse", files={"file": ("hill.gpx", data, "application/gpx+xml")})
    assert r.status_code == 200
    assert r.json()["warnings"]
    run = client.post(
        "/api/optimize",
        json={"course": {"kind": "gpx", "gpx_text": data.decode(), "filename": "hill.gpx"}},
    )
    assert run.status_code == 200
    assert run.json()["course"]["source"] == "hill.gpx"
    assert run.json()["optimization"]["status"] in {"improved", "no_improvement"}


@pytest.mark.parametrize(
    ("name", "content", "fragment"),
    [
        ("bad.gpx", b"<gpx><trk>", "well-formed"),
        ("empty.gpx", b"", "empty"),
        ("course.txt", b"<gpx/>", ".gpx"),
    ],
)
def test_invalid_uploads(name, content, fragment):
    r = client.post("/api/courses/parse", files={"file": (name, content, "application/octet-stream")})
    assert r.status_code == 422
    assert fragment in r.json()["detail"]


def test_upload_size_limit(monkeypatch):
    import vector_pacing.course as course_mod

    monkeypatch.setattr(course_mod, "MAX_GPX_BYTES", 100)
    r = client.post("/api/courses/parse", files={"file": ("big.gpx", b"x" * 500, "application/gpx+xml")})
    assert r.status_code == 422 and "larger" in r.json()["detail"]


@pytest.mark.parametrize(
    "payload",
    [
        {"course": {"kind": "nope"}},
        {"course": {"kind": "gpx"}},
        {**DEMO, "rider": {"cp_w": -5}},
        {**DEMO, "rider": {"cda_m2": "fast"}},
        {**DEMO, "rider": {"w_prime_kj": 500}},
        {**DEMO, "rider": {"unknown": 1}},
        {**DEMO, "baseline": {"mode": "manual"}},
        {**DEMO, "optimizer": {"n_segments": 1000}},
        {**DEMO, "environment": {"air_density": 0}},
        {},
    ],
)
def test_input_validation(payload):
    r = client.post("/api/simulate", json=payload)
    assert r.status_code == 422
    assert r.json()["detail"].startswith("Invalid input")


def test_coast_speed_above_limit_rejected():
    r = client.post("/api/simulate", json={**DEMO, "environment": {"max_speed_kmh": 50, "coast_speed_kmh": 70}})
    assert r.status_code == 422 and "coasting" in r.json()["detail"]


def test_no_feasible_constant_power_explained():
    r = client.post("/api/simulate", json={**DEMO, "rider": {"w_prime_kj": 1}, "optimizer": {"min_power_pct_cp": 100}})
    # 100 % of CP is always sustainable (no depletion), so this is feasible; tighten further via a tiny CP lower bound
    assert r.status_code == 200
    r = client.post(
        "/api/simulate",
        json={
            **DEMO,
            "rider": {"w_prime_kj": 1, "cp_w": 50, "rider_mass_kg": 120},
            "optimizer": {"min_power_pct_cp": 100, "max_power_pct_cp": 100},
        },
    )
    # CP=50 W on the demo climb: speed can drop below the stall threshold -> infeasible -> explained 422
    assert r.status_code == 422
    assert "No constant power" in r.json()["detail"]


def test_gpx_with_bad_course_in_run_request():
    r = client.post("/api/simulate", json={"course": {"kind": "gpx", "gpx_text": "<gpx></gpx>"}})
    assert r.status_code == 422 and r.json()["kind"] == "course_error"
