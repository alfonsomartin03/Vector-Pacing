"""Potential-drafting detection for events where drafting is restricted (e.g. ITTs).

This module is independent of the pacing simulator/optimizer. It reports
*potential* drafting intervals for human review. It never confirms a rules
violation and never assigns penalties. See docs/DRAFTING.md for the full
method, the import format and its limitations.

Pipeline
--------
1. **Import** (:func:`parse_scenario`): a ``vector-pacing-riders/1`` JSON document
   with a course line and timestamped rider positions (lat/lon, optional
   horizontal accuracy).
2. **Course positions**: every sample is projected onto the course line in a
   local metric frame. Projection is *continuity-constrained*: each sample is
   only matched to course within a window around the rider's previous course
   position, so out-and-back lanes or adjacent sections a few metres apart are
   not confused.
3. **Synchronisation**: each rider is resampled onto a common time grid
   (``analysis_step_s``) by linear interpolation. Grid points are *missing* if the
   surrounding raw samples are further apart than ``max_interpolation_gap_s``.
   A centred rolling median (``smoothing_s``) reduces GNSS jitter.
4. **Zone test** for every ordered pair (follower, leader) and grid time::

       0 < gap ≤ zone_length           (gap = leader course position − follower's)
       both moving, same direction     (velocity vectors point the same way)
       physical distance ≤ zone_length + 3σ   (consistency check)
       not laterally separated         (only if both accuracies ≤ lateral_accuracy_max_m)

   ``σ = √(acc_f² + acc_l² + (timestamp_tolerance_s · speed)²)`` is the
   combined 1-σ gap uncertainty.
5. **Intervals**: in-zone times are joined into intervals (gaps ≤ ``merge_gap_s``
   bridged). Each interval is classified with the configured rules:

   * ``overtake``         — follower got ahead within ``allowed_pass_s``
   * ``drop_back``        — follower had just been overtaken and left the zone
                            within ``drop_back_s``
   * ``brief_encounter``  — in the zone no longer than the allowance, no pass
   * ``potential_drafting`` — in the zone longer than the allowance
   * ``inconclusive``     — data quality too poor to tell following from passing
                            or riding apart (or probability inside the zone < 60 %)
   * ``exempt``           — entirely inside configured exempt course sections

   Time inside configured exempt course sections does not count.
6. **Dismissed proximity**: riders physically within the zone length who are
   *not* following are reported as ``separate_course_section``,
   ``opposite_direction``, ``lateral_separation`` or ``stationary`` encounters so the
   distinction is visible.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib import resources
from typing import Any, Literal

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

FORMAT_ID = "vector-pacing-riders/1"
DRAFT_MODEL_VERSION = "vp-draft-1.0"
EARTH_RADIUS_M = 6_371_008.8
MAX_RIDERS = 200
MAX_TOTAL_SAMPLES = 500_000
MAX_DURATION_S = 24 * 3600
MOVING_SPEED_MS = 1.0
OFF_COURSE_M = 50.0
PROJECTION_SPEED_MS = 25.0  # used to bound the continuity search window
PROJECTION_MARGIN_M = 50.0
MIN_CONFIDENCE = 0.6  # mean probability inside the zone needed to flag potential drafting

Classification = Literal["potential_drafting", "inconclusive", "overtake", "drop_back", "brief_encounter", "exempt"]
EncounterType = Literal["separate_course_section", "opposite_direction", "lateral_separation", "stationary"]

DISCLAIMER = (
    "Potential drafting only. Intervals are derived from GNSS position estimates and the configured, "
    "illustrative settings. They are prompts for human review, not confirmed rules violations, and no "
    "penalty is implied."
)


class ScenarioError(ValueError):
    """The rider-position document is invalid. The message is user-facing."""


# ---------------------------------------------------------------- settings


@dataclass(frozen=True)
class ExemptSection:
    start_m: float
    end_m: float
    label: str = ""


@dataclass(frozen=True)
class DraftingSettings:
    """Detection settings. Defaults are illustrative, not any governing body's rules."""

    zone_length_m: float = 10.0
    lateral_separation_m: float = 2.0
    allowed_pass_s: float = 20.0
    drop_back_s: float = 20.0
    exempt_sections: tuple[ExemptSection, ...] = ()
    smoothing_s: float = 5.0
    max_interpolation_gap_s: float = 5.0
    merge_gap_s: float = 5.0
    timestamp_tolerance_s: float = 0.2
    assumed_accuracy_m: float = 5.0
    lateral_accuracy_max_m: float = 1.0
    analysis_step_s: float = 1.0


ILLUSTRATIVE_PRESETS: dict[str, dict[str, Any]] = {
    "illustrative_standard": {
        "label": "Illustrative — standard (10 m / 20 s)",
        "description": "Example values for demonstration. Not taken from any governing body.",
        "settings": {"zone_length_m": 10.0, "allowed_pass_s": 20.0, "drop_back_s": 20.0, "lateral_separation_m": 2.0},
    },
    "illustrative_long_zone": {
        "label": "Illustrative — long zone (15 m / 30 s)",
        "description": "Example of a longer zone with more time to pass. Not taken from any governing body.",
        "settings": {"zone_length_m": 15.0, "allowed_pass_s": 30.0, "drop_back_s": 30.0, "lateral_separation_m": 2.0},
    },
    "illustrative_short_zone": {
        "label": "Illustrative — short zone (7 m / 15 s)",
        "description": "Example of a shorter zone and stricter passing time. Not taken from any governing body.",
        "settings": {"zone_length_m": 7.0, "allowed_pass_s": 15.0, "drop_back_s": 15.0, "lateral_separation_m": 1.5},
    },
}


