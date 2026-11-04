from pathlib import Path

import numpy as np
import pytest

from vector_pacing.course import (
    MAX_ABS_GRADE,
    CourseError,
    build_course,
    haversine_m,
    load_demo_course,
    parse_gpx,
)

SAMPLES = Path(__file__).resolve().parents[2] / "samples"
M_PER_DEG = 111_195.0


def gpx(body: str, ns: str = 'xmlns="http://www.topografix.com/GPX/1/1"') -> str:
    return f'<?xml version="1.0"?><gpx version="1.1" {ns}>{body}</gpx>'


def straight_track(n: int, spacing_m: float, ele=lambda i: 100.0, seg_split: int | None = None) -> str:
    pts = []
    for i in range(n):
        e = ele(i)
        ele_xml = "" if e is None else f"<ele>{e}</ele>"
        pts.append(f'<trkpt lat="{45 + i * spacing_m / M_PER_DEG:.7f}" lon="7.0">{ele_xml}</trkpt>')
    if seg_split:
        return gpx(f"<trk><trkseg>{''.join(pts[:seg_split])}</trkseg><trkseg>{''.join(pts[seg_split:])}</trkseg></trk>")
    return gpx(f"<trk><name>T</name><trkseg>{''.join(pts)}</trkseg></trk>")


def test_haversine_one_degree_latitude():
    d = haversine_m(np.array([0.0]), np.array([0.0]), np.array([1.0]), np.array([0.0]))
    assert d[0] == pytest.approx(111_195, rel=1e-3)


def test_demo_course_is_deterministic_and_reasonable():
    c = load_demo_course()
    again = parse_gpx((SAMPLES.parent / "backend/vector_pacing/data/demo_course.gpx").read_bytes(), source="demo")
    assert c.length_m == pytest.approx(32_000, rel=0.01)
    assert np.allclose(c.grade, again.grade)
    assert c.content_hash == again.content_hash
    assert c.warnings == []
    assert 200 < c.elevation_gain_m < 400
    assert c.distance_m.size == c.grade.size + 1


@pytest.mark.parametrize(
    "ns",
    [
        'xmlns="http://www.topografix.com/GPX/1/1"',
        'xmlns="http://www.topografix.com/GPX/1/0"',
        'xmlns:g="http://www.topografix.com/GPX/1/1"',  # unprefixed elements but namespace declared
        "",
    ],
)
def test_namespaces_are_supported(ns):
    pts = "".join(f'<trkpt lat="{45 + i * 100 / M_PER_DEG}" lon="7"><ele>10</ele></trkpt>' for i in range(20))
    c = parse_gpx(gpx(f"<trk><trkseg>{pts}</trkseg></trk>", ns))
    assert c.length_m == pytest.approx(1900, rel=1e-3)


def test_prefixed_namespace_elements():
    pts = "".join(f'<g:trkpt lat="{45 + i * 100 / M_PER_DEG}" lon="7"><g:ele>5</g:ele></g:trkpt>' for i in range(5))
    text = f'<g:gpx xmlns:g="http://www.topografix.com/GPX/1/1"><g:trk><g:trkseg>{pts}</g:trkseg></g:trk></g:gpx>'
    assert parse_gpx(text).length_m == pytest.approx(400, rel=1e-3)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "empty"),
        ("<gpx><trk>", "well-formed"),
        ("not xml at all", "well-formed"),
        ("<kml></kml>", "not GPX"),
        (gpx(""), "No track or route"),
        (gpx('<trk><trkseg><trkpt lat="1" lon="2"/></trkseg></trk>'), "at least two"),
        (gpx('<trk><trkseg><trkpt lat="91" lon="2"/><trkpt lat="1" lon="2"/></trkseg></trk>'), "out-of-range"),
        (gpx('<trk><trkseg><trkpt lat="x" lon="2"/><trkpt lat="1" lon="2"/></trkseg></trk>'), "non-numeric"),
        (gpx('<trk><trkseg><trkpt lon="2"/><trkpt lat="1" lon="2"/></trkseg></trk>'), "missing"),
    ],
)
def test_malformed_inputs_raise_course_error(text, message):
    with pytest.raises(CourseError, match=message):
        parse_gpx(text)


