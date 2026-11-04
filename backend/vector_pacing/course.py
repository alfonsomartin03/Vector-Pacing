"""Course loading: safe GPX parsing, distance, smoothing and gradient.

Processing pipeline (all distances in metres, elevations in metres):

1. Parse GPX with ``defusedxml`` (blocks entity-expansion / external-entity
   attacks). Namespaces are ignored by matching on local tag names, so GPX 1.0,
   GPX 1.1 and vendor-extended files all work.
2. Collect points from every ``<trk>/<trkseg>`` in document order. Segments are
   concatenated; the straight-line gap between the end of one segment and the
   start of the next is counted as distance. If no track points exist,
   ``<rte>/<rtept>`` points are used instead.
3. Points with invalid coordinates are rejected (the whole file fails, because
   silently dropping them would change the course). Consecutive points closer
   than ``DUPLICATE_TOLERANCE_M`` are treated as duplicates and dropped.
4. Missing elevation: values are linearly interpolated by distance from the
   nearest points that have elevation (edges are held constant). If fewer than
   two points carry elevation, the course is treated as flat and a warning is
   returned.
5. The course is resampled onto a uniform distance grid with step
   ``ds = max(MIN_STEP_M, length / TARGET_STEPS)``.
6. Elevation is smoothed with a centred moving average over
   ``SMOOTHING_WINDOW_M`` (edge values repeated), which suppresses GPS/barometer
   noise that would otherwise produce unrealistic gradient spikes.
7. Gradient per step is ``Δelevation / ds`` and is clipped to
   ``±MAX_ABS_GRADE``; any clipping is reported as a warning.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from importlib import resources
from xml.etree.ElementTree import Element, ParseError

import numpy as np
from defusedxml import DefusedXmlException
from defusedxml.ElementTree import fromstring
from scipy.ndimage import uniform_filter1d

EARTH_RADIUS_M = 6_371_008.8
DUPLICATE_TOLERANCE_M = 0.05
MIN_COURSE_LENGTH_M = 200.0
MAX_COURSE_LENGTH_M = 300_000.0
MAX_POINTS = 200_000
MAX_GPX_BYTES = 10 * 1024 * 1024
MIN_STEP_M = 5.0
TARGET_STEPS = 2000
SMOOTHING_WINDOW_M = 100.0
MAX_ABS_GRADE = 0.25

DEMO_COURSE_ID = "demo"


class CourseError(ValueError):
    """Raised when a course cannot be loaded. The message is user-facing."""


@dataclass(frozen=True)
class Course:
    """A course resampled to a uniform distance grid.

    ``distance_m`` and ``elevation_m`` have ``n + 1`` entries (step edges);
    ``grade`` has ``n`` entries (one per step, rise over run).
    """

    name: str
    source: str
    distance_m: np.ndarray
    elevation_m: np.ndarray
    grade: np.ndarray
    step_m: float
    raw_point_count: int
    content_hash: str
    warnings: list[str] = field(default_factory=list)

    @property
    def length_m(self) -> float:
        return float(self.distance_m[-1])

    @property
    def n_steps(self) -> int:
        return int(self.grade.size)

    @property
    def elevation_gain_m(self) -> float:
        d = np.diff(self.elevation_m)
        return float(d[d > 0].sum())

    @property
    def elevation_loss_m(self) -> float:
        d = np.diff(self.elevation_m)
        return float(-d[d < 0].sum())


def _local(tag: str) -> str:
    """Strip an XML namespace: '{http://...}trkpt' -> 'trkpt'."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _children(el: Element, name: str) -> list[Element]:
    return [c for c in el if _local(c.tag) == name]


def _text_of_child(el: Element, name: str) -> str | None:
    for c in el:
        if _local(c.tag) == name:
            return (c.text or "").strip()
    return None


def _parse_point(el: Element, index: int) -> tuple[float, float, float | None]:
    try:
        lat = float(el.attrib["lat"])
        lon = float(el.attrib["lon"])
    except KeyError as exc:
        raise CourseError(f"Point {index + 1} is missing its {exc.args[0]} attribute.") from None
    except ValueError:
        raise CourseError(f"Point {index + 1} has a non-numeric latitude/longitude.") from None
    if not (math.isfinite(lat) and math.isfinite(lon)) or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise CourseError(f"Point {index + 1} has out-of-range coordinates ({lat}, {lon}).")
    ele: float | None = None
    ele_text = _text_of_child(el, "ele")
    if ele_text:
        try:
            ele = float(ele_text)
        except ValueError:
            ele = None
        if ele is not None and (not math.isfinite(ele) or not (-500 <= ele <= 9000)):
            ele = None
    return lat, lon, ele


def haversine_m(lat1: np.ndarray, lon1: np.ndarray, lat2: np.ndarray, lon2: np.ndarray) -> np.ndarray:
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi = p2 - p1
    dlmb = np.radians(lon2) - np.radians(lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlmb / 2) ** 2
    return np.asarray(2 * EARTH_RADIUS_M * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0))))


