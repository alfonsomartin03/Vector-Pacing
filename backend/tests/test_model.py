"""Physics, W′ model, simulation rules and optimizer behaviour."""

import itertools
import math

import numpy as np
import pytest

from vector_pacing.athlete import w_prime_balance
from vector_pacing.course import build_course, load_demo_course
from vector_pacing.optimize import (
    OptimizerSettings,
    expand_plan,
    make_segments,
    optimize_pacing,
    terrain_boundaries,
)
from vector_pacing.physics import (
    G,
    SolverError,
    resistance_coefficients,
    solve_speed,
    terminal_speed,
    wheel_power_required,
)
from vector_pacing.simulate import Environment, Rider, SimulationError, Simulator

M_PER_DEG = 111_195.0


def synthetic_course(elevations, spacing=20.0):
    pts = [(45 + i * spacing / M_PER_DEG, 7.0, float(e)) for i, e in enumerate(elevations)]
    return build_course(pts, name="synthetic", source="test", content_hash="t")


# ---------------------------------------------------------------- physics


def coeffs(grade, mass=80.0, cda=0.3, crr=0.004, rho=1.2):
    return resistance_coefficients(np.asarray(grade, float), total_mass_kg=mass, cda_m2=cda, crr=crr, air_density=rho)


def test_solver_roundtrip_across_grades_and_powers():
    grades = np.linspace(-0.2, 0.2, 81)
    a, b = coeffs(grades)
    for p in [0.0, 1.0, 50.0, 250.0, 1200.0]:
        v = solve_speed(np.full_like(grades, p), a, b)
        assert np.all(v >= 0) and np.all(np.isfinite(v))
        assert np.allclose(wheel_power_required(v, a, b), p, atol=1e-5)


def test_flat_speed_matches_hand_calculation():
    a, b = coeffs([0.0])
    v = solve_speed(np.array([250.0]), a, b)[0]
    assert 0.5 * 1.2 * 0.3 * v**3 + 0.004 * 80 * G * v == pytest.approx(250.0, abs=1e-6)
    assert 38 < v * 3.6 < 40  # ~38.9 km/h


def test_speed_monotonic_in_power_and_grade():
    a, b = coeffs([0.0])
    v = solve_speed(np.array([100.0, 200.0, 300.0]), a, np.repeat(b, 3))
    assert v[0] < v[1] < v[2]
    a, b = coeffs([-0.05, 0.0, 0.05])
    v = solve_speed(np.full(3, 250.0), a, b)
    assert v[0] > v[1] > v[2]


def test_zero_power_uphill_stalls_and_downhill_reaches_terminal_speed():
    a, b = coeffs([0.03, -0.06])
    v = solve_speed(np.zeros(2), a, b)
    assert v[0] == 0.0
    assert v[1] == pytest.approx(terminal_speed(a, b)[1])
    assert v[1] == pytest.approx(math.sqrt(-b[1] / a))


def test_solver_rejects_invalid_inputs():
    a, b = coeffs([0.0])
    with pytest.raises(SolverError):
        solve_speed(np.array([-1.0]), a, b)
    with pytest.raises(SolverError):
        solve_speed(np.array([np.nan]), a, b)
    with pytest.raises(SolverError):
        solve_speed(np.array([100.0]), 0.0, b)


# ---------------------------------------------------------------- W′ balance


def test_w_prime_linear_depletion():
    w = w_prime_balance(np.array([380.0] * 10), np.ones(10) * 10, cp_w=280, w_prime_j=20_000)
    assert w[-1] == pytest.approx(20_000 - 100 * 100)


def test_w_prime_exact_recovery_matches_ode():
    w = w_prime_balance(np.array([500.0, 200.0]), np.array([50.0, 60.0]), cp_w=300, w_prime_j=20_000)
    after = 20_000 - 200 * 50
    expected = 20_000 - (20_000 - after) * math.exp(-(100 * 60) / 20_000)
    assert w[0] == pytest.approx(after)
    assert w[1] == pytest.approx(expected)


