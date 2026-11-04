"""Segment-based pacing optimization.

The course is divided into ``n_segments`` terrain-aware segments (see
:func:`terrain_boundaries`), each with
one planned power value. We minimise finishing time subject to

* power bounds  ``min_power ≤ P_k ≤ max_power`` for each segment, and
* W′bal ≥ ``reserve`` at the end of every simulation step,

using SciPy's SLSQP (a local, gradient-based method with finite-difference
gradients), starting from the feasible constant-power baseline. This finds a
*local* optimum near the baseline; no global optimality is claimed.

Honesty rules:

* Every simulated candidate that is feasible is recorded; the best one is
  chosen even if SLSQP stops early or reports failure.
* The chosen plan is rounded **down** to whole watts (lowering power can only
  increase W′bal) and then re-simulated independently by the same simulator
  used for the baseline. Only that verified result is reported.
* If the verified plan is not at least ``MIN_REPORTABLE_IMPROVEMENT_S`` faster,
  the outcome is "no_improvement" and the baseline is recommended.
* Computation is bounded by an evaluation budget and a wall-clock limit.
"""

from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
from scipy.optimize import minimize

from .simulate import SimulationError, SimulationResult, Simulator

MIN_REPORTABLE_IMPROVEMENT_S = 0.5
MAX_EVALUATIONS = 4000
MAX_WALL_TIME_S = 30.0
RESERVE_FRACTION = 0.002  # optimizer targets W′bal ≥ 0.2 % of W′ for numerical margin

Status = Literal["improved", "no_improvement", "failed"]


class _BudgetExceeded(Exception):
    pass


@dataclass(frozen=True)
class OptimizerSettings:
    n_segments: int = 12
    min_power_w: float = 170.0
    max_power_w: float = 380.0
    max_iterations: int = 150


@dataclass
class Segment:
    index: int
    start_m: float
    end_m: float
    step_slice: slice
    mean_grade: float


@dataclass
class OptimizationOutcome:
    status: Status
    message: str
    segments: list[Segment]
    plan_w: np.ndarray | None  # per segment, whole watts
    result: SimulationResult | None
    solver_success: bool
    solver_message: str
    iterations: int
    evaluations: int
    elapsed_s: float
    notes: list[str] = field(default_factory=list)


