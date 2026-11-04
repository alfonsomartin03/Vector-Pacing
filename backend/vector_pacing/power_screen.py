"""Within-rider power screening: sustained speed on unusually little power.

Ported from RaceGuard (``raceguard.analysis``, matched-section evidence model). Drafting reduces the
aerodynamic power needed for a given speed. This module looks for stable sections of one rider's
activity where measured power is clearly below what the *same rider* needed in comparable sections
(similar direction, grade, acceleration and, preferably, speed) elsewhere in the activity.

It complements the GPS following check in :mod:`vector_pacing.drafting`: it needs only the rider's
own file (no other riders), but it cannot tell drafting from tailwind, position changes, road surface
or sensor error. Results are review prompts, never findings. See docs/DRAFTING.md.

Method (unchanged from RaceGuard):

1. **Frames.** A centred 7 s window smooths speed, power, grade and acceleration. Grade is derived
   from elevation over >= 20 m when not supplied. Coasting, soft pedalling (< max(75 W, 35 % of the
   rider's median power)), cadence < 30 rpm, nearby pedalling transitions (±4 s), speeds < 8 m/s and
   recording gaps are excluded. Heading is only used when GPS movement is coherent.
2. **Sections.** Stable 10 s sections, sampled every 5 s so events are not diluted by fixed
   boundaries; speed may vary <= 1.5 m/s and grade <= 2 % within a section.
3. **Comparison.** Each section is compared with sections of the same rider 30 s–30 min away, within
   20° of heading, 0.75 % grade and 0.2 m/s² acceleration. Same-speed peers (±0.75 m/s) are preferred;
   otherwise peers within 3.5 m/s are speed-normalised with a v³ aerodynamic adjustment. The median
   reference must exceed measured power by >= 45 W and 15 % (60 W and 20 % when speed-normalised) and
   by 2.5 × a robust spread, and the deficit may not exceed 70 % of the aerodynamic share.
4. **Segments.** Flagged frames are joined (gaps <= 5 s); segments shorter than 15 s are dropped.
"""

from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from itertools import pairwise
from statistics import fmean, median

import numpy as np

from .course import haversine_m as _haversine_np

GRAVITY_MPS2 = 9.80665
EARTH_RADIUS_M = 6_371_008.8
POWER_SCREEN_VERSION = "raceguard-matched-sections-1"


@dataclass(frozen=True, slots=True)
class RiderProfile:
    """Physical assumptions for the mechanical (non-aerodynamic) power terms."""

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
    """One sample in SI units. ``t`` is seconds (any common origin)."""

    t: float
    latitude: float
    longitude: float
    speed_mps: float
    power_w: float | None = None
    elevation_m: float | None = None
    cadence_rpm: float | None = None
    distance_m: float | None = None
    gradient: float = 0.0


@dataclass(frozen=True, slots=True)
class PowerScreenConfig:
    """Conservative defaults for surfacing sections for human review (RaceGuard values)."""

    minimum_segment_seconds: float = 15.0
    minimum_power_deficit_w: float = 45.0
    minimum_power_deficit_ratio: float = 0.15
    minimum_speed_mps: float = 8.0
    maximum_sample_gap_seconds: float = 5.0
    heading_window_seconds: float = 8.0
    minimum_heading_displacement_m: float = 10.0
    minimum_heading_consistency: float = 0.85
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
class PowerAnomaly:
    """A time range where power was low against the rider's own comparable sections."""

    rider_id: str
    start_s: float
    end_s: float
    duration_s: float
    score: float  # heuristic review priority, not a probability
    power_score: float
    duration_score: float
    average_speed_mps: float
    average_power_w: float | None
    reference_power_w: float
    peer_count: int
    speed_normalised: bool
    heading_deg: float | None
    latitude: float
    longitude: float
    distance_m: float | None
    notes: tuple[str, ...]

    @property
    def deficit_w(self) -> float:
        return self.reference_power_w - (self.average_power_w or 0.0)


@dataclass
class PowerScreenResult:
    rider_id: str
    anomalies: list[PowerAnomaly]
    has_power: bool
    warnings: list[str] = field(default_factory=list)