def test_w_prime_never_exceeds_capacity_and_is_not_clamped_below_zero():
    w = w_prime_balance(np.array([0.0, 600.0]), np.array([600.0, 100.0]), cp_w=300, w_prime_j=20_000)
    assert w[0] == pytest.approx(20_000)
    assert w[1] == pytest.approx(-10_000)  # exhausted: reported, not clamped


def test_w_prime_at_cp_is_steady():
    w = w_prime_balance(np.full(5, 300.0), np.full(5, 100.0), cp_w=300, w_prime_j=20_000)
    assert np.allclose(w, 20_000)


# ---------------------------------------------------------------- simulation rules


def test_flat_course_time_matches_physics():
    c = synthetic_course([100.0] * 501)  # 10 km flat
    rider = Rider(rider_mass_kg=72, bike_mass_kg=8, cda_m2=0.3, crr=0.004, drivetrain_efficiency=1.0)
    sim = Simulator(c, rider, Environment(air_density=1.2))
    r = sim.run_constant(250)
    a, b = coeffs([0.0])
    v = solve_speed(np.array([250.0]), a, b)[0]
    assert r.total_time_s == pytest.approx(c.length_m / v, rel=1e-9)
    assert r.feasible and r.min_w_bal_j == pytest.approx(rider.w_prime_j)


def test_descent_coasting_and_speed_limit():
    elev = [500 - 0.12 * 20 * i for i in range(300)]  # -12 % for 6 km
    c = synthetic_course(elev)
    sim = Simulator(c, Rider(), Environment(max_speed_kmh=70, coast_speed_kmh=50))
    r = sim.run_constant(250)
    interior = slice(20, -20)
    assert np.all(r.coasting[interior])
    assert np.all(r.power_w[interior] == 0)
    assert np.all(r.speed_capped[interior])
    assert np.all(r.speed_ms <= 70 / 3.6 + 1e-9)
    assert np.all(r.planned_power_w == 250)


def test_speed_limit_reduces_applied_power_to_hold_limit():
    c = synthetic_course([500 - 0.03 * 20 * i for i in range(300)])  # -3 %
    sim = Simulator(c, Rider(), Environment(max_speed_kmh=45, coast_speed_kmh=100))
    r = sim.run_constant(400)
    i = 150
    assert r.speed_capped[i]
    assert 0 <= r.power_w[i] < 400
    expected = wheel_power_required(np.array([45 / 3.6]), sim.a, sim.b[i : i + 1])[0] / sim.rider.drivetrain_efficiency
    assert r.power_w[i] == pytest.approx(max(expected, 0.0))


def test_infeasible_effort_is_detected_and_not_clamped():
    c = synthetic_course([100.0] * 1001)  # 20 km flat
    sim = Simulator(c, Rider(cp_w=250, w_prime_j=15_000), Environment())
    r = sim.run_constant(320)
    assert not r.feasible
    assert r.min_w_bal_j < -1000
    assert any("exhausted" in v for v in r.violations)


def test_stall_detected_on_steep_climb_at_low_power():
    c = synthetic_course([100 + 0.2 * 20 * i for i in range(100)])
    sim = Simulator(c, Rider(), Environment())
    r = sim.run_constant(20)
    assert not r.feasible
    assert any("stall" in v for v in r.violations)


def test_invalid_plan_rejected():
    sim = Simulator(load_demo_course(), Rider(), Environment())
    with pytest.raises(SimulationError):
        sim.run(np.ones(3))
    with pytest.raises(SimulationError):
        sim.run(np.full(sim.course.n_steps, -5.0))


def test_max_feasible_constant_power_is_tight():
    sim = Simulator(load_demo_course(), Rider(), Environment())
    p = sim.max_feasible_constant_power(100, 500)
    assert p is not None
    assert sim.run_constant(p).feasible
    assert not sim.run_constant(p + 1.0).feasible
    assert sim.max_feasible_constant_power(400, 500) is None


