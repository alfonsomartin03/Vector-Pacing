"""Within-rider power screen (ported from RaceGuard's matched-section tests)."""

from vector_pacing.power_screen import (
    PowerScreenConfig,
    RiderProfile,
    TelemetryPoint,
    _stable_heading,
    expected_solo_power,
    screen_rider,
)


def phased(phases: list[tuple[int, float, float, float]]) -> list[TelemetryPoint]:
    pts: list[TelemetryPoint] = []
    i = 0
    for duration, speed, gradient, power in phases:
        for _ in range(duration):
            pts.append(
                TelemetryPoint(float(i), 40.0 + i * 0.0001, -74.0, speed, power, cadence_rpm=90.0, gradient=gradient)
            )
            i += 1
    return pts


def activity(profile: RiderProfile, outlier_samples: int, baseline_speed: float = 12.0) -> list[TelemetryPoint]:
    base = expected_solo_power(baseline_speed, 0.01, profile)
    pts = [TelemetryPoint(float(i), 40.0 + i * 0.0001, -74.0, baseline_speed, base, gradient=0.01) for i in range(60)]
    pts += [
        TelemetryPoint(float(60 + i), 40.006 + i * 0.0001, -74.0, 15.0, base, gradient=0.01)
        for i in range(outlier_samples)
    ]
    return pts


def run(points: list[TelemetryPoint]):
    return screen_rider("r", points)


def test_expected_power_physics():
    rider = RiderProfile("142")
    assert expected_solo_power(12.0, 0.0, rider) > expected_solo_power(8.0, 0.0, rider)
    assert expected_solo_power(10.0, 0.05, rider) > expected_solo_power(10.0, 0.0, rider)
    try:
        expected_solo_power(-1.0, 0.0, rider)
    except ValueError as exc:
        assert "speed" in str(exc)
    else:
        raise AssertionError("negative speed accepted")


def test_empty_and_no_power_are_safe():
    assert run([]).anomalies == []
    r = run([TelemetryPoint(float(i), 40 + i * 1e-4, -74, 12.0) for i in range(60)])
    assert not r.has_power and r.anomalies == [] and "No power" in r.warnings[0]


def test_fast_aero_rider_is_not_flagged_without_personal_deviation():
    r = run(activity(RiderProfile("aero", cda_m2=0.15), 0, baseline_speed=16.0))
    assert r.anomalies == []
    assert any("No sustained power anomaly" in w for w in r.warnings)


def test_sustained_speed_outlier_is_flagged_against_rider_baseline():
    r = run(activity(RiderProfile("aero", cda_m2=0.18), 40))
    assert r.anomalies
    a = r.anomalies[0]
    assert a.score >= 0.55 and a.reference_power_w > (a.average_power_w or 0)


def test_coasting_at_high_speed_is_not_flagged():
    pts = activity(RiderProfile("c", cda_m2=0.18), 0)
    pts += [
        TelemetryPoint(60.0 + i, 40.006 + i * 0.0001, -74.0, 15.0, 0.0, cadence_rpm=0.0, gradient=-0.02)
        for i in range(25)
    ]
    assert run(pts).anomalies == []


def test_short_soft_pedaling_and_restart_do_not_create_flag():
    pts = activity(RiderProfile("t", cda_m2=0.18), 0)
    base = pts[0].power_w
    for i in range(16):
        restarting = i >= 8
        pts.append(
            TelemetryPoint(
                60.0 + i, 40.006 + i * 0.0001, -74.0, 15.0, base if restarting else 40.0,
                cadence_rpm=85.0 if restarting else 20.0, gradient=0.01,
            )
        )  # fmt: skip
    assert run(pts).anomalies == []


