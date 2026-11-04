"""Within-activity drafting screening with optional trailing-rider corroboration.

This is a review-priority model. Ground speed and power alone cannot identify
drafting uniquely because wind and rider position are not measured by FIT records.
"""

from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import pairwise
from statistics import fmean, median

from .ingest import clean_points, haversine_m
from .models import AnalysisConfig, Evidence, RiderProfile, SuspiciousSegment, TelemetryPoint
from .physics import GRAVITY_MPS2


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    points_analyzed: int
    riders_analyzed: tuple[str, ...]
    started_at: datetime | None
    ended_at: datetime | None
    segments: tuple[SuspiciousSegment, ...]
    warnings: tuple[str, ...] = ()

    @property
    def is_suspicious(self) -> bool:
        return bool(self.segments)

    @property
    def confidence(self) -> float:
        """Largest heuristic evidence score; this is not a calibrated probability."""
        return max((segment.score for segment in self.segments), default=0.0)

    @property
    def evidence_score(self) -> float:
        return self.confidence


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
    start: datetime
    end: datetime
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


@dataclass(frozen=True, slots=True)
class _Candidate:
    frame: _Frame
    power: _PowerEvidence | None = None
    leader_id: str | None = None
    separation_m: float | None = None
    leader_speed_mps: float | None = None
    leader_power_w: float | None = None


@dataclass(frozen=True, slots=True)
class _RiderRelationship:
    rider_ahead_id: str
    separation_m: float
    rider_ahead_speed_mps: float
    rider_ahead_power_w: float | None


def analyze(
    points: list[TelemetryPoint],
    profiles: dict[str, RiderProfile] | None = None,
    config: AnalysisConfig | None = None,
) -> AnalysisResult:
    config = config or AnalysisConfig()
    profiles = profiles or {}
    if not points:
        return AnalysisResult(0, (), None, None, (), ("No valid telemetry points were supplied.",))
    points = clean_points(points)
    if not points:
        return AnalysisResult(0, (), None, None, (), ("No valid telemetry points were supplied.",))

    by_rider: dict[str, list[TelemetryPoint]] = defaultdict(list)
    for point in points:
        by_rider[point.rider_id].append(point)
    riders = tuple(sorted(by_rider))
    warnings: list[str] = []
    if len(riders) == 1:
        warnings.append("Single-rider screening needs repeated comparable conditions; wind and riding position remain unknown.")
    if all(point.power_w is None for point in points):
        warnings.append("Power is absent; only sustained trailing-rider GPS evidence can be screened.")

    frames_by_rider = {
        rider_id: _build_frames(rider_points, config)
        for rider_id, rider_points in by_rider.items()
    }
    power_by_key: dict[tuple[str, datetime], _PowerEvidence] = {}
    for rider_id, frames in frames_by_rider.items():
        profile = profiles.get(rider_id, RiderProfile(rider_id))
        sections = _build_sections(frames, config)
        comparisons = _compare_sections(sections, profile, config)
        for section, evidence in comparisons:
            for frame in section.frames:
                key = (rider_id, frame.point.timestamp)
                previous = power_by_key.get(key)
                if previous is None or _power_evidence_strength(
                    evidence
                ) > _power_evidence_strength(previous):
                    power_by_key[key] = evidence
        if not comparisons and any(frame.stable_pedaling for frame in frames):
            warnings.append(
                f"Rider {rider_id}: no sustained power anomaly had enough comparable sections "
                "at similar speed, grade, and direction."
            )

    trailing = _trailing_evidence(frames_by_rider, config)
    segments: list[SuspiciousSegment] = []
    for rider_id, frames in frames_by_rider.items():
        candidates: list[_Candidate] = []
        for frame in frames:
            key = (rider_id, frame.point.timestamp)
            power = power_by_key.get(key)
            lead = trailing.get(key)
            if power is not None or lead is not None:
                candidates.append(
                    _Candidate(
                        frame=frame,
                        power=power,
                        leader_id=lead.rider_ahead_id if lead else None,
                        separation_m=lead.separation_m if lead else None,
                        leader_speed_mps=lead.rider_ahead_speed_mps if lead else None,
                        leader_power_w=lead.rider_ahead_power_w if lead else None,
                    )
                )
        for run in _runs(candidates, config.maximum_sample_gap_seconds):
            duration = _run_duration(run)
            if duration < config.minimum_segment_seconds:
                continue
            segment = _segment_from_run(rider_id, run, duration, config)
            if segment.score >= config.score_threshold:
                segments.append(segment)

    return AnalysisResult(
        points_analyzed=len(points),
        riders_analyzed=riders,
        started_at=min(point.timestamp for point in points),
        ended_at=max(point.timestamp for point in points),
        segments=tuple(sorted(segments, key=lambda segment: (segment.start_time, segment.rider_id))),
        warnings=tuple(warnings),
    )