# ---------------------------------------------------------------- import


@dataclass
class RiderTrack:
    id: str
    label: str
    t: np.ndarray  # seconds, relative to scenario origin, strictly increasing
    lat: np.ndarray
    lon: np.ndarray
    accuracy_m: np.ndarray
    accuracy_reported: bool
    warnings: list[str] = field(default_factory=list)


@dataclass
class Scenario:
    name: str
    description: str
    illustrative: bool
    source: str
    content_hash: str
    start_time: str | None  # ISO 8601 of t = 0 when known
    course_latlon: np.ndarray  # (n, 2)
    course_name: str
    riders: list[RiderTrack]
    warnings: list[str] = field(default_factory=list)


def _num(v: Any, what: str) -> float:
    if isinstance(v, bool) or not isinstance(v, int | float):
        raise ScenarioError(f"{what} must be a number.")
    f = float(v)
    if not math.isfinite(f):
        raise ScenarioError(f"{what} must be finite.")
    return f


def _parse_time(v: Any, what: str) -> float:
    if isinstance(v, str):
        try:
            dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            raise ScenarioError(f"{what} is not an ISO 8601 timestamp: {v!r}.") from None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.timestamp()
    return _num(v, what)


def _rows_from_samples(samples: Any, rid: str) -> list[dict[str, Any]]:
    """Accept row form (list of objects) or columnar form (object of equal-length arrays)."""
    if isinstance(samples, list):
        if not all(isinstance(s, dict) for s in samples):
            raise ScenarioError(f"Rider {rid}: every sample must be an object.")
        return samples
    if isinstance(samples, dict):
        lengths = {k: len(v) for k, v in samples.items() if isinstance(v, list)}
        if not lengths or len(set(lengths.values())) != 1 or len(lengths) != len(samples):
            raise ScenarioError(f"Rider {rid}: columnar samples must be arrays of equal length.")
        n = next(iter(lengths.values()))
        return [{k: samples[k][i] for k in samples} for i in range(n)]
    raise ScenarioError(f"Rider {rid}: 'samples' must be a list of objects or an object of arrays.")


