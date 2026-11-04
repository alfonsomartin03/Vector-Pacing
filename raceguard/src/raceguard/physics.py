"""Transparent cycling-physics estimates used as screening evidence."""

from __future__ import annotations

from .models import RiderProfile

GRAVITY_MPS2 = 9.80665


def expected_solo_power(
    speed_mps: float,
    gradient: float,
    profile: RiderProfile,
    *,
    air_density_kg_m3: float = 1.225,
    headwind_mps: float = 0.0,
    acceleration_mps2: float = 0.0,
) -> float:
    """Estimate crank power needed for solo riding using a simple force model.

    ``gradient`` is rise/run (0.01 means 1%). Positive ``headwind_mps`` increases
    air speed. Negative total power is clamped to zero because this prototype does
    not attempt to model braking or recovered energy.
    """

    if speed_mps < 0:
        raise ValueError("speed_mps cannot be negative")
    if not 0 < profile.drivetrain_efficiency <= 1:
        raise ValueError("drivetrain_efficiency must be in (0, 1]")

    air_speed = max(0.0, speed_mps + headwind_mps)
    aerodynamic_w = 0.5 * air_density_kg_m3 * profile.cda_m2 * air_speed**3
    rolling_w = profile.crr * profile.total_mass_kg * GRAVITY_MPS2 * speed_mps
    climbing_w = profile.total_mass_kg * GRAVITY_MPS2 * gradient * speed_mps
    acceleration_w = profile.total_mass_kg * acceleration_mps2 * speed_mps
    wheel_power = aerodynamic_w + rolling_w + climbing_w + acceleration_w
    return max(0.0, wheel_power / profile.drivetrain_efficiency)