def _build_frames(points: list[TelemetryPoint], config: AnalysisConfig) -> list[_Frame]:
    ordered = sorted(points, key=lambda point: point.timestamp)
    timestamps = [point.timestamp for point in ordered]
    powers = [point.power_w for point in ordered if point.power_w is not None and point.power_w > 0]
    typical_power = median(powers) if powers else 0.0
    active_threshold = max(config.minimum_pedaling_power_w, typical_power * config.soft_pedaling_fraction)
    active = [
        point.power_w is not None
        and point.power_w >= active_threshold
        and (point.cadence_rpm is None or point.cadence_rpm >= config.minimum_pedaling_cadence_rpm)
        for point in ordered
    ]
    inactive_prefix = [0]
    gap_prefix = [0]
    for index, is_active in enumerate(active):
        inactive_prefix.append(inactive_prefix[-1] + (not is_active))
        if index:
            gap = (timestamps[index] - timestamps[index - 1]).total_seconds()
            gap_prefix.append(gap_prefix[-1] + (gap > config.maximum_sample_gap_seconds))
    frames: list[_Frame] = []
    half_window = config.rolling_window_seconds / 2
    for point in ordered:
        left = bisect_left(timestamps, point.timestamp - timedelta(seconds=half_window))
        right = bisect_right(timestamps, point.timestamp + timedelta(seconds=half_window))
        window = ordered[left:right]
        # A recording gap marks a new interval. Do not smooth across missing telemetry.
        if gap_prefix[right - 1] - gap_prefix[left] > 0:
            window = [point]
        elapsed = (window[-1].timestamp - window[0].timestamp).total_seconds()
        acceleration = (
            (window[-1].speed_mps - window[0].speed_mps) / elapsed if elapsed > 0 else 0.0
        )
        grade = fmean(sample.gradient for sample in window)
        derived = _derived_grade(window, config)
        if abs(grade) < 0.0001 and derived is not None:
            grade = derived
        direction_left = bisect_left(
            timestamps, point.timestamp - timedelta(seconds=config.heading_window_seconds)
        )
        direction_right = bisect_right(
            timestamps, point.timestamp + timedelta(seconds=config.heading_window_seconds)
        )
        direction_points = ordered[direction_left:direction_right]
        heading = _stable_heading(direction_points, config)
        transition_left = bisect_left(
            timestamps, point.timestamp - timedelta(seconds=config.pedaling_transition_seconds)
        )
        transition_right = bisect_right(
            timestamps, point.timestamp + timedelta(seconds=config.pedaling_transition_seconds)
        )
        transition = inactive_prefix[transition_right] != inactive_prefix[transition_left]
        window_powers = [sample.power_w for sample in window if sample.power_w is not None]
        frames.append(
            _Frame(
                point=point,
                speed_mps=fmean(sample.speed_mps for sample in window),
                gradient=grade,
                acceleration_mps2=acceleration,
                heading_deg=heading,
                power_w=fmean(window_powers) if window_powers else None,
                stable_pedaling=not transition and point.speed_mps >= config.minimum_speed_mps,
            )
        )
    return frames


def _derived_grade(window: list[TelemetryPoint], config: AnalysisConfig) -> float | None:
    first, last = window[0], window[-1]
    if first.elevation_m is None or last.elevation_m is None:
        return None
    if first.distance_m is not None and last.distance_m is not None:
        traveled = last.distance_m - first.distance_m
    else:
        traveled = haversine_m(first, last)
    if traveled < config.minimum_gradient_distance_m:
        return None
    return max(-0.25, min(0.25, (last.elevation_m - first.elevation_m) / traveled))