def terrain_boundaries(elevation_m: np.ndarray, n_segments: int, min_steps: int) -> list[int]:
    """Choose segment boundaries (step-edge indices) where the profile bends most.

    Greedy top-down piecewise-linear split (Douglas–Peucker style): repeatedly
    split the segment whose elevation deviates most from its straight chord, at
    the point of maximum deviation, keeping every segment at least
    ``min_steps`` long. When the profile is (nearly) flat, the longest segment
    is halved instead, so the result is always ``n_segments`` segments when
    the course is long enough.
    """
    n_edges = elevation_m.size
    edges = [0, n_edges - 1]
    while len(edges) - 1 < n_segments:
        best: tuple[float, int] | None = None
        for a, b in itertools.pairwise(edges):
            if b - a < 2 * min_steps:
                continue
            idx = np.arange(a + min_steps, b - min_steps + 1)
            chord = elevation_m[a] + (elevation_m[b] - elevation_m[a]) * (idx - a) / (b - a)
            dev = np.abs(elevation_m[idx] - chord)
            j = int(np.argmax(dev))
            if best is None or dev[j] > best[0]:
                best = (float(dev[j]), int(idx[j]))
        if best is None:
            break
        if best[0] < 1.0:  # flat: halve the longest splittable segment
            a, b = max(
                ((a, b) for a, b in itertools.pairwise(edges) if b - a >= 2 * min_steps),
                key=lambda ab: ab[1] - ab[0],
            )
            best = (0.0, (a + b) // 2)
        edges = sorted([*edges, best[1]])
    return edges


def make_segments(sim: Simulator, n_segments: int) -> list[Segment]:
    n_steps = sim.course.n_steps
    n = max(1, min(n_segments, n_steps))
    min_steps = max(1, n_steps // (3 * n))
    edges = terrain_boundaries(sim.course.elevation_m, n, min_steps)
    d = sim.course.distance_m
    segs = []
    for k in range(len(edges) - 1):
        s = slice(int(edges[k]), int(edges[k + 1]))
        run_m = d[s.stop] - d[s.start]
        mean_grade = float((sim.course.elevation_m[s.stop] - sim.course.elevation_m[s.start]) / run_m)
        segs.append(Segment(k, float(d[s.start]), float(d[s.stop]), s, mean_grade))
    return segs


def expand_plan(segments: list[Segment], seg_power: np.ndarray, n_steps: int) -> np.ndarray:
    out = np.empty(n_steps)
    for seg, p in zip(segments, seg_power, strict=True):
        out[seg.step_slice] = p
    return out


def optimize_pacing(
    sim: Simulator,
    baseline: SimulationResult,
    baseline_power_w: float,
    settings: OptimizerSettings,
    *,
    max_evaluations: int = MAX_EVALUATIONS,
    max_wall_time_s: float = MAX_WALL_TIME_S,
) -> OptimizationOutcome:
    t0 = time.perf_counter()
    segments = make_segments(sim, settings.n_segments)
    n_steps = sim.course.n_steps
    cp = sim.rider.cp_w
    w0 = sim.rider.w_prime_j
    lo, hi = settings.min_power_w, settings.max_power_w
    reserve = RESERVE_FRACTION * w0
    t_base = baseline.total_time_s

    def fail(msg: str, solver_msg: str = "", evals: int = 0, iters: int = 0) -> OptimizationOutcome:
        return OptimizationOutcome(
            "failed", msg, segments, None, None, False, solver_msg, iters, evals, time.perf_counter() - t0
        )

    if not baseline.feasible:
        return fail(
            "The baseline is infeasible, so there is no feasible starting point to optimize from. "
            "Lower the manual baseline power or use the Auto baseline."
        )
    if not (lo <= baseline_power_w <= hi):
        return fail(
            f"The baseline power ({baseline_power_w:.0f} W) is outside the optimizer power bounds "
            f"({lo:.0f}–{hi:.0f} W). Widen the bounds or change the baseline."
        )

    cache: dict[bytes, SimulationResult] = {}
    feasible_seen: list[tuple[float, np.ndarray]] = []
    evals = 0

    def sim_x(x: np.ndarray) -> SimulationResult:
        nonlocal evals
        key = np.asarray(x, dtype=float).tobytes()
        r = cache.get(key)
        if r is None:
            if evals >= max_evaluations or time.perf_counter() - t0 > max_wall_time_s:
                raise _BudgetExceeded
            evals += 1
            p = np.clip(np.asarray(x, dtype=float) * cp, lo, hi)
            r = sim.run(expand_plan(segments, p, n_steps))
            if len(cache) > 256:
                cache.clear()
            cache[key] = r
            if r.feasible and r.min_w_bal_j >= 0:
                feasible_seen.append((r.total_time_s, p.copy()))
        return r

    x0 = np.full(len(segments), baseline_power_w / cp)
    bounds = [(lo / cp, hi / cp)] * len(segments)
    solver_success, solver_message, iterations = False, "", 0
    try:
        res = minimize(
            lambda x: sim_x(x).total_time_s / t_base,
            x0,
            method="SLSQP",
            bounds=bounds,
            constraints=[{"type": "ineq", "fun": lambda x: (sim_x(x).w_bal_j - reserve) / w0}],
            options={"maxiter": settings.max_iterations, "ftol": 1e-7, "eps": 1e-4},
        )
        solver_success = bool(res.success)
        solver_message = str(res.message)
        iterations = int(res.nit)
        sim_x(res.x)  # make sure the final iterate is recorded
    except _BudgetExceeded:
        solver_message = f"Stopped at the computation budget ({max_evaluations} simulations / {max_wall_time_s:.0f} s)."
    except SimulationError as exc:
        return fail(f"The optimizer could not complete: {exc}", str(exc), evals)
    except (ValueError, FloatingPointError) as exc:
        return fail("The numerical optimizer failed unexpectedly.", str(exc), evals)

    notes = [
        "Local optimization (SLSQP) started from the constant-power baseline; "
        "the result is not guaranteed to be the global optimum.",
    ]
    if not solver_success:
        notes.append(f"The solver did not report convergence ({solver_message}); the best feasible plan found is used.")

    if not feasible_seen:
        return fail("The optimizer did not find any feasible plan.", solver_message, evals, iterations)

    _, best_p = min(feasible_seen, key=lambda t: t[0])
    plan = np.floor(best_p + 1e-9)
    plan = np.clip(plan, np.ceil(lo), np.floor(hi))
    try:
        verified = sim.run(expand_plan(segments, plan, n_steps))
    except SimulationError as exc:
        return fail(f"Verification of the optimized plan failed: {exc}", solver_message, evals, iterations)
    elapsed = time.perf_counter() - t0

    if not verified.feasible:
        return OptimizationOutcome(
            "failed",
            "The optimized plan failed independent verification (it violates the W′ model), so it is not reported.",
            segments, None, verified, solver_success, solver_message, iterations, evals, elapsed, notes,
        )  # fmt: skip
    saving = t_base - verified.total_time_s
    if saving < MIN_REPORTABLE_IMPROVEMENT_S:
        return OptimizationOutcome(
            "no_improvement",
            f"No meaningful improvement was found (verified difference {saving:+.1f} s, threshold "
            f"{MIN_REPORTABLE_IMPROVEMENT_S} s). The constant-power baseline is recommended.",
            segments, plan, verified, solver_success, solver_message, iterations, evals, elapsed, notes,
        )  # fmt: skip
    return OptimizationOutcome(
        "improved",
        f"The variable-power plan is estimated to be {saving:.1f} s faster than the baseline under this model.",
        segments, plan, verified, solver_success, solver_message, iterations, evals, elapsed, notes,
    )  # fmt: skip