def parse_scenario(doc: Any, source: str = "upload", min_riders: int = 2) -> Scenario:
    """Validate a ``vector-pacing-riders/1`` document. Raises :class:`ScenarioError`."""
    if not isinstance(doc, dict):
        raise ScenarioError("The document must be a JSON object.")
    if doc.get("format") != FORMAT_ID:
        raise ScenarioError(f'Unsupported format: expected "format": "{FORMAT_ID}".')
    course = doc.get("course")
    if not isinstance(course, dict) or not isinstance(course.get("points"), list):
        raise ScenarioError("'course.points' must be a list of [lat, lon] pairs.")
    pts = []
    for i, p in enumerate(course["points"]):
        if not isinstance(p, list | tuple) or len(p) < 2:
            raise ScenarioError(f"Course point {i + 1} must be [lat, lon].")
        lat, lon = _num(p[0], f"Course point {i + 1} latitude"), _num(p[1], f"Course point {i + 1} longitude")
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ScenarioError(f"Course point {i + 1} is out of range.")
        pts.append((lat, lon))
    if len(pts) < 2:
        raise ScenarioError("The course needs at least two points.")

    riders_doc = doc.get("riders")
    if not isinstance(riders_doc, list) or len(riders_doc) < min_riders:
        raise ScenarioError("'riders' must list at least two riders." if min_riders == 2 else "No riders were given.")
    if len(riders_doc) > MAX_RIDERS:
        raise ScenarioError(f"At most {MAX_RIDERS} riders are supported.")

    origin = doc.get("start_time")
    origin_ts = _parse_time(origin, "start_time") if isinstance(origin, str) else None
    raw: list[tuple[str, str, list[tuple[float, float, float, float | None]], list[str]]] = []
    seen: set[str] = set()
    total = 0
    uses_iso = None
    for r in riders_doc:
        if not isinstance(r, dict) or not isinstance(r.get("id"), str) or not r["id"].strip():
            raise ScenarioError("Every rider needs a non-empty string 'id'.")
        rid = r["id"].strip()[:40]
        if rid in seen:
            raise ScenarioError(f"Duplicate rider id {rid!r}.")
        seen.add(rid)
        rows = _rows_from_samples(r.get("samples"), rid)
        total += len(rows)
        if total > MAX_TOTAL_SAMPLES:
            raise ScenarioError(f"More than {MAX_TOTAL_SAMPLES:,} samples in total.")
        samples: list[tuple[float, float, float, float | None]] = []
        warns: list[str] = []
        dropped = 0
        for k, s in enumerate(rows):
            if "time" in s:
                iso = True
                t = _parse_time(s["time"], f"Rider {rid} sample {k + 1} time")
            elif "t" in s:
                iso = False
                t = _num(s["t"], f"Rider {rid} sample {k + 1} t")
            else:
                raise ScenarioError(f"Rider {rid} sample {k + 1} needs 't' (seconds) or 'time' (ISO 8601).")
            if uses_iso is None:
                uses_iso = iso
            elif uses_iso != iso:
                raise ScenarioError("Use either 't' or 'time' consistently for all samples.")
            lat_raw, lon_raw = s.get("lat"), s.get("lon")
            if lat_raw is None or lon_raw is None:
                dropped += 1
                continue
            lat, lon = _num(lat_raw, f"Rider {rid} latitude"), _num(lon_raw, f"Rider {rid} longitude")
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise ScenarioError(f"Rider {rid} sample {k + 1} has out-of-range coordinates.")
            acc = s.get("accuracy_m")
            acc_v = None
            if acc is not None:
                acc_v = _num(acc, f"Rider {rid} accuracy_m")
                if acc_v <= 0 or acc_v > 1000:
                    raise ScenarioError(f"Rider {rid} sample {k + 1}: accuracy_m must be in (0, 1000].")
            samples.append((t, lat, lon, acc_v))
        if dropped:
            warns.append(f"{dropped} sample(s) without a position were ignored.")
        label = str(r.get("label") or rid)[:80]
        raw.append((rid, label, samples, warns))

    all_t = [s[0] for _, _, samples, _ in raw for s in samples]
    if not all_t:
        raise ScenarioError("No rider samples with positions were found.")
    t0 = origin_ts if (uses_iso and origin_ts is not None) else (min(all_t) if uses_iso else 0.0)
    start_time = origin if isinstance(origin, str) else None
    if uses_iso and start_time is None:
        start_time = datetime.fromtimestamp(t0, UTC).isoformat().replace("+00:00", "Z")

    riders: list[RiderTrack] = []
    for rid, label, samples, warns in raw:
        if len(samples) < 2:
            raise ScenarioError(f"Rider {rid} needs at least two samples with positions.")
        arr = sorted(samples, key=lambda s: s[0])
        ts = np.array([s[0] for s in arr]) - t0
        keep = np.concatenate([[True], np.diff(ts) > 1e-6])
        if (~keep).any():
            warns.append(f"{int((~keep).sum())} sample(s) with duplicate timestamps were ignored.")
        if any(a[0] != b[0] for a, b in zip(samples, arr, strict=True)):
            warns.append("Samples were not in time order and were sorted.")
        arr = [s for s, k in zip(arr, keep, strict=True) if k]
        ts = ts[keep]
        if ts[-1] - ts[0] > MAX_DURATION_S:
            raise ScenarioError(f"Rider {rid} spans more than 24 hours.")
        reported = all(s[3] is not None for s in arr)
        if not reported and any(s[3] is not None for s in arr):
            warns.append("Accuracy was missing for some samples; the assumed accuracy was used there.")
        riders.append(
            RiderTrack(
                id=rid,
                label=label,
                t=ts,
                lat=np.array([s[1] for s in arr]),
                lon=np.array([s[2] for s in arr]),
                accuracy_m=np.array([np.nan if s[3] is None else s[3] for s in arr]),
                accuracy_reported=reported,
                warnings=warns,
            )
        )

    canonical = json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()
    return Scenario(
        name=str(doc.get("name") or "Uploaded rider data")[:120],
        description=str(doc.get("description") or "")[:2000],
        illustrative=bool(doc.get("illustrative", False)),
        source=source,
        content_hash=hashlib.sha256(canonical).hexdigest()[:16],
        start_time=start_time,
        course_latlon=np.array(pts, dtype=float),
        course_name=str(course.get("name") or "Course")[:120],
        riders=riders,
    )


_demo_cache: Scenario | None = None


def load_demo_scenario() -> Scenario:
    global _demo_cache
    if _demo_cache is None:
        raw = resources.files("vector_pacing").joinpath("data/drafting_demo.json").read_text(encoding="utf-8")
        _demo_cache = parse_scenario(json.loads(raw), source="demo")
    return _demo_cache


# ---------------------------------------------------------------- geometry