def _bearing(first: TelemetryPoint, last: TelemetryPoint) -> float | None:
    north = math.radians(last.latitude - first.latitude)
    east = math.radians(last.longitude - first.longitude) * math.cos(
        math.radians((first.latitude + last.latitude) / 2)
    )
    if math.hypot(north, east) < 1e-9:
        return None
    return math.degrees(math.atan2(east, north)) % 360


def _stable_heading(
    points: list[TelemetryPoint], config: AnalysisConfig
) -> float | None:
    """Return a course heading only when GPS movement is coherent enough to trust.

    Successive position vectors are combined instead of relying on a single pair
    of endpoints. The resultant/path ratio rejects stationary GPS wander, sharp
    turns, and turnarounds where an ahead/behind label would be ambiguous.
    """
    if len(points) < 2:
        return None
    north_total = 0.0
    east_total = 0.0
    path_distance = 0.0
    for first, last in pairwise(points):
        elapsed = (last.timestamp - first.timestamp).total_seconds()
        if elapsed <= 0 or elapsed > config.maximum_sample_gap_seconds:
            return None
        north = math.radians(last.latitude - first.latitude) * 6_371_008.8
        east = (
            math.radians(last.longitude - first.longitude)
            * 6_371_008.8
            * math.cos(math.radians((first.latitude + last.latitude) / 2))
        )
        distance = math.hypot(north, east)
        north_total += north
        east_total += east
        path_distance += distance
    displacement = math.hypot(north_total, east_total)
    if (
        path_distance < config.minimum_heading_displacement_m
        or displacement < config.minimum_heading_displacement_m
        or displacement / path_distance < config.minimum_heading_consistency
    ):
        return None
    return math.degrees(math.atan2(east_total, north_total)) % 360


def _angle_difference(first: float, second: float) -> float:
    return abs((first - second + 180) % 360 - 180)


def _build_sections(frames: list[_Frame], config: AnalysisConfig) -> list[_Section]:
    sections: list[_Section] = []
    current: list[_Frame] = []
    for frame in frames:
        if not frame.stable_pedaling or frame.heading_deg is None or frame.power_w is None:
            _finish_section_run(current, sections, config)
            continue
        if current and (
            (frame.point.timestamp - current[-1].point.timestamp).total_seconds()
            > config.maximum_sample_gap_seconds
            or _angle_difference(frame.heading_deg, current[-1].heading_deg or 0) > 20
        ):
            _finish_section_run(current, sections, config)
        current.append(frame)
    _finish_section_run(current, sections, config)
    return sections


def _finish_section_run(
    current: list[_Frame], sections: list[_Section], config: AnalysisConfig
) -> None:
    """Create overlapping windows so event timing cannot hide an anomaly.

    FIT samples are commonly one second apart, but drafting does not begin on a
    detector-defined ten-second boundary. A half-window stride gives every
    sustained event at least one well-aligned comparison window while retaining
    the duration requirement applied later to the merged candidate frames.
    """
    if len(current) < 3:
        current.clear()
        return
    timestamps = [frame.point.timestamp for frame in current]
    window = timedelta(seconds=config.comparison_section_seconds)
    stride = timedelta(seconds=max(1.0, config.comparison_section_stride_seconds))
    anchor = timestamps[0]
    last_timestamp = timestamps[-1]
    while anchor < last_timestamp:
        left = bisect_left(timestamps, anchor)
        right = bisect_right(timestamps, anchor + window)
        _append_section(current[left:right], sections, config)
        anchor += stride
    current.clear()


def _append_section(
    frames: list[_Frame], sections: list[_Section], config: AnalysisConfig
) -> None:
    if len(frames) < 3:
        return
    elapsed = (frames[-1].point.timestamp - frames[0].point.timestamp).total_seconds()
    speeds = [frame.speed_mps for frame in frames]
    grades = [frame.gradient for frame in frames]
    if (
        elapsed < min(5.0, config.comparison_section_seconds / 2)
        or max(speeds) - min(speeds) > 1.5
        or max(grades) - min(grades) > 0.02
    ):
        return
    sections.append(
        _Section(
            frames=tuple(frames),
            start=frames[0].point.timestamp,
            end=frames[-1].point.timestamp,
            speed_mps=fmean(speeds),
            gradient=fmean(grades),
            acceleration_mps2=fmean(frame.acceleration_mps2 for frame in frames),
            heading_deg=_circular_mean(
                [frame.heading_deg for frame in frames if frame.heading_deg is not None]
            ),
            power_w=fmean(frame.power_w for frame in frames if frame.power_w is not None),
        )
    )