def expected_solo_power(
    speed_mps: float,
    gradient: float,
    profile: RiderProfile,
    *,
    air_density_kg_m3: float = 1.225,
    headwind_mps: float = 0.0,
    acceleration_mps2: float = 0.0,
) -> float:
    """Crank power for solo riding with a simple force model (``gradient`` is rise/run)."""
    if speed_mps < 0:
        raise ValueError("speed_mps cannot be negative")
    if not 0 < profile.drivetrain_efficiency <= 1:
        raise ValueError("drivetrain_efficiency must be in (0, 1]")
    air_speed = max(0.0, speed_mps + headwind_mps)
    aerodynamic_w = 0.5 * air_density_kg_m3 * profile.cda_m2 * air_speed**3
    rolling_w = profile.crr * profile.total_mass_kg * GRAVITY_MPS2 * speed_mps
    climbing_w = profile.total_mass_kg * GRAVITY_MPS2 * gradient * speed_mps
    acceleration_w = profile.total_mass_kg * acceleration_mps2 * speed_mps
    return max(0.0, (aerodynamic_w + rolling_w + climbing_w + acceleration_w) / profile.drivetrain_efficiency)


# ---------------------------------------------------------------- internals


@dataclass(frozen=True, slots=True)
class _Frame:
    point: TelemetryPoint
    speed_mps: float
    gradient: float
    acceleration_mps2: float
    heading_deg: float | None
    power_w: float | None
    stable_pedaling: bool


@dataclass(frozen=True, slots=True)
class _Section:
    frames: tuple[_Frame, ...]
    start: float
    end: float
    speed_mps: float
    gradient: float
    acceleration_mps2: float
    heading_deg: float
    power_w: float


@dataclass(frozen=True, slots=True)
class _PowerEvidence:
    reference_w: float
    deficit_w: float
    peer_count: int
    matched_speed: bool


def _distance_m(a: TelemetryPoint, b: TelemetryPoint) -> float:
    lat1, lat2 = math.radians(a.latitude), math.radians(b.latitude)
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(math.radians(b.longitude - a.longitude) / 2) ** 2
    )
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


def clean_points(points: list[TelemetryPoint]) -> list[TelemetryPoint]:
    """Drop impossible samples and duplicate timestamps; sort by time."""
    unique: dict[float, TelemetryPoint] = {}
    for p in points:
        if not (-90 <= p.latitude <= 90 and -180 <= p.longitude <= 180 and 0 <= p.speed_mps <= 45):
            continue
        if p.power_w is not None and not (0 <= p.power_w <= 2500):
            continue
        unique[p.t] = p
    return sorted(unique.values(), key=lambda p: p.t)


def points_from_track(
    t: list[float],
    lat: list[float],
    lon: list[float],
    speed_mps: list[float | None],
    power_w: list[float | None],
    cadence_rpm: list[float | None],
    altitude_m: list[float | None],
    distance_m: list[float | None],
) -> tuple[list[TelemetryPoint], bool]:
    """Build telemetry points; speed is derived from GPS where the file has none.

    Returns ``(points, speed_derived)``.
    """
    derived = all(s is None for s in speed_mps)
    gps_speed: list[float] = [0.0] * len(t)
    if derived and len(t) > 1:
        la, lo, tt = np.array(lat), np.array(lon), np.array(t)
        step = _haversine_np(la[:-1], lo[:-1], la[1:], lo[1:]) / np.maximum(np.diff(tt), 1e-6)
        # centred difference: mean of the step before and after each sample
        v = np.concatenate([[step[0]], (step[:-1] + step[1:]) / 2, [step[-1]]])
        gps_speed = v.tolist()
    pts = []
    for i in range(len(t)):
        s = gps_speed[i] if derived else speed_mps[i]
        if s is None:
            continue
        pts.append(
            TelemetryPoint(
                t=t[i],
                latitude=lat[i],
                longitude=lon[i],
                speed_mps=float(s),
                power_w=power_w[i],
                elevation_m=altitude_m[i],
                cadence_rpm=cadence_rpm[i],
                distance_m=distance_m[i],
            )
        )
    return pts, derived