class CourseLine:
    """Course polyline in a local equirectangular metric frame (fine for event-sized areas)."""

    def __init__(self, latlon: np.ndarray):
        self.lat0 = float(np.mean(latlon[:, 0]))
        self.lon0 = float(np.mean(latlon[:, 1]))
        xy = self.to_xy(latlon[:, 0], latlon[:, 1])
        seg = np.hypot(np.diff(xy[:, 0]), np.diff(xy[:, 1]))
        keep = np.concatenate([[True], seg > 1e-3])
        xy = xy[keep]
        if len(xy) < 2:
            raise ScenarioError("The course has zero length.")
        self.xy = xy
        self.a = xy[:-1]
        self.d = np.diff(xy, axis=0)
        self.seg_len = np.hypot(self.d[:, 0], self.d[:, 1])
        self.cum = np.concatenate([[0.0], np.cumsum(self.seg_len)])
        self.length_m = float(self.cum[-1])

    def to_xy(self, lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
        x = EARTH_RADIUS_M * np.radians(np.asarray(lon) - self.lon0) * math.cos(math.radians(self.lat0))
        y = EARTH_RADIUS_M * np.radians(np.asarray(lat) - self.lat0)
        return np.column_stack([x, y])

    def project(
        self, p: np.ndarray, lo: int, hi: int, s_pred: float | None = None, acc: float = 1.0
    ) -> tuple[float, float, float]:
        """Project ``p`` onto segments ``lo..hi-1`` → (course_m, signed left offset, distance).

        Without ``s_pred`` the nearest point wins (ties → earliest course position). With ``s_pred``,
        segments within ``max(3·acc, 10 m)`` of the nearest match are grouped into contiguous course
        sections; the section closest to the predicted course position is chosen and the point within it is
        the purely geometric nearest point. The prediction therefore only disambiguates *which* section
        (e.g. outbound vs. return lane), never shifts the position along it.
        """
        a, d, ln = self.a[lo:hi], self.d[lo:hi], self.seg_len[lo:hi]
        rel = p - a
        u = np.clip((rel[:, 0] * d[:, 0] + rel[:, 1] * d[:, 1]) / ln**2, 0.0, 1.0)
        q = a + d * u[:, None]
        dist = np.hypot(p[0] - q[:, 0], p[1] - q[:, 1])
        s = self.cum[lo:hi] + u * ln
        if s_pred is None:
            best = float(dist.min())
            cand = np.flatnonzero(dist <= best + 1e-9)
            k = int(cand[np.argmin(s[cand])])
        else:
            cand = np.flatnonzero(dist <= float(dist.min()) + max(3.0 * acc, 10.0))
            groups = np.split(cand, np.flatnonzero(np.diff(cand) > 1) + 1)
            k = min(
                (int(g[np.argmin(dist[g])]) for g in groups),
                key=lambda j: abs(float(s[j]) - s_pred) + float(dist[j]),
            )
        cross = d[k, 0] * rel[k, 1] - d[k, 1] * rel[k, 0]
        return float(s[k]), float(cross / ln[k]), float(dist[k])

    def segment_range(self, s_lo: float, s_hi: float) -> tuple[int, int]:
        lo = max(0, int(np.searchsorted(self.cum, s_lo, side="right")) - 1)
        hi = min(len(self.seg_len), int(np.searchsorted(self.cum, s_hi, side="left")) + 1)
        return lo, max(hi, lo + 1)


@dataclass
class ProjectedTrack:
    s: np.ndarray
    offset: np.ndarray
    off_course_m: np.ndarray
    xy: np.ndarray


def project_track(
    course: CourseLine, rider: RiderTrack, assumed_accuracy_m: float = 5.0, start_ambiguity_m: float = 10.0
) -> ProjectedTrack:
    """Continuity-constrained projection of a rider's samples onto the course."""
    xy = course.to_xy(rider.lat, rider.lon)
    n = len(xy)
    s = np.empty(n)
    off = np.empty(n)
    dist = np.empty(n)
    # First sample: several course sections may be about equally close (start = finish on
    # out-and-back courses, adjacent lanes). Among near-equal matches pick the section whose direction best
    # matches the rider's initial heading; if the rider has not moved yet, prefer the earliest position.
    a, d, ln = course.a, course.d, course.seg_len
    rel = xy[0] - a
    u = np.clip((rel[:, 0] * d[:, 0] + rel[:, 1] * d[:, 1]) / ln**2, 0.0, 1.0)
    q = a + d * u[:, None]
    dd = np.hypot(xy[0, 0] - q[:, 0], xy[0, 1] - q[:, 1])
    ss = course.cum[:-1] + u * ln
    cand = np.flatnonzero(dd <= dd.min() + start_ambiguity_m)
    moved = np.flatnonzero(np.hypot(*(xy - xy[0]).T) > 20.0)
    if moved.size and cand.size > 1:
        h = xy[moved[0]] - xy[0]
        align = (d[cand, 0] * h[0] + d[cand, 1] * h[1]) / (ln[cand] * np.hypot(*h))
        best = cand[align >= align.max() - 0.05]
        k = int(best[np.argmin(dd[best])])
    else:
        k = int(cand[np.argmin(ss[cand])])
    lo, hi = course.segment_range(ss[k] - 1.0, ss[k] + 1.0)
    s[0], off[0], dist[0] = course.project(xy[0], lo, hi)
    acc = np.where(np.isfinite(rider.accuracy_m), rider.accuracy_m, assumed_accuracy_m)
    v_prev = 0.0
    for i in range(1, n):
        dt = rider.t[i] - rider.t[i - 1]
        w = PROJECTION_SPEED_MS * dt + PROJECTION_MARGIN_M
        lo, hi = course.segment_range(s[i - 1] - w, s[i - 1] + w)
        s_pred = s[i - 1] + v_prev * dt
        s[i], off[i], dist[i] = course.project(xy[i], lo, hi, s_pred, max(float(acc[i]), 0.5))
        v_prev = float(np.clip((s[i] - s[i - 1]) / dt, -PROJECTION_SPEED_MS, PROJECTION_SPEED_MS))
    return ProjectedTrack(s=s, offset=off, off_course_m=dist, xy=xy)


# ---------------------------------------------------------------- synchronisation


def _rolling_nanmedian(x: np.ndarray, window: int) -> np.ndarray:
    if window <= 1 or x.size < window:
        return x
    half = window // 2
    padded = np.concatenate([np.full(half, np.nan), x, np.full(half, np.nan)])
    view = sliding_window_view(padded, window)
    out = np.full_like(x, np.nan)
    valid = np.isfinite(x)
    with np.errstate(all="ignore"):
        if valid.any():
            out[valid] = np.nanmedian(view[valid], axis=1)
    return out


@dataclass
class GridTrack:
    s: np.ndarray
    offset: np.ndarray
    x: np.ndarray
    y: np.ndarray
    acc: np.ndarray
    vx: np.ndarray
    vy: np.ndarray
    vs: np.ndarray
    valid: np.ndarray


def resample(
    rider: RiderTrack, proj: ProjectedTrack, grid: np.ndarray, settings: DraftingSettings
) -> tuple[GridTrack, int]:
    t = rider.t
    j = np.searchsorted(t, grid, side="right")
    inside = (grid >= t[0]) & (grid <= t[-1])
    jl = np.clip(j - 1, 0, len(t) - 1)
    jr = np.clip(j, 0, len(t) - 1)
    exact = np.isclose(grid, t[jl], atol=1e-9)
    bracket = t[jr] - t[jl]
    valid = inside & (exact | (bracket <= settings.max_interpolation_gap_s + 1e-9))
    acc_raw = np.where(np.isfinite(rider.accuracy_m), rider.accuracy_m, settings.assumed_accuracy_m)

    def interp(v: np.ndarray) -> np.ndarray:
        return np.where(valid, np.interp(grid, t, v), np.nan)

    w = max(1, round(settings.smoothing_s / settings.analysis_step_s))
    if w % 2 == 0:
        w += 1
    s = _rolling_nanmedian(interp(proj.s), w)
    off = _rolling_nanmedian(interp(proj.offset), w)
    x = _rolling_nanmedian(interp(proj.xy[:, 0]), w)
    y = _rolling_nanmedian(interp(proj.xy[:, 1]), w)
    acc = interp(acc_raw)
    step = settings.analysis_step_s
    with np.errstate(all="ignore"):
        vs = np.gradient(s, step) if s.size > 1 else np.zeros_like(s)
        vx = np.gradient(x, step) if x.size > 1 else np.zeros_like(x)
        vy = np.gradient(y, step) if y.size > 1 else np.zeros_like(y)
    valid = valid & np.isfinite(s) & np.isfinite(vx) & np.isfinite(vy)
    active = int(inside.sum())
    return GridTrack(s, off, x, y, acc, vx, vy, vs, valid), active


# ---------------------------------------------------------------- detection


@dataclass
class Interval:
    follower: str
    leader: str
    classification: Classification
    i0: int
    i1: int
    started_by: str
    ended_by: str
    duration_s: float
    counted_s: float
    exempt_s: float
    missing_s: float
    gap_median_m: float
    gap_min_m: float
    gap_max_m: float
    sigma_median_m: float
    confident_inside_fraction: float
    quality: Literal["good", "limited", "insufficient"]
    lateral_used: bool
    limitations: list[str]
    series: dict[str, list[float | None]]
    course_m: tuple[float, float]
    order_uncertain: bool = False


@dataclass
class Encounter:
    rider_a: str
    rider_b: str
    type: EncounterType
    i0: int
    i1: int
    duration_s: float
    min_distance_m: float
    median_course_gap_m: float


@dataclass
class RiderQuality:
    id: str
    label: str
    samples: int
    start_s: float
    end_s: float
    median_interval_s: float
    irregular_interval_fraction: float
    max_interval_s: float
    missing_fraction: float
    accuracy_median_m: float
    accuracy_reported: bool
    max_off_course_m: float
    warnings: list[str]


@dataclass
class DraftingAnalysis:
    scenario: Scenario
    settings: DraftingSettings
    course: CourseLine
    grid: np.ndarray
    intervals: list[Interval]
    encounters: list[Encounter]
    rider_quality: list[RiderQuality]
    limitations: list[str]
    tracks: dict[str, GridTrack]


def _runs(mask: np.ndarray, bridge: int) -> list[tuple[int, int]]:
    """Contiguous True runs; runs separated by ≤ ``bridge`` False samples are merged."""
    idx = np.flatnonzero(mask)
    if idx.size == 0:
        return []
    runs = []
    start = prev = int(idx[0])
    for k in idx[1:]:
        k = int(k)
        if k - prev - 1 > bridge:
            runs.append((start, prev))
            start = k
        prev = k
    runs.append((start, prev))
    return runs


def _in_sections(s: np.ndarray, sections: tuple[ExemptSection, ...]) -> np.ndarray:
    out = np.zeros(s.shape, dtype=bool)
    for sec in sections:
        out |= (s >= sec.start_m) & (s <= sec.end_m)
    return out


def _phi(z: np.ndarray) -> np.ndarray:
    return np.asarray(0.5 * (1.0 + np.vectorize(math.erf)(z / math.sqrt(2.0))))


def _r(v: float, nd: int = 2) -> float | None:
    return round(float(v), nd) if math.isfinite(v) else None


def analyze(scenario: Scenario, settings: DraftingSettings) -> DraftingAnalysis:
    course = CourseLine(scenario.course_latlon)
    step = settings.analysis_step_s
    t_min = min(float(r.t[0]) for r in scenario.riders)
    t_max = max(float(r.t[-1]) for r in scenario.riders)
    grid = np.arange(math.floor(t_min), t_max + 1e-9, step)
    if grid.size > 200_000:
        raise ScenarioError("The scenario is too long for the chosen analysis step.")

    tracks: dict[str, GridTrack] = {}
    quality: list[RiderQuality] = []
    for r in scenario.riders:
        proj = project_track(course, r, settings.assumed_accuracy_m)
        gt, active = resample(r, proj, grid, settings)
        tracks[r.id] = gt
        iv = np.diff(r.t)
        med = float(np.median(iv))
        warns = list(r.warnings)
        max_off = float(proj.off_course_m.max())
        if max_off > OFF_COURSE_M:
            warns.append(f"Up to {max_off:.0f} m from the course line; course positions may be unreliable there.")
        if not r.accuracy_reported:
            warns.append(f"Accuracy not reported; {settings.assumed_accuracy_m:g} m assumed.")
        irregular = float(np.mean(np.abs(iv - med) > 0.2 * med)) if iv.size else 0.0
        missing = 1.0 - float(gt.valid.sum()) / max(active, 1)
        if irregular > 0.1:
            warns.append("Sampling is irregular; positions were interpolated onto the common time grid.")
        if missing > 0.05:
            warns.append(f"{missing:.0%} of this rider's time span has no usable position (dropouts).")
        acc_vals = np.where(np.isfinite(r.accuracy_m), r.accuracy_m, settings.assumed_accuracy_m)
        quality.append(
            RiderQuality(
                id=r.id,
                label=r.label,
                samples=len(r.t),
                start_s=float(r.t[0]),
                end_s=float(r.t[-1]),
                median_interval_s=med,
                irregular_interval_fraction=irregular,
                max_interval_s=float(iv.max()) if iv.size else 0.0,
                missing_fraction=missing,
                accuracy_median_m=float(np.median(acc_vals)),
                accuracy_reported=r.accuracy_reported,
                max_off_course_m=max_off,
                warnings=warns,
            )
        )

    L = settings.zone_length_m
    bridge = max(0, round(settings.merge_gap_s / step))
    ids = [r.id for r in scenario.riders]
    intervals: list[Interval] = []
    in_zone_by_pair: dict[tuple[str, str], np.ndarray] = {}
    pair_cache: dict[tuple[str, str], dict[str, np.ndarray]] = {}

    for f in ids:
        for ld in ids:
            if f == ld:
                continue
            A, B = tracks[f], tracks[ld]
            valid = A.valid & B.valid
            if not valid.any():
                in_zone_by_pair[(f, ld)] = np.zeros(grid.shape, dtype=bool)
                continue
            with np.errstate(invalid="ignore"):
                gap = B.s - A.s
                dist = np.hypot(B.x - A.x, B.y - A.y)
                speed_a = np.hypot(A.vx, A.vy)
                speed_b = np.hypot(B.vx, B.vy)
                sigma = np.sqrt(
                    A.acc**2 + B.acc**2 + (settings.timestamp_tolerance_s * np.maximum(speed_a, speed_b)) ** 2
                )
                moving = (speed_a > MOVING_SPEED_MS) & (speed_b > MOVING_SPEED_MS)
                same_dir = (A.vx * B.vx + A.vy * B.vy) > 0
                lat_reliable = (A.acc <= settings.lateral_accuracy_max_m) & (B.acc <= settings.lateral_accuracy_max_m)
                lat_sep = np.abs(B.offset - A.offset)
                lateral_clear = lat_reliable & (lat_sep > settings.lateral_separation_m)
                in_zone = valid & moving & same_dir & (gap > 0) & (gap <= L) & ~lateral_clear & (dist <= L + 3 * sigma)
            in_zone_by_pair[(f, ld)] = in_zone
            pair_cache[(f, ld)] = {
                "gap": gap,
                "dist": dist,
                "sigma": sigma,
                "lat_sep": lat_sep,
                "lat_reliable": lat_reliable,
                "valid": valid,
            }
            for i0, i1 in _runs(in_zone, bridge):
                intervals.append(_classify(f, ld, i0, i1, grid, A, pair_cache[(f, ld)], settings))

    intervals = _merge_inconclusive(intervals, grid, bridge, tracks, pair_cache, settings)
    intervals.sort(key=lambda iv: (grid[iv.i0], iv.follower))
    encounters = _encounters(ids, tracks, grid, in_zone_by_pair, settings, bridge)
    limitations = [
        "Positions are GNSS estimates; accuracy values are treated as 1-σ horizontal error "
        f"(or {settings.assumed_accuracy_m:g} m where not reported).",
        "Course positions come from projecting each sample onto the course line; riders far from the line "
        "or on overlapping sections can be mis-placed.",
        f"Device clocks are assumed aligned within ±{settings.timestamp_tolerance_s:g} s; this is included in "
        "the gap uncertainty.",
        f"Lateral separation is used only when both riders report accuracy ≤ {settings.lateral_accuracy_max_m:g} m.",
        f"Positions are interpolated onto a {step:g} s grid; gaps longer than "
        f"{settings.max_interpolation_gap_s:g} s are treated as missing, and a {settings.smoothing_s:g} s "
        "rolling median reduces jitter.",
        "Aerodynamic benefit is not estimated. Road layout, obstacles, marshal instructions, and other context "
        "are not known to this analysis.",
        "Settings are configurable and the bundled values are illustrative; they are not the rules of any "
        "particular event or governing body.",
    ]
    return DraftingAnalysis(scenario, settings, course, grid, intervals, encounters, quality, limitations, tracks)


def _classify(
    f: str,
    ld: str,
    i0: int,
    i1: int,
    grid: np.ndarray,
    A: GridTrack,
    pc: dict[str, np.ndarray],
    st: DraftingSettings,
) -> Interval:
    step = st.analysis_step_s
    L = st.zone_length_m
    gap, sigma, valid = pc["gap"], pc["sigma"], pc["valid"]
    look = max(1, round(10.0 / step))

    def first_valid(rng: range) -> float | None:
        for k in rng:
            if 0 <= k < grid.size and valid[k]:
                return float(gap[k])
        return None

    before = first_valid(range(i0 - 1, i0 - 1 - look, -1))
    after = first_valid(range(i1 + 1, i1 + 1 + look))
    started_by = "overtaken" if (before is not None and before <= 0) else "approached"
    if after is None:
        ended_by = "data_unavailable"
    elif after <= 0:
        ended_by = "follower_passed"
    elif after > L:
        ended_by = "gap_opened"
    else:
        ended_by = "zone_conditions_ended"

    sl = slice(i0, i1 + 1)
    duration = (i1 - i0 + 1) * step
    exempt_mask = _in_sections(np.nan_to_num(A.s[sl], nan=-1e12), st.exempt_sections)
    exempt = float(exempt_mask.sum()) * step
    counted = duration - exempt
    v = valid[sl]
    missing = float((~v).sum()) * step
    g = gap[sl][v]
    sg = sigma[sl][v]
    sigma_med = float(np.median(sg)) if sg.size else math.inf
    # Mean estimated probability that the true gap is inside (0, L], treating the gap error as Gaussian.
    conf = float(np.mean(_phi((L - g) / sg) - _phi(-g / sg))) if g.size else 0.0
    missing_frac = missing / duration
    if sigma_med <= L / 3 and missing_frac <= 0.1:
        quality: Literal["good", "limited", "insufficient"] = "good"
    elif sigma_med <= L / 2 and missing_frac <= 0.3:
        quality = "limited"
    else:
        quality = "insufficient"
    lat_used = bool(np.all(pc["lat_reliable"][sl][v])) if v.any() else False

    allowance = st.drop_back_s if started_by == "overtaken" else st.allowed_pass_s
    cls: Classification
    if counted <= 0:
        cls = "exempt"  # entirely inside exempt course sections
    elif quality == "insufficient":
        cls = "inconclusive"  # cannot tell following from passing or riding apart
    elif counted <= allowance:
        if ended_by == "follower_passed":
            cls = "overtake"
        elif started_by == "overtaken":
            cls = "drop_back"
        else:
            cls = "brief_encounter"
    elif conf < MIN_CONFIDENCE:
        cls = "inconclusive"
    else:
        cls = "potential_drafting"

    lim: list[str] = []
    lim.append(f"Combined gap uncertainty ≈ ±{sigma_med:.1f} m (1σ) against a {L:g} m zone.")
    if conf < 1.0:
        lim.append(f"Estimated probability that the gap was inside the zone: {conf:.0%} (averaged over the interval).")
    if missing > 0:
        lim.append(f"{missing:.0f} s without usable positions were bridged inside this interval.")
    if exempt > 0:
        lim.append(f"{exempt:.0f} s fell inside exempt course sections and were not counted.")
    if not lat_used:
        lim.append("Lateral separation could not be used (position accuracy too low); riders side by side "
                   "cannot be distinguished from riders in line.")  # fmt: skip
    if ended_by == "data_unavailable":
        lim.append("The interval ends where usable data stops (dropout or end of track), so its end is uncertain.")
    if cls == "potential_drafting" and ended_by == "follower_passed":
        lim.append(f"The follower eventually passed, but after more than the allowed {st.allowed_pass_s:g} s.")
    if cls == "inconclusive":
        lim.append(
            "Data quality is too low to tell following from passing or riding apart; this is not a "
            "potential-drafting flag. Review manually if needed."
        )

    # Supporting series (inspection), including a little context either side.
    ctx = max(1, round(15.0 / step))
    c0, c1 = max(0, i0 - ctx), min(grid.size - 1, i1 + ctx)
    cs = slice(c0, c1 + 1)

    def clean(a: np.ndarray, nd: int = 2) -> list[float | None]:
        return [_r(x, nd) for x in a.tolist()]

    lat_series = np.where(pc["lat_reliable"][cs], pc["lat_sep"][cs], np.nan)
    s_vals = A.s[sl][np.isfinite(A.s[sl])]
    return Interval(
        follower=f,
        leader=ld,
        classification=cls,
        i0=i0,
        i1=i1,
        started_by=started_by,
        ended_by=ended_by,
        duration_s=duration,
        counted_s=counted,
        exempt_s=exempt,
        missing_s=missing,
        gap_median_m=float(np.median(g)) if g.size else math.nan,
        gap_min_m=float(g.min()) if g.size else math.nan,
        gap_max_m=float(g.max()) if g.size else math.nan,
        sigma_median_m=sigma_med,
        confident_inside_fraction=conf,
        quality=quality,
        lateral_used=lat_used,
        limitations=lim,
        series={
            "t_s": clean(grid[cs], 1),
            "gap_m": clean(gap[cs]),
            "sigma_m": clean(sigma[cs]),
            "distance_m": clean(pc["dist"][cs]),
            "lateral_sep_m": clean(lat_series),
            "follower_x": clean(A.x[cs], 1),
            "follower_y": clean(A.y[cs], 1),
        },
        course_m=(float(s_vals.min()) if s_vals.size else math.nan, float(s_vals.max()) if s_vals.size else math.nan),
    )


def _merge_inconclusive(
    intervals: list[Interval],
    grid: np.ndarray,
    bridge: int,
    tracks: dict[str, GridTrack],
    pair_cache: dict[tuple[str, str], dict[str, np.ndarray]],
    st: DraftingSettings,
) -> list[Interval]:
    """Merge nearby inconclusive intervals of the same two riders (in either order) into one."""
    out = [iv for iv in intervals if iv.classification != "inconclusive"]
    groups: dict[frozenset[str], list[Interval]] = {}
    for iv in intervals:
        if iv.classification == "inconclusive":
            groups.setdefault(frozenset((iv.follower, iv.leader)), []).append(iv)

    def flush(cur: list[Interval]) -> None:
        if len(cur) == 1:
            out.append(cur[0])
            return
        totals: dict[tuple[str, str], int] = {}
        for c in cur:
            totals[(c.follower, c.leader)] = totals.get((c.follower, c.leader), 0) + (c.i1 - c.i0 + 1)
        f, ld = max(totals, key=lambda k: totals[k])
        m = _classify(f, ld, min(c.i0 for c in cur), max(c.i1 for c in cur), grid, tracks[f], pair_cache[(f, ld)], st)
        m.classification = "inconclusive"
        m.order_uncertain = len(totals) > 1
        if m.order_uncertain:
            m.limitations.append(
                f"{len(cur)} short intervals in both orders were merged; which rider was ahead could not be "
                "determined reliably."
            )
        out.append(m)

    for ivs in groups.values():
        ivs.sort(key=lambda iv: iv.i0)
        cur = [ivs[0]]
        for iv in ivs[1:]:
            if iv.i0 <= max(c.i1 for c in cur) + bridge:
                cur.append(iv)
            else:
                flush(cur)
                cur = [iv]
        flush(cur)
    return out


def _encounters(
    ids: list[str],
    tracks: dict[str, GridTrack],
    grid: np.ndarray,
    in_zone_by_pair: dict[tuple[str, str], np.ndarray],
    st: DraftingSettings,
    bridge: int,
) -> list[Encounter]:
    L = st.zone_length_m
    out: list[Encounter] = []
    for a_i, a in enumerate(ids):
        for b in ids[a_i + 1 :]:
            A, B = tracks[a], tracks[b]
            valid = A.valid & B.valid
            if not valid.any():
                continue
            with np.errstate(invalid="ignore"):
                dist = np.hypot(B.x - A.x, B.y - A.y)
                gap = B.s - A.s
                speed_a, speed_b = np.hypot(A.vx, A.vy), np.hypot(B.vx, B.vy)
                sigma = np.sqrt(A.acc**2 + B.acc**2)
                near = valid & (dist <= L) & ~in_zone_by_pair[(a, b)] & ~in_zone_by_pair[(b, a)]
                moving = (speed_a > MOVING_SPEED_MS) & (speed_b > MOVING_SPEED_MS)
                same_dir = (A.vx * B.vx + A.vy * B.vy) > 0
                lat_reliable = (A.acc <= st.lateral_accuracy_max_m) & (B.acc <= st.lateral_accuracy_max_m)
                lat_clear = lat_reliable & (np.abs(B.offset - A.offset) > st.lateral_separation_m)
                types: dict[EncounterType, np.ndarray] = {
                    "separate_course_section": near & (np.abs(gap) > L + 3 * sigma),
                }
                rest = near & ~types["separate_course_section"]
                types["stationary"] = rest & ~moving
                rest = rest & moving
                types["opposite_direction"] = rest & ~same_dir
                types["lateral_separation"] = rest & same_dir & lat_clear
            for typ, mask in types.items():
                for i0, i1 in _runs(mask, bridge):
                    sl = slice(i0, i1 + 1)
                    v = valid[sl]
                    out.append(
                        Encounter(
                            rider_a=a,
                            rider_b=b,
                            type=typ,
                            i0=i0,
                            i1=i1,
                            duration_s=(i1 - i0 + 1) * st.analysis_step_s,
                            min_distance_m=float(np.nanmin(dist[sl][v])),
                            median_course_gap_m=float(np.nanmedian(np.abs(gap[sl][v]))),
                        )
                    )
    out.sort(key=lambda e: (grid[e.i0], e.rider_a))
    return out