def _power_evidence_strength(evidence: _PowerEvidence) -> float:
    return evidence.deficit_w / max(evidence.reference_w, 1.0)


def _circular_mean(angles: list[float]) -> float:
    east = fmean(math.sin(math.radians(angle)) for angle in angles)
    north = fmean(math.cos(math.radians(angle)) for angle in angles)
    return math.degrees(math.atan2(east, north)) % 360


def _compare_sections(
    sections: list[_Section], profile: RiderProfile, config: AnalysisConfig
) -> list[tuple[_Section, _PowerEvidence]]:
    matches: list[tuple[_Section, _PowerEvidence]] = []
    for section in sections:
        peers = [
            other for other in sections
            if other is not section
            and config.comparison_exclusion_seconds
            <= _section_gap(section, other)
            <= config.comparison_max_seconds
            and _angle_difference(section.heading_deg, other.heading_deg) <= 20
            and abs(section.gradient - other.gradient) <= config.similar_gradient_tolerance
            and abs(section.acceleration_mps2 - other.acceleration_mps2)
            <= config.similar_acceleration_tolerance_mps2
            and abs(section.speed_mps - other.speed_mps) <= 3.5
        ]
        if len(peers) < max(2, config.minimum_comparison_sections):
            continue
        same_speed = [
            peer for peer in peers
            if abs(peer.speed_mps - section.speed_mps) <= config.similar_speed_tolerance_mps
        ]
        selected = same_speed if len(same_speed) >= 2 else peers
        matched_speed = selected is same_speed
        references = [
            reference for peer in selected
            if (reference := _reference_power(peer, section, profile, matched_speed)) is not None
        ]
        if len(references) < 2:
            continue
        reference_w = median(references)
        deficit_w = reference_w - section.power_w
        mad_w = median(abs(value - reference_w) for value in references)
        min_w = config.minimum_power_deficit_w if matched_speed else max(60.0, config.minimum_power_deficit_w)
        min_ratio = config.minimum_power_deficit_ratio if matched_speed else max(0.20, config.minimum_power_deficit_ratio)
        if deficit_w < max(min_w, reference_w * min_ratio, 2.5 * max(20.0, 1.4826 * mad_w)):
            continue
        # A drag saving cannot exceed the whole aerodynamic part of solo power.
        aero_w = max(0.0, reference_w - _mechanical_power(section, profile))
        if aero_w < 50 or deficit_w > 0.7 * aero_w:
            continue
        matches.append(
            (section, _PowerEvidence(reference_w, deficit_w, len(references), matched_speed))
        )
    return matches


def _mechanical_power(section: _Section, profile: RiderProfile) -> float:
    speed = section.speed_mps
    mass = profile.total_mass_kg
    wheel_w = (
        profile.crr * mass * GRAVITY_MPS2 * speed
        + mass * GRAVITY_MPS2 * section.gradient * speed
        + mass * section.acceleration_mps2 * speed
    )
    return wheel_w / profile.drivetrain_efficiency


def _reference_power(
    peer: _Section, target: _Section, profile: RiderProfile, matched_speed: bool
) -> float | None:
    if matched_speed:
        return peer.power_w + _mechanical_power(target, profile) - _mechanical_power(peer, profile)
    peer_aero_w = peer.power_w - _mechanical_power(peer, profile)
    if peer_aero_w <= 0:
        return None
    return _mechanical_power(target, profile) + peer_aero_w * (
        target.speed_mps / peer.speed_mps
    ) ** 3


def _section_gap(first: _Section, second: _Section) -> float:
    if first.end < second.start:
        return (second.start - first.end).total_seconds()
    if second.end < first.start:
        return (first.start - second.end).total_seconds()
    return 0.0


