"""Course simulation for a per-step power plan.

Explicit behaviour rules applied at every step (see docs/MODEL.md):

1. **Coasting (spin-out).** If the road is steep enough that the zero-power
   terminal speed is at or above ``coast_speed``, the rider stops pedalling:
   applied power is 0 W and the speed is the terminal speed.
2. **Pedalling.** Otherwise the rider applies the planned power and travels at
   the steady speed solved by :func:`physics.solve_speed`.
3. **Speed limit.** If the resulting speed exceeds ``max_speed`` the speed is
   capped and applied power is reduced to exactly what holds the limit (or 0 W
   with braking if gravity alone exceeds it).
4. **Stall.** A speed below ``STALL_SPEED_MS`` makes the plan infeasible.
5. **Solver failure** raises :class:`SimulationError`; no result is produced.

Planned power and applied power can therefore differ; W′ and time use the
*applied* power.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .athlete import FEASIBILITY_TOLERANCE_J, w_prime_balance
from .course import Course
from .physics import SolverError, resistance_coefficients, solve_speed, terminal_speed, wheel_power_required

STALL_SPEED_MS = 1.0  # 3.6 km/h


class SimulationError(RuntimeError):
    """Simulation could not be completed. The message is user-facing."""


@dataclass(frozen=True)
class Rider:
    rider_mass_kg: float = 72.0
    bike_mass_kg: float = 8.5
    cda_m2: float = 0.28
    crr: float = 0.004
    drivetrain_efficiency: float = 0.975
    cp_w: float = 280.0
    w_prime_j: float = 20_000.0

    @property
    def total_mass_kg(self) -> float:
        return self.rider_mass_kg + self.bike_mass_kg


@dataclass(frozen=True)
class Environment:
    air_density: float = 1.225
    max_speed_kmh: float = 80.0
    coast_speed_kmh: float = 60.0


@dataclass
class SimulationResult:
    distance_m: np.ndarray  # step start, n
    planned_power_w: np.ndarray
    power_w: np.ndarray  # applied
    speed_ms: np.ndarray
    dt_s: np.ndarray
    w_bal_j: np.ndarray  # at end of each step
    coasting: np.ndarray
    speed_capped: np.ndarray
    total_time_s: float
    min_w_bal_j: float
    feasible: bool
    violations: list[str] = field(default_factory=list)

    @property
    def elapsed_s(self) -> np.ndarray:
        return np.cumsum(self.dt_s)

    def average_power_w(self) -> float:
        return float(np.sum(self.power_w * self.dt_s) / self.total_time_s)

    def normalized_power_w(self) -> float:
        """Approximate NP: 4th-power mean of 30 s rolling time-weighted power."""
        t = np.concatenate([[0.0], self.elapsed_s])
        grid = np.arange(0.0, t[-1], 1.0)
        if grid.size < 30:
            return self.average_power_w()
        idx = np.clip(np.searchsorted(t, grid, side="right") - 1, 0, self.power_w.size - 1)
        p1 = self.power_w[idx]
        rolling = np.convolve(p1, np.ones(30) / 30.0, mode="valid")
        return float(np.mean(rolling**4) ** 0.25)


class Simulator:
    """Precomputes course/rider coefficients so repeated simulations are cheap."""

    def __init__(self, course: Course, rider: Rider, env: Environment):
        self.course = course
        self.rider = rider
        self.env = env
        self.a, self.b = resistance_coefficients(
            course.grade,
            total_mass_kg=rider.total_mass_kg,
            cda_m2=rider.cda_m2,
            crr=rider.crr,
            air_density=env.air_density,
        )
        self.ds = np.diff(course.distance_m)
        self.v_term = terminal_speed(self.a, self.b)
        self.v_max = env.max_speed_kmh / 3.6
        self.coasting = self.v_term >= env.coast_speed_kmh / 3.6
        p_hold = wheel_power_required(np.full_like(self.b, self.v_max), self.a, self.b)
        self.p_hold_vmax_w = np.clip(p_hold / rider.drivetrain_efficiency, 0.0, None)

    def run(self, planned_power_w: np.ndarray) -> SimulationResult:
        plan = np.asarray(planned_power_w, dtype=float)
        if plan.shape != self.ds.shape:
            raise SimulationError("Power plan length does not match the course.")
        if not np.all(np.isfinite(plan)) or np.any(plan < 0):
            raise SimulationError("Planned power must be finite and non-negative.")
        eta = self.rider.drivetrain_efficiency
        applied = np.where(self.coasting, 0.0, plan)
        try:
            v = solve_speed(applied * eta, self.a, self.b)
        except SolverError as exc:
            raise SimulationError(f"The physics solver failed: {exc}") from None
        v = np.where(self.coasting, self.v_term, v)
        capped = v > self.v_max
        v = np.where(capped, self.v_max, v)
        applied = np.where(capped, np.minimum(applied, self.p_hold_vmax_w), applied)

        violations: list[str] = []
        stalled = v < STALL_SPEED_MS
        if np.any(stalled):
            i = int(np.argmax(stalled))
            violations.append(
                f"Speed drops below {STALL_SPEED_MS * 3.6:.1f} km/h at km {self.course.distance_m[i] / 1000:.2f}; "
                "the rider would stall at this power."
            )
        v_safe = np.maximum(v, 1e-3)
        dt = self.ds / v_safe
        if not np.all(np.isfinite(dt)):
            raise SimulationError("The simulation produced a non-finite step time.")
        wbal = w_prime_balance(applied, dt, self.rider.cp_w, self.rider.w_prime_j)
        min_w = float(wbal.min())
        if min_w < -FEASIBILITY_TOLERANCE_J:
            i = int(np.argmax(wbal < -FEASIBILITY_TOLERANCE_J))
            violations.append(
                f"W′ balance is exhausted at km {self.course.distance_m[i + 1] / 1000:.2f} "
                f"and reaches {min_w / 1000:.2f} kJ; this effort exceeds the modelled capacity."
            )
        return SimulationResult(
            distance_m=self.course.distance_m[:-1],
            planned_power_w=plan,
            power_w=applied,
            speed_ms=v,
            dt_s=dt,
            w_bal_j=wbal,
            coasting=self.coasting.copy(),
            speed_capped=capped,
            total_time_s=float(dt.sum()),
            min_w_bal_j=min_w,
            feasible=not violations,
            violations=violations,
        )

    def run_constant(self, power_w: float) -> SimulationResult:
        return self.run(np.full(self.ds.shape, float(power_w)))

    def max_feasible_constant_power(self, lo_w: float, hi_w: float, tol_w: float = 0.05) -> float | None:
        """Highest constant power in ``[lo, hi]`` that keeps the plan feasible.

        Uses bisection (W′ depletion grows monotonically with constant power).
        Returns ``None`` when even ``lo`` is infeasible.
        """
        if not self.run_constant(lo_w).feasible:
            return None
        if self.run_constant(hi_w).feasible:
            return hi_w
        lo, hi = lo_w, hi_w
        for _ in range(60):
            if hi - lo <= tol_w:
                break
            mid = 0.5 * (lo + hi)
            if self.run_constant(mid).feasible:
                lo = mid
            else:
                hi = mid
        return lo