def screen_rider(
    rider_id: str,
    points: list[TelemetryPoint],
    profile: RiderProfile | None = None,
    config: PowerScreenConfig | None = None,
) -> PowerScreenResult:
    """Screen one rider's activity for sustained power deficits against their own riding."""
    config = config or PowerScreenConfig()
    profile = profile or RiderProfile(rider_id)
    points = clean_points(points)
    has_power = any(p.power_w is not None for p in points)
    if not has_power:
        return PowerScreenResult(rider_id, [], False, ["No power recorded; the power screen was skipped."])
    frames = _build_frames(points, config)
    sections = _build_sections(frames, config)
    comparisons = _compare_sections(sections, profile, config)
    power_by_t: dict[float, _PowerEvidence] = {}
    for section, evidence in comparisons:
        for frame in section.frames:
            prev = power_by_t.get(frame.point.t)
            if prev is None or _strength(evidence) > _strength(prev):
                power_by_t[frame.point.t] = evidence
    warnings: list[str] = []
    if not comparisons and any(f.stable_pedaling for f in frames):
        warnings.append(
            "No sustained power anomaly had enough comparable sections at similar speed, grade and direction."
        )
    candidates = [(f, power_by_t[f.point.t]) for f in frames if f.point.t in power_by_t]
    anomalies: list[PowerAnomaly] = []
    for run in _runs(candidates, config.maximum_sample_gap_seconds):
        duration = _run_duration(run)
        if duration >= config.minimum_segment_seconds:
            anomalies.append(_anomaly_from_run(rider_id, run, duration))
    return PowerScreenResult(rider_id, sorted(anomalies, key=lambda a: a.start_s), True, warnings)


def _strength(e: _PowerEvidence) -> float:
    return e.deficit_w / max(e.reference_w, 1.0)


def _build_frames(points: list[TelemetryPoint], config: PowerScreenConfig) -> list[_Frame]:
    ordered = points
    ts = [p.t for p in ordered]
    powers = [p.power_w for p in ordered if p.power_w is not None and p.power_w > 0]
    typical = median(powers) if powers else 0.0
    active_threshold = max(config.minimum_pedaling_power_w, typical * config.soft_pedaling_fraction)
    active = [
        p.power_w is not None
        and p.power_w >= active_threshold
        and (p.cadence_rpm is None or p.cadence_rpm >= config.minimum_pedaling_cadence_rpm)
        for p in ordered
    ]
    inactive_prefix = [0]
    gap_prefix = [0]
    for i, is_active in enumerate(active):
        inactive_prefix.append(inactive_prefix[-1] + (not is_active))
        if i:
            gap_prefix.append(gap_prefix[-1] + (ts[i] - ts[i - 1] > config.maximum_sample_gap_seconds))
    frames: list[_Frame] = []
    half = config.rolling_window_seconds / 2
    for p in ordered:
        left, right = bisect_left(ts, p.t - half), bisect_right(ts, p.t + half)
        window = ordered[left:right]
        # A recording gap marks a new interval. Do not smooth across missing telemetry.
        if gap_prefix[right - 1] - gap_prefix[left] > 0:
            window = [p]
        elapsed = window[-1].t - window[0].t
        acceleration = (window[-1].speed_mps - window[0].speed_mps) / elapsed if elapsed > 0 else 0.0
        grade = fmean(s.gradient for s in window)
        derived = _derived_grade(window, config)
        if abs(grade) < 0.0001 and derived is not None:
            grade = derived
        dl = bisect_left(ts, p.t - config.heading_window_seconds)
        dr = bisect_right(ts, p.t + config.heading_window_seconds)
        heading = _stable_heading(ordered[dl:dr], config)
        tl = bisect_left(ts, p.t - config.pedaling_transition_seconds)
        tr = bisect_right(ts, p.t + config.pedaling_transition_seconds)
        transition = inactive_prefix[tr] != inactive_prefix[tl]
        window_powers = [s.power_w for s in window if s.power_w is not None]
        frames.append(
            _Frame(
                point=p,
                speed_mps=fmean(s.speed_mps for s in window),
                gradient=grade,
                acceleration_mps2=acceleration,
                heading_deg=heading,
                power_w=fmean(window_powers) if window_powers else None,
                stable_pedaling=not transition and p.speed_mps >= config.minimum_speed_mps,
            )
        )
    return frames


def _derived_grade(window: list[TelemetryPoint], config: PowerScreenConfig) -> float | None:
    first, last = window[0], window[-1]
    if first.elevation_m is None or last.elevation_m is None:
        return None
    if first.distance_m is not None and last.distance_m is not None:
        traveled = last.distance_m - first.distance_m
    else:
        traveled = _distance_m(first, last)
    if traveled < config.minimum_gradient_distance_m:
        return None
    return max(-0.25, min(0.25, (last.elevation_m - first.elevation_m) / traveled))