def _trailing_evidence(
    frames_by_rider: dict[str, list[_Frame]], config: AnalysisConfig
) -> dict[tuple[str, datetime], _RiderRelationship]:
    if len(frames_by_rider) < 2:
        return {}
    timestamps = {
        rider_id: [frame.point.timestamp for frame in frames]
        for rider_id, frames in frames_by_rider.items()
    }
    possible: dict[tuple[str, datetime], _RiderRelationship] = {}
    for rider_id, frames in frames_by_rider.items():
        for frame in frames:
            if frame.heading_deg is None or frame.speed_mps < config.minimum_speed_mps:
                continue
            best: _RiderRelationship | None = None
            for other_id, others in frames_by_rider.items():
                if other_id == rider_id:
                    continue
                index = bisect_left(timestamps[other_id], frame.point.timestamp)
                for candidate_index in (index - 1, index):
                    if not 0 <= candidate_index < len(others):
                        continue
                    other = others[candidate_index]
                    if abs((other.point.timestamp - frame.point.timestamp).total_seconds()) > 2:
                        continue
                    if (
                        other.heading_deg is None
                        or _angle_difference(frame.heading_deg, other.heading_deg)
                        > config.maximum_pair_heading_difference_deg
                    ):
                        continue
                    if abs(frame.speed_mps - other.speed_mps) > 2:
                        continue
                    north = math.radians(other.point.latitude - frame.point.latitude) * 6_371_008.8
                    east = math.radians(other.point.longitude - frame.point.longitude) * 6_371_008.8 * math.cos(math.radians(frame.point.latitude))
                    angle = math.radians(frame.heading_deg)
                    ahead = north * math.cos(angle) + east * math.sin(angle)
                    lateral = abs(east * math.cos(angle) - north * math.sin(angle))
                    if 2 <= ahead <= config.proximity_threshold_m and lateral <= 5:
                        separation = math.hypot(ahead, lateral)
                        if best is None or separation < best.separation_m:
                            best = _RiderRelationship(
                                rider_ahead_id=other_id,
                                separation_m=separation,
                                rider_ahead_speed_mps=other.speed_mps,
                                rider_ahead_power_w=other.power_w,
                            )
            if best is not None:
                possible[(rider_id, frame.point.timestamp)] = best

    # GPS can briefly place riders close during a legal pass or because of normal
    # position error. Keep only an unchanged ahead/behind relationship that lasts
    # longer than the configured confirmation window.
    confirmed: dict[tuple[str, datetime], _RiderRelationship] = {}
    for rider_id, frames in frames_by_rider.items():
        run: list[tuple[datetime, _RiderRelationship]] = []
        for frame in frames:
            key = (rider_id, frame.point.timestamp)
            relationship = possible.get(key)
            continues = bool(
                run
                and relationship is not None
                and relationship.rider_ahead_id == run[-1][1].rider_ahead_id
                and (frame.point.timestamp - run[-1][0]).total_seconds()
                <= config.maximum_sample_gap_seconds
            )
            if not continues:
                _confirm_relationship_run(run, rider_id, confirmed, config)
                run = []
            if relationship is not None:
                run.append((frame.point.timestamp, relationship))
        _confirm_relationship_run(run, rider_id, confirmed, config)
    return confirmed


def _confirm_relationship_run(
    run: list[tuple[datetime, _RiderRelationship]],
    rider_id: str,
    confirmed: dict[tuple[str, datetime], _RiderRelationship],
    config: AnalysisConfig,
) -> None:
    if not run:
        return
    observed_seconds = (run[-1][0] - run[0][0]).total_seconds()
    if observed_seconds <= config.minimum_proximity_seconds:
        return
    for timestamp, relationship in run:
        confirmed[(rider_id, timestamp)] = relationship


def _runs(candidates: list[_Candidate], max_gap: float) -> list[list[_Candidate]]:
    runs: list[list[_Candidate]] = []
    for candidate in candidates:
        if not runs or (candidate.frame.point.timestamp - runs[-1][-1].frame.point.timestamp).total_seconds() > max_gap:
            runs.append([candidate])
        else:
            runs[-1].append(candidate)
    return runs


def _run_duration(run: list[_Candidate]) -> float:
    observed = (run[-1].frame.point.timestamp - run[0].frame.point.timestamp).total_seconds()
    intervals = [
        (right.frame.point.timestamp - left.frame.point.timestamp).total_seconds()
        for left, right in pairwise(run)
    ]
    return observed + min(median(intervals), 5.0) if intervals else 1.0