def parse_gpx_points(
    data: bytes | str,
) -> tuple[str | None, list[tuple[float, float, float | None]], list[str], str]:
    """Read GPX track (or route) points: ``(name, [(lat, lon, ele)], warnings, content_hash)``."""
    raw = data.encode("utf-8") if isinstance(data, str) else data
    if len(raw) == 0:
        raise CourseError("The file is empty.")
    if len(raw) > MAX_GPX_BYTES:
        raise CourseError(f"The file is larger than {MAX_GPX_BYTES // (1024 * 1024)} MB.")
    try:
        root = fromstring(raw)
    except DefusedXmlException:
        raise CourseError("The file uses forbidden XML features (entities/DTDs) and was rejected.") from None
    except (ParseError, ValueError):
        raise CourseError("The file is not well-formed XML, so it cannot be read as GPX.") from None
    if _local(root.tag) != "gpx":
        raise CourseError("The file is XML but not GPX (root element is not <gpx>).")

    name = None
    segments: list[list[Element]] = []
    for trk in _children(root, "trk"):
        name = name or _text_of_child(trk, "name")
        for seg in _children(trk, "trkseg"):
            pts = _children(seg, "trkpt")
            if pts:
                segments.append(pts)
    warnings: list[str] = []
    if not segments:
        for rte in _children(root, "rte"):
            name = name or _text_of_child(rte, "name")
            pts = _children(rte, "rtept")
            if pts:
                segments.append(pts)
        if segments:
            warnings.append("No track found; the route (<rte>) points were used instead.")
    if not segments:
        raise CourseError("No track or route points were found in the GPX file.")
    if len(segments) > 1:
        warnings.append(
            f"{len(segments)} track segments were joined in file order; gaps between them count as distance."
        )
    if not name:
        meta = _children(root, "metadata")
        name = _text_of_child(meta[0], "name") if meta else None

    elements = [p for seg in segments for p in seg]
    if len(elements) > MAX_POINTS:
        raise CourseError(f"The course has more than {MAX_POINTS:,} points.")
    parsed = [_parse_point(p, i) for i, p in enumerate(elements)]
    return name, parsed, warnings, hashlib.sha256(raw).hexdigest()[:16]


def parse_gpx(data: bytes | str, source: str = "upload") -> Course:
    """Parse GPX bytes/text into a :class:`Course`. Raises :class:`CourseError`."""
    name, parsed, warnings, content_hash = parse_gpx_points(data)
    return build_course(
        parsed,
        name=(name or "Uploaded course")[:120],
        source=source,
        content_hash=content_hash,
        warnings=warnings,
    )


def build_course(
    points: list[tuple[float, float, float | None]],
    *,
    name: str,
    source: str,
    content_hash: str,
    warnings: list[str] | None = None,
) -> Course:
    """Turn raw (lat, lon, elevation-or-None) points into a resampled course."""
    warnings = list(warnings or [])
    if len(points) < 2:
        raise CourseError("A course needs at least two points.")
    lat = np.array([p[0] for p in points], dtype=float)
    lon = np.array([p[1] for p in points], dtype=float)
    ele = np.array([np.nan if p[2] is None else p[2] for p in points], dtype=float)

    seg = haversine_m(lat[:-1], lon[:-1], lat[1:], lon[1:])
    keep = np.concatenate([[True], seg > DUPLICATE_TOLERANCE_M])
    dropped = int((~keep).sum())
    if dropped:
        warnings.append(f"{dropped} duplicate point(s) were removed.")
    lat, lon, ele = lat[keep], lon[keep], ele[keep]
    if lat.size < 2:
        raise CourseError("All points are at the same location; the course has zero length.")
    cum = np.concatenate([[0.0], np.cumsum(haversine_m(lat[:-1], lon[:-1], lat[1:], lon[1:]))])
    length = float(cum[-1])
    if length < MIN_COURSE_LENGTH_M:
        raise CourseError(f"The course is only {length:.0f} m long; at least {MIN_COURSE_LENGTH_M:.0f} m is required.")
    if length > MAX_COURSE_LENGTH_M:
        raise CourseError(
            f"The course is {length / 1000:.0f} km long; the limit is {MAX_COURSE_LENGTH_M / 1000:.0f} km."
        )

    has_ele = np.isfinite(ele)
    if has_ele.sum() < 2:
        warnings.append("The file has no usable elevation data; the course is treated as flat.")
        ele = np.zeros_like(cum)
    elif not has_ele.all():
        missing = int((~has_ele).sum())
        warnings.append(f"{missing} point(s) had no elevation; values were interpolated by distance.")
        ele = np.interp(cum, cum[has_ele], ele[has_ele])

    step = max(MIN_STEP_M, length / TARGET_STEPS)
    n = max(1, round(length / step))
    step = length / n
    grid = np.linspace(0.0, length, n + 1)
    ele_grid = np.interp(grid, cum, ele)
    window = max(1, round(SMOOTHING_WINDOW_M / step))
    if window % 2 == 0:
        window += 1
    if window > 1 and n + 1 > window:
        ele_grid = uniform_filter1d(ele_grid, size=window, mode="nearest")
    grade = np.diff(ele_grid) / step
    clipped = int((np.abs(grade) > MAX_ABS_GRADE).sum())
    if clipped:
        warnings.append(f"{clipped} step(s) exceeded ±{MAX_ABS_GRADE:.0%} gradient after smoothing and were clipped.")
        grade = np.clip(grade, -MAX_ABS_GRADE, MAX_ABS_GRADE)
        ele_grid = np.concatenate([[ele_grid[0]], ele_grid[0] + np.cumsum(grade * step)])

    return Course(
        name=name,
        source=source,
        distance_m=grid,
        elevation_m=ele_grid,
        grade=grade,
        step_m=step,
        raw_point_count=len(points),
        content_hash=content_hash,
        warnings=warnings,
    )


_demo_cache: Course | None = None


def load_demo_course() -> Course:
    """Load the bundled demo course (no network access)."""
    global _demo_cache
    if _demo_cache is None:
        raw = resources.files("vector_pacing").joinpath("data/demo_course.gpx").read_bytes()
        _demo_cache = parse_gpx(raw, source=DEMO_COURSE_ID)
    return _demo_cache