def _stable_heading(points: list[TelemetryPoint], config: PowerScreenConfig) -> float | None:
    """Course heading only when GPS movement is coherent enough to trust.

    Successive position vectors are combined; the resultant/path ratio rejects stationary GPS wander,
    sharp turns and turnarounds.
    """
    if len(points) < 2:
        return None
    north_total = east_total = path = 0.0
    for a, b in pairwise(points):
        elapsed = b.t - a.t
        if elapsed <= 0 or elapsed > config.maximum_sample_gap_seconds:
            return None
        north = math.radians(b.latitude - a.latitude) * EARTH_RADIUS_M
        east = (
            math.radians(b.longitude - a.longitude)
            * EARTH_RADIUS_M
            * math.cos(math.radians((a.latitude + b.latitude) / 2))
        )
        north_total += north
        east_total += east
        path += math.hypot(north, east)
    displacement = math.hypot(north_total, east_total)
    if (
        path < config.minimum_heading_displacement_m
        or displacement < config.minimum_heading_displacement_m
        or displacement / path < config.minimum_heading_consistency
    ):
        return None
    return math.degrees(math.atan2(east_total, north_total)) % 360


def _angle_difference(a: float, b: float) -> float:
    return abs((a - b + 180) % 360 - 180)


def _build_sections(frames: list[_Frame], config: PowerScreenConfig) -> list[_Section]:
    sections: list[_Section] = []
    current: list[_Frame] = []
    for f in frames:
        if not f.stable_pedaling or f.heading_deg is None or f.power_w is None:
            _finish_section_run(current, sections, config)
            continue
        if current and (
            f.point.t - current[-1].point.t > config.maximum_sample_gap_seconds
            or _angle_difference(f.heading_deg, current[-1].heading_deg or 0) > 20
        ):
            _finish_section_run(current, sections, config)
        current.append(f)
    _finish_section_run(current, sections, config)
    return sections


def _finish_section_run(current: list[_Frame], sections: list[_Section], config: PowerScreenConfig) -> None:
    """Overlapping windows (half-window stride) so event timing cannot hide an anomaly."""
    if len(current) < 3:
        current.clear()
        return
    ts = [f.point.t for f in current]
    stride = max(1.0, config.comparison_section_stride_seconds)
    anchor = ts[0]
    while anchor < ts[-1]:
        _append_section(
            current[bisect_left(ts, anchor) : bisect_right(ts, anchor + config.comparison_section_seconds)],
            sections,
            config,
        )
        anchor += stride
    current.clear()


def _append_section(frames: list[_Frame], sections: list[_Section], config: PowerScreenConfig) -> None:
    if len(frames) < 3:
        return
    elapsed = frames[-1].point.t - frames[0].point.t
    speeds = [f.speed_mps for f in frames]
    grades = [f.gradient for f in frames]
    if (
        elapsed < min(5.0, config.comparison_section_seconds / 2)
        or max(speeds) - min(speeds) > 1.5
        or max(grades) - min(grades) > 0.02
    ):
        return
    sections.append(
        _Section(
            frames=tuple(frames),
            start=frames[0].point.t,
            end=frames[-1].point.t,
            speed_mps=fmean(speeds),
            gradient=fmean(grades),
            acceleration_mps2=fmean(f.acceleration_mps2 for f in frames),
            heading_deg=_circular_mean([f.heading_deg for f in frames if f.heading_deg is not None]),
            power_w=fmean(f.power_w for f in frames if f.power_w is not None),
        )
    )


def _circular_mean(angles: list[float]) -> float:
    east = fmean(math.sin(math.radians(a)) for a in angles)
    north = fmean(math.cos(math.radians(a)) for a in angles)
    return math.degrees(math.atan2(east, north)) % 360


def _compare_sections(
    sections: list[_Section], profile: RiderProfile, config: PowerScreenConfig
) -> list[tuple[_Section, _PowerEvidence]]:
    matches: list[tuple[_Section, _PowerEvidence]] = []
    for section in sections:
        peers = [
            other
            for other in sections
            if other is not section
            and config.comparison_exclusion_seconds <= _section_gap(section, other) <= config.comparison_max_seconds
            and _angle_difference(section.heading_deg, other.heading_deg) <= 20
            and abs(section.gradient - other.gradient) <= config.similar_gradient_tolerance
            and abs(section.acceleration_mps2 - other.acceleration_mps2) <= config.similar_acceleration_tolerance_mps2
            and abs(section.speed_mps - other.speed_mps) <= 3.5
        ]
        if len(peers) < max(2, config.minimum_comparison_sections):
            continue
        same_speed = [p for p in peers if abs(p.speed_mps - section.speed_mps) <= config.similar_speed_tolerance_mps]
        matched_speed = len(same_speed) >= 2
        selected = same_speed if matched_speed else peers
        references = [r for p in selected if (r := _reference_power(p, section, profile, matched_speed)) is not None]
        if len(references) < 2:
            continue
        reference_w = median(references)
        deficit_w = reference_w - section.power_w
        mad_w = median(abs(v - reference_w) for v in references)
        min_w = config.minimum_power_deficit_w if matched_speed else max(60.0, config.minimum_power_deficit_w)
        min_ratio = (
            config.minimum_power_deficit_ratio if matched_speed else max(0.20, config.minimum_power_deficit_ratio)
        )
        if deficit_w < max(min_w, reference_w * min_ratio, 2.5 * max(20.0, 1.4826 * mad_w)):
            continue
        # A drag saving cannot exceed the whole aerodynamic part of solo power.
        aero_w = max(0.0, reference_w - _mechanical_power(section, profile))
        if aero_w < 50 or deficit_w > 0.7 * aero_w:
            continue
        matches.append((section, _PowerEvidence(reference_w, deficit_w, len(references), matched_speed)))
    return matches