def test_derived_downhill_gradient_prevents_fast_descent_flag():
    profile = RiderProfile("d", cda_m2=0.18)
    flat, down = expected_solo_power(15.0, 0.0, profile), expected_solo_power(15.0, -0.01, profile)
    pts = []
    for i in range(85):
        descending = i >= 60
        pts.append(
            TelemetryPoint(
                float(i), 40.0 + i * 0.0001, -74.0, 15.0, down if descending else flat,
                elevation_m=100.0 - 0.01 * max(0.0, (i - 59) * 15.0), distance_m=i * 15.0, cadence_rpm=90.0,
            )
        )  # fmt: skip
    assert run(pts).anomalies == []


def test_lower_power_on_a_descent_is_not_compared_with_flat_riding():
    p = RiderProfile("h", cda_m2=0.25)
    flat, down = expected_solo_power(12, 0, p), expected_solo_power(12, -0.02, p)
    assert run(phased([(50, 12, 0, flat), (40, 12, -0.02, down), (50, 12, 0, flat)])).anomalies == []


def test_opposite_direction_is_not_a_power_reference():
    normal = expected_solo_power(12, 0, RiderProfile("o", cda_m2=0.25))
    pts = phased([(60, 12, 0, normal), (40, 12, 0, normal - 100)])
    pts = [
        TelemetryPoint(p.t, 40.006 - (i - 60) * 0.0001 if i >= 60 else p.latitude, p.longitude, p.speed_mps,
                       p.power_w, cadence_rpm=p.cadence_rpm, gradient=p.gradient)
        for i, p in enumerate(pts)
    ]  # fmt: skip
    assert run(pts).anomalies == []


def test_heading_uses_coherent_travel_direction_and_rejects_turnaround():
    north = [TelemetryPoint(float(i), 40 + i * 0.0001, -74.0, 12.0) for i in range(6)]
    h = _stable_heading(north, PowerScreenConfig())
    assert h is not None and abs(h) < 1.0
    lats = [40.0, 40.0001, 40.0002, 40.0001, 40.0]
    back = [TelemetryPoint(float(i), la, -74.0, 12.0) for i, la in enumerate(lats)]
    assert _stable_heading(back, PowerScreenConfig()) is None


def test_repeated_low_power_sections_are_compared_with_similar_sections():
    p = RiderProfile("repeat", cda_m2=0.25)
    normal, filler = expected_solo_power(12.0, 0.01, p), expected_solo_power(9.0, 0.03, p)
    phases = [
        (40, 12.0, 0.01, normal), (40, 9.0, 0.03, filler), (40, 12.0, 0.01, normal - 100),
        (40, 9.0, 0.03, filler), (40, 12.0, 0.01, normal), (40, 9.0, 0.03, filler),
        (40, 12.0, 0.01, normal - 100), (40, 9.0, 0.03, filler), (40, 12.0, 0.01, normal),
    ]  # fmt: skip
    found = [a for a in run(phased(phases)).anomalies if any("comparable sections" in n for n in a.notes)]
    assert len(found) >= 2
    assert [a.start_s for a in found] == sorted(a.start_s for a in found)


def test_single_rider_event_between_fixed_boundaries_is_detected():
    normal = expected_solo_power(12.0, 0.01, RiderProfile("x", cda_m2=0.25))
    pts = phased([(127, 12.0, 0.01, normal), (18, 12.0, 0.01, normal - 100), (127, 12.0, 0.01, normal)])
    r = run(pts)
    assert any(a.start_s <= pts[144].t and a.end_s >= pts[127].t for a in r.anomalies)


def test_multiple_single_rider_events_are_reported_separately():
    normal = expected_solo_power(12.0, 0.01, RiderProfile("m", cda_m2=0.25))
    phases = [
        (100, 12.0, 0.01, normal), (22, 12.0, 0.01, normal - 100), (70, 12.0, 0.01, normal),
        (22, 12.0, 0.01, normal - 100), (100, 12.0, 0.01, normal),
    ]  # fmt: skip
    a = run(phased(phases)).anomalies
    assert len(a) >= 2 and a[1].start_s - a[0].end_s > 30