def _segment_from_run(
    rider_id: str, run: list[_Candidate], duration: float, config: AnalysisConfig
) -> SuspiciousSegment:
    power = [item.power for item in run if item.power is not None]
    trailing = [item for item in run if item.leader_id is not None]
    separation = fmean(item.separation_m for item in trailing if item.separation_m is not None) if trailing else None
    reference = fmean(item.reference_w for item in power) if power else None
    measured = [item.frame.power_w for item in run if item.frame.power_w is not None]
    leader_speeds = [item.leader_speed_mps for item in trailing if item.leader_speed_mps is not None]
    leader_powers = [item.leader_power_w for item in trailing if item.leader_power_w is not None]
    power_score = min(1.0, fmean(item.deficit_w / item.reference_w for item in power) / 0.35) if power else 0.0
    proximity_score = max(0.0, 1 - separation / config.proximity_threshold_m) if separation is not None else 0.0
    duration_score = min(1.0, duration / 45)
    speed_score = min(1.0, fmean(item.frame.speed_mps for item in run) / 15)
    telemetry_confidence = min(0.95, 0.5 + (0.2 if power else 0) + (0.2 if trailing else 0))
    leader_ids = [item.leader_id for item in trailing if item.leader_id is not None]
    leader = max(set(leader_ids), key=leader_ids.count) if leader_ids else None
    headings = [item.frame.heading_deg for item in run if item.frame.heading_deg is not None]
    # Heuristic review priority, not a posterior probability of drafting.
    score = min(0.95, 0.55 + 0.14 * duration_score + 0.15 * power_score + 0.12 * proximity_score + (0.08 if power and trailing else 0))
    notes: list[str] = []
    if power:
        peer_count = max(item.peer_count for item in power)
        notes.append(
            f"Power was lower than {peer_count} comparable sections from this rider's activity "
            "after matching travel direction, speed, grade, and acceleration."
        )
        if not all(item.matched_speed for item in power):
            notes.append("Some comparison sections required speed normalization, which increases wind uncertainty.")
    if trailing:
        notes.append(
            f"Synchronized GPS places rider {rider_id} behind rider "
            f"{leader} "
            f"for more than {config.minimum_proximity_seconds:.0f} seconds; GPS distance is approximate."
        )
    notes.append("Wind, riding position, road surface, and sensor error can produce similar patterns.")
    distances = [item.frame.point.distance_m for item in run if item.frame.point.distance_m is not None]
    return SuspiciousSegment(
        rider_id=rider_id,
        start_time=run[0].frame.point.timestamp,
        end_time=run[-1].frame.point.timestamp,
        score=round(score, 3),
        evidence=Evidence(
            proximity_score=round(proximity_score, 3),
            duration_score=round(duration_score, 3),
            power_score=round(power_score, 3),
            speed_score=round(speed_score, 3),
            telemetry_confidence=round(telemetry_confidence, 3),
            notes=tuple(notes),
        ),
        nearest_rider_id=leader,
        rider_ahead_id=leader,
        rider_behind_id=rider_id if leader is not None else None,
        average_separation_m=round(separation, 1) if separation is not None else None,
        rider_ahead_speed_mps=round(fmean(leader_speeds), 2) if leader_speeds else None,
        rider_ahead_power_w=round(fmean(leader_powers), 1) if leader_powers else None,
        rider_behind_speed_mps=round(fmean(item.frame.speed_mps for item in trailing), 2) if trailing else None,
        rider_behind_power_w=round(
            fmean(item.frame.power_w for item in trailing if item.frame.power_w is not None), 1
        ) if any(item.frame.power_w is not None for item in trailing) else None,
        direction_heading_deg=round(_circular_mean(headings), 1) if headings else None,
        average_speed_mps=round(fmean(item.frame.speed_mps for item in run), 2),
        average_power_w=round(fmean(measured), 1) if measured else None,
        expected_power_w=round(reference, 1) if reference is not None else None,
        latitude=round(fmean(item.frame.point.latitude for item in run), 6),
        longitude=round(fmean(item.frame.point.longitude for item in run), 6),
        course_distance_m=round(fmean(distances), 1) if distances else None,
    )
