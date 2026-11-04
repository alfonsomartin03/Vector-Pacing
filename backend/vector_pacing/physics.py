"""Quasi-steady-state cycling power model.

At every simulation step the rider is assumed to travel at the steady speed at
which propulsive power at the wheel equals resistive power::

    η·P = v · ( ½·ρ·CdA·v²  +  m·g·Crr·cos θ  +  m·g·sin θ )
          wheel     aero         rolling            gravity

* ``P``   rider (crank) power [W]
* ``η``   drivetrain efficiency (dimensionless, e.g. 0.975)
* ``ρ``   air density [kg/m³]; still air is assumed (no wind)
* ``CdA`` drag area [m²]
* ``Crr`` rolling-resistance coefficient
* ``m``   rider + bike mass [kg];  ``g`` = 9.80665 m/s²
* ``θ``   road angle, ``atan(grade)``

Acceleration/inertia is neglected (each step jumps to its steady speed). With
``a = ½ρCdA > 0`` and ``b = m·g·(Crr·cos θ + sin θ)`` the equation becomes the
cubic ``a·v³ + b·v − η·P = 0``. For ``P ≥ 0`` it has exactly one non-negative
root (``f(0) = −ηP ≤ 0`` and ``f`` is eventually increasing), which we compute
in closed form (Cardano / trigonometric form), vectorised over all steps, and
then polish with two Newton iterations. Results that are non-finite or leave
a residual above tolerance raise :class:`SolverError` rather than being used.
"""

from __future__ import annotations

import numpy as np

G = 9.80665
RESIDUAL_TOLERANCE_W = 1e-6


class SolverError(RuntimeError):
    """The speed solver did not converge to a valid root."""


def resistance_coefficients(
    grade: np.ndarray, *, total_mass_kg: float, cda_m2: float, crr: float, air_density: float
) -> tuple[float, np.ndarray]:
    """Return ``(a, b)`` such that wheel power at speed v is ``a·v³ + b·v``."""
    theta = np.arctan(grade)
    a = 0.5 * air_density * cda_m2
    b = total_mass_kg * G * (crr * np.cos(theta) + np.sin(theta))
    return a, np.asarray(b, dtype=float)


def wheel_power_required(v: np.ndarray, a: float, b: np.ndarray) -> np.ndarray:
    """Wheel power [W] needed to hold speed ``v`` [m/s] (negative ⇒ braking needed)."""
    return np.asarray(a * v**3 + b * v)


def terminal_speed(a: float, b: np.ndarray) -> np.ndarray:
    """Steady speed with zero power (0 where the road is not steep enough to roll)."""
    return np.sqrt(np.clip(-b / a, 0.0, None))


def solve_speed(wheel_power_w: np.ndarray, a: float, b: np.ndarray) -> np.ndarray:
    """Solve ``a·v³ + b·v = P_wheel`` for the non-negative root, vectorised.

    Raises :class:`SolverError` if any root is invalid.
    """
    pw = np.asarray(wheel_power_w, dtype=float)
    b = np.broadcast_to(np.asarray(b, dtype=float), pw.shape)
    if a <= 0 or not np.isfinite(a):
        raise SolverError("Aerodynamic coefficient must be positive.")
    if np.any(pw < 0) or not np.all(np.isfinite(pw)):
        raise SolverError("Wheel power must be finite and non-negative.")

    p = b / a
    q = -pw / a
    disc = (q / 2.0) ** 2 + (p / 3.0) ** 3
    v = np.empty_like(pw)

    one = disc >= 0
    if np.any(one):
        s = np.sqrt(disc[one])
        v[one] = np.cbrt(-q[one] / 2.0 + s) + np.cbrt(-q[one] / 2.0 - s)
    three = ~one  # only possible when p < 0 (descending): take the largest root
    if np.any(three):
        pp, qq = p[three], q[three]
        arg = np.clip((3.0 * qq / (2.0 * pp)) * np.sqrt(-3.0 / pp), -1.0, 1.0)
        v[three] = 2.0 * np.sqrt(-pp / 3.0) * np.cos(np.arccos(arg) / 3.0)

    v = np.clip(v, 0.0, None)
    for _ in range(2):  # Newton polish
        f = a * v**3 + b * v - pw
        df = 3.0 * a * v**2 + b
        ok = (df > 1e-12) & (v > 0)
        v = np.where(ok, v - np.divide(f, df, out=np.zeros_like(f), where=ok), v)
        v = np.clip(v, 0.0, None)

    residual = np.abs(a * v**3 + b * v - pw)
    bad = ~np.isfinite(v) | (residual > RESIDUAL_TOLERANCE_W * np.maximum(1.0, pw))
    if np.any(bad):
        i = int(np.argmax(bad))
        raise SolverError(f"Speed solver failed to converge at step {i} (residual {residual[i]:.3g} W).")
    return np.asarray(v, dtype=float)