def test_entity_expansion_is_rejected():
    bomb = '<?xml version="1.0"?><!DOCTYPE gpx [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;">]><gpx>&b;</gpx>'
    with pytest.raises(CourseError, match="forbidden"):
        parse_gpx(bomb)


def test_zero_length_course_rejected():
    same = "".join('<trkpt lat="45" lon="7"><ele>1</ele></trkpt>' for _ in range(10))
    with pytest.raises(CourseError, match="zero length"):
        parse_gpx(gpx(f"<trk><trkseg>{same}</trkseg></trk>"))


def test_too_short_course_rejected():
    with pytest.raises(CourseError, match="at least"):
        parse_gpx(straight_track(5, 10))


def test_duplicates_removed_with_warning():
    pts = []
    for i in range(30):
        p = f'<trkpt lat="{45 + i * 50 / M_PER_DEG}" lon="7"><ele>10</ele></trkpt>'
        pts += [p, p] if i % 3 == 0 else [p]
    c = parse_gpx(gpx(f"<trk><trkseg>{''.join(pts)}</trkseg></trk>"))
    assert any("duplicate" in w for w in c.warnings)
    assert c.length_m == pytest.approx(29 * 50, rel=1e-3)


def test_missing_elevation_interpolated():
    c = parse_gpx(straight_track(101, 20, ele=lambda i: None if i % 2 else float(i)))
    assert any("interpolated" in w for w in c.warnings)
    # Linear profile 1 m per 20 m step -> 5 % grade everywhere (interior, after smoothing)
    assert np.median(c.grade) == pytest.approx(0.05, abs=1e-3)


def test_no_elevation_treated_as_flat():
    c = parse_gpx(straight_track(50, 20, ele=lambda i: None))
    assert any("flat" in w for w in c.warnings)
    assert np.all(c.grade == 0)


def test_multiple_segments_joined_including_gap():
    c = parse_gpx(straight_track(60, 20, seg_split=30))
    assert c.length_m == pytest.approx(59 * 20, rel=1e-3)
    assert any("segments were joined" in w for w in c.warnings)


def test_route_fallback():
    pts = "".join(f'<rtept lat="{45 + i * 100 / M_PER_DEG}" lon="7"/>' for i in range(10))
    c = parse_gpx(gpx(f"<rte><name>R</name>{pts}</rte>"))
    assert c.name == "R"
    assert any("route" in w for w in c.warnings)


def test_smoothing_reduces_noise_and_grade_is_clipped():
    rng = np.random.default_rng(0)
    noise = rng.normal(0, 3, 400)
    c = parse_gpx(straight_track(400, 10, ele=lambda i: round(100 + noise[i], 2)))
    raw_grade_std = np.std(np.diff(100 + noise) / 10)
    assert np.std(c.grade) < raw_grade_std / 3
    cliff = parse_gpx(straight_track(100, 10, ele=lambda i: 0.0 if i < 50 else 300.0))
    assert np.max(np.abs(cliff.grade)) <= MAX_ABS_GRADE + 1e-12
    assert any("clipped" in w for w in cliff.warnings)


def test_bundled_samples():
    hill = parse_gpx((SAMPLES / "hill_climb_two_segments.gpx").read_bytes())
    assert hill.length_m == pytest.approx(8000, rel=0.01)
    assert len(hill.warnings) == 3  # segments joined, duplicate removed, elevation interpolated
    flat = parse_gpx((SAMPLES / "flat_route_no_elevation.gpx").read_bytes())
    assert flat.length_m == pytest.approx(10_000, rel=0.01)
    with pytest.raises(CourseError):
        parse_gpx((SAMPLES / "invalid_malformed.gpx").read_bytes())


def test_build_course_requires_two_points():
    with pytest.raises(CourseError):
        build_course([(1.0, 2.0, None)], name="x", source="x", content_hash="x")