def _mechanical_power(section: _Section, profile: RiderProfile) -> float:
    v, m = section.speed_mps, profile.total_mass_kg
    wheel_w = (
        profile.crr * m * GRAVITY_MPS2 * v + m * GRAVITY_MPS2 * section.gradient * v + m * section.acceleration_mps2 * v
    )
    return wheel_w / profile.drivetrain_efficiency


def _reference_power(peer: _Section, target: _Section, profile: RiderProfile, matched_speed: bool) -> float | None:
    if matched_speed:
        return peer.power_w + _mechanical_power(target, profile) - _mechanical_power(peer, profile)
    peer_aero_w = peer.power_w - _mechanical_power(peer, profile)
    if peer_aero_w <= 0:
        return None
    return _mechanical_power(target, profile) + peer_aero_w * (target.speed_mps / peer.speed_mps) ** 3


def _section_gap(a: _Section, b: _Section) -> float:
    if a.end < b.start:
        return b.start - a.end
    if b.end < a.start:
        return a.start - b.end
    return 0.0


_Cand = tuple[_Frame, _PowerEvidence]


def _runs(candidates: list[_Cand], max_gap: float) -> list[list[_Cand]]:
    runs: list[list[_Cand]] = []
    for c in candidates:
        if not runs or c[0].point.t - runs[-1][-1][0].point.t > max_gap:
            runs.append([c])
        else:
            runs[-1].append(c)
    return runs


def _run_duration(run: list[_Cand]) -> float:
    observed = run[-1][0].point.t - run[0][0].point.t
    intervals = [b[0].point.t - a[0].point.t for a, b in pairwise(run)]
    return observed + min(median(intervals), 5.0) if intervals else 1.0


def _anomaly_from_run(rider_id: str, run: list[_Cand], duration: float) -> PowerAnomaly:
    frames = [f for f, _ in run]
    evidence = [e for _, e in run]
    reference = fmean(e.reference_w for e in evidence)
    measured = [f.power_w for f in frames if f.power_w is not None]
    power_score = min(1.0, fmean(e.deficit_w / e.reference_w for e in evidence) / 0.35)
    duration_score = min(1.0, duration / 45)
    # Heuristic review priority, not a posterior probability of drafting (RaceGuard weights).
    score = min(0.95, 0.55 + 0.14 * duration_score + 0.15 * power_score)
    peer_count = max(e.peer_count for e in evidence)
    normalised = not all(e.matched_speed for e in evidence)
    notes = [
        f"Power was lower than {peer_count} comparable sections from this rider's activity after matching "
        "travel direction, speed, grade and acceleration."
    ]
    if normalised:
        notes.append("Some comparison sections required speed normalisation, which increases wind uncertainty.")
    notes.append("Wind, riding position, road surface and sensor error can produce similar patterns.")
    headings = [f.heading_deg for f in frames if f.heading_deg is not None]
    distances = [f.point.distance_m for f in frames if f.point.distance_m is not None]
    return PowerAnomaly(
        rider_id=rider_id,
        start_s=frames[0].point.t,
        end_s=frames[-1].point.t,
        duration_s=duration,
        score=round(score, 3),
        power_score=round(power_score, 3),
        duration_score=round(duration_score, 3),
        average_speed_mps=fmean(f.speed_mps for f in frames),
        average_power_w=fmean(measured) if measured else None,
        reference_power_w=reference,
        peer_count=peer_count,
        speed_normalised=normalised,
        heading_deg=_circular_mean(headings) if headings else None,
        latitude=fmean(f.point.latitude for f in frames),
        longitude=fmean(f.point.longitude for f in frames),
        distance_m=fmean(distances) if distances else None,
        notes=tuple(notes),
    )
