"""Core domain models shared by ingestion, analysis, and presentation layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True, slots=True)
class RiderProfile:
    """Physical assumptions used to estimate solo power demand."""

    rider_id: str
    rider_mass_kg: float = 75.0
    bike_mass_kg: float = 8.0
    cda_m2: float = 0.30
    crr: float = 0.004
    drivetrain_efficiency: float = 0.97

    @property
    def total_mass_kg(self) -> float:
        return self.rider_mass_kg + self.bike_mass_kg


@dataclass(frozen=True, slots=True)
class TelemetryPoint:
    """A normalized telemetry sample in SI units."""

    rider_id: str
    timestamp: datetime
    latitude: float
    longitude: float
    speed_mps: float
    power_w: float | None = None
    elevation_m: float | None = None
    cadence_rpm: float | None = None
    heart_rate_bpm: float | None = None
    distance_m: float | None = None
    gradient: float = 0.0


@dataclass(frozen=True, slots=True)
class AnalysisConfig:
    """Conservative defaults for surfacing segments for human review."""

    proximity_threshold_m: float = 15.0
    minimum_proximity_seconds: float = 10.0
    minimum_segment_seconds: float = 15.0
    minimum_power_deficit_w: float = 45.0
    minimum_power_deficit_ratio: float = 0.15
    minimum_speed_mps: float = 8.0
    maximum_sample_gap_seconds: float = 5.0
    heading_window_seconds: float = 8.0
    minimum_heading_displacement_m: float = 10.0
    minimum_heading_consistency: float = 0.85
    maximum_pair_heading_difference_deg: float = 15.0
    score_threshold: float = 0.55
    rolling_window_seconds: float = 7.0
    pedaling_transition_seconds: float = 4.0
    minimum_pedaling_power_w: float = 75.0
    soft_pedaling_fraction: float = 0.35
    minimum_pedaling_cadence_rpm: float = 30.0
    minimum_gradient_distance_m: float = 20.0
    comparison_section_seconds: float = 10.0
    comparison_section_stride_seconds: float = 5.0
    comparison_exclusion_seconds: float = 30.0
    comparison_max_seconds: float = 1800.0
    similar_speed_tolerance_mps: float = 0.75
    similar_gradient_tolerance: float = 0.0075
    similar_acceleration_tolerance_mps2: float = 0.20
    minimum_comparison_sections: int = 2


@dataclass(frozen=True, slots=True)
class Evidence:
    """Explainable measurements contributing to a review flag."""

    proximity_score: float = 0.0
    duration_score: float = 0.0
    power_score: float = 0.0
    speed_score: float = 0.0
    telemetry_confidence: float = 0.0
    notes: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class SuspiciousSegment:
    """A time range warranting review, never an assertion of wrongdoing."""

    rider_id: str
    start_time: datetime
    end_time: datetime
    score: float
    evidence: Evidence
    nearest_rider_id: str | None = None
    rider_ahead_id: str | None = None
    rider_behind_id: str | None = None
    average_separation_m: float | None = None
    rider_ahead_speed_mps: float | None = None
    rider_ahead_power_w: float | None = None
    rider_behind_speed_mps: float | None = None
    rider_behind_power_w: float | None = None
    direction_heading_deg: float | None = None
    average_speed_mps: float | None = None
    average_power_w: float | None = None
    expected_power_w: float | None = None
    latitude: float | None = None
    longitude: float | None = None
    course_distance_m: float | None = None

    @property
    def duration_seconds(self) -> float:
        return (self.end_time - self.start_time).total_seconds()