def test_repeat_runs_are_deterministic():
    sim = Simulator(load_demo_course(), Rider(), Environment())
    a, b = sim.run_constant(270), sim.run_constant(270)
    assert a.total_time_s == b.total_time_s
    assert np.array_equal(a.w_bal_j, b.w_bal_j)


# ---------------------------------------------------------------- optimizer


@pytest.fixture(scope="module")
def demo_opt():
    sim = Simulator(load_demo_course(), Rider(), Environment())
    p = math.floor(sim.max_feasible_constant_power(168, 364))
    base = sim.run_constant(p)
    return sim, base, p, optimize_pacing(sim, base, p, OptimizerSettings(12, 168, 364))


def test_optimizer_improves_demo_and_result_is_verified_feasible(demo_opt):
    sim, base, _p, out = demo_opt
    assert out.status == "improved"
    assert out.result is not None and out.plan_w is not None
    # independent re-verification with the same simulator
    again = sim.run(expand_plan(out.segments, out.plan_w, sim.course.n_steps))
    assert again.feasible and again.min_w_bal_j >= 0
    assert again.total_time_s == pytest.approx(out.result.total_time_s)
    assert again.total_time_s < base.total_time_s - 0.5
    assert np.all(out.plan_w >= 168) and np.all(out.plan_w <= 364)
    assert np.all(out.plan_w == np.floor(out.plan_w))  # whole watts


def test_optimizer_pushes_harder_on_climbs(demo_opt):
    _, _, p, out = demo_opt
    grades = np.array([s.mean_grade for s in out.segments])
    assert out.plan_w[np.argmax(grades)] > p
    assert out.plan_w[np.argmin(grades)] < p


def test_flat_course_shows_no_meaningful_improvement():
    c = synthetic_course([100.0] * 1001)
    sim = Simulator(c, Rider(), Environment())
    p = math.floor(sim.max_feasible_constant_power(168, 364))
    out = optimize_pacing(sim, sim.run_constant(p), p, OptimizerSettings(6, 168, 364))
    assert out.status == "no_improvement"
    assert "baseline is recommended" in out.message


def test_optimizer_refuses_infeasible_baseline():
    sim = Simulator(load_demo_course(), Rider(), Environment())
    base = sim.run_constant(340)
    out = optimize_pacing(sim, base, 340, OptimizerSettings(12, 168, 364))
    assert out.status == "failed" and out.result is None


def test_optimizer_budget_exhaustion_is_reported_honestly():
    sim = Simulator(load_demo_course(), Rider(), Environment())
    p = math.floor(sim.max_feasible_constant_power(168, 364))
    base = sim.run_constant(p)
    out = optimize_pacing(sim, base, p, OptimizerSettings(12, 168, 364), max_evaluations=5)
    assert "budget" in out.solver_message
    assert not out.solver_success
    assert out.status in {"no_improvement", "improved", "failed"}
    if out.result is not None:
        assert out.result.feasible


def test_terrain_boundaries_find_climb_edges():
    elev = np.concatenate([np.full(100, 0.0), np.linspace(0, 100, 100), np.full(100, 100.0)])
    edges = terrain_boundaries(elev, 3, 10)
    assert edges[0] == 0 and edges[-1] == elev.size - 1
    assert abs(edges[1] - 100) <= 2 and abs(edges[2] - 199) <= 2


def test_terrain_boundaries_flat_course_still_splits():
    edges = terrain_boundaries(np.zeros(201), 4, 10)
    assert len(edges) == 5 and edges == sorted(set(edges))


def test_segments_cover_course_exactly():
    sim = Simulator(load_demo_course(), Rider(), Environment())
    segs = make_segments(sim, 30)
    assert len(segs) == 30
    assert segs[0].step_slice.start == 0 and segs[-1].step_slice.stop == sim.course.n_steps
    for s1, s2 in itertools.pairwise(segs):
        assert s1.step_slice.stop == s2.step_slice.start
