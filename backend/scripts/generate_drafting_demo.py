"""Regenerate the bundled multi-rider drafting scenario deterministically.

Usage: python scripts/generate_drafting_demo.py

The scenario is synthetic and illustrative. It is an out-and-back time trial
(riders mostly start 30 s apart) on a gently curving road with the outbound and return
lanes 8 m apart, built so each detection case appears once:

* R02 → R01  sustained following (~150 s at ~6 m)                   → potential drafting
* R04 → R03  legal pass under illustrative settings (~17 s in zone) → overtake + drop-back
* R06 → R05  brief encounter (~15 s at 8–10 m, no pass)             → brief encounter
* R08 → R07  following near the zone edge with poor GNSS (9 m),
             irregular timestamps and dropouts                       → inconclusive / not flagged
* R09        abandons and rides back against traffic                 → opposite-direction encounters
* R12 / R11  side by side 3 m apart with precise GNSS (0.3 m)        → lateral separation, not following
* outbound vs. return riders pass 8 m apart                          → separate course sections
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

OUT = Path(__file__).resolve().parents[1] / "vector_pacing" / "data" / "drafting_demo.json"
LAT0, LON0 = 44.10, 5.20
R = 6_371_008.8
STEM = 4000.0
LANE = 4.0  # lane centre offset from road centreline (lanes 8 m apart)
DT_FINE = 0.1


def centreline(u: np.ndarray) -> np.ndarray:
    return np.column_stack([u, 60.0 * np.sin(u / 700.0)])


def build_course(spacing: float = 5.0) -> tuple[np.ndarray, np.ndarray]:
    """Return (xy course polyline, cumulative distance)."""
    u = np.arange(0.0, STEM + 1e-9, spacing)
    c = centreline(u)
    du = np.gradient(c, axis=0)
    n = np.column_stack([-du[:, 1], du[:, 0]])
    n /= np.hypot(n[:, 0], n[:, 1])[:, None]
    out = c - LANE * n  # right-hand lane, heading +x
    back = (c + LANE * n)[::-1]
    # Turnaround: semicircle around the end of the centreline.
    end, nn = c[-1], n[-1]
    tang = np.array([nn[1], -nn[0]])  # forward direction at the end
    ang = np.linspace(-math.pi / 2, math.pi / 2, 13)[1:-1]
    turn = np.array([end + LANE * (math.sin(a) * tang - math.cos(a) * nn) for a in ang])
    xy = np.vstack([out, turn, back])
    cum = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(xy, axis=0).T))])
    return xy, cum


XY, CUM = build_course()
LENGTH = float(CUM[-1])


def position(s: np.ndarray, lateral: np.ndarray) -> np.ndarray:
    """Point at course distance s, shifted `lateral` metres to the left of travel direction."""
    s = np.clip(s, 0.0, LENGTH)
    x = np.interp(s, CUM, XY[:, 0])
    y = np.interp(s, CUM, XY[:, 1])
    k = np.clip(np.searchsorted(CUM, s, side="right") - 1, 0, len(XY) - 2)
    d = XY[k + 1] - XY[k]
    d /= np.hypot(d[:, 0], d[:, 1])[:, None]
    return np.column_stack([x - d[:, 1] * lateral, y + d[:, 0] * lateral])


def to_latlon(xy: np.ndarray) -> np.ndarray:
    lat = LAT0 + np.degrees(xy[:, 1] / R)
    lon = LON0 + np.degrees(xy[:, 0] / (R * math.cos(math.radians(LAT0))))
    return np.column_stack([lat, lon])


def integrate(t_start: float, speed_fn) -> tuple[np.ndarray, np.ndarray]:
    """Fine-time trajectory s(t) from t_start until the finish (or speed_fn returns None)."""
    ts, ss = [t_start], [0.0]
    t, s = t_start, 0.0
    while s < LENGTH and t < t_start + 3000:
        v = speed_fn(t, s)
        if v is None:
            break
        t += DT_FINE
        s = max(0.0, s + v * DT_FINE)
        ts.append(t)
        ss.append(s)
        if s <= 0.0 and v < 0:
            break
    return np.array(ts), np.array(ss)


def s_at(traj: tuple[np.ndarray, np.ndarray], t: float) -> float:
    return float(np.interp(t, traj[0], traj[1], left=np.nan, right=np.nan))


def follow(target, gap: float, t0: float, t1: float, v_before: float, v_after: float):
    """Speed law: ride at v_before until `gap` behind target, hold it until t1, then v_after."""
    state = {"locked": False}

    def fn(t: float, s: float):
        tgt = s_at(target, t)
        if t >= t1:
            return v_after
        if not state["locked"] and math.isfinite(tgt) and tgt - s <= gap and t >= t0:
            state["locked"] = True
        if state["locked"]:
            nxt = s_at(target, t + DT_FINE)
            return (nxt - gap - s) / DT_FINE
        return v_before

    return fn


def ar1_noise(rng: np.random.Generator, n: int, sd: float, rho: float = 0.9) -> np.ndarray:
    e = rng.normal(0.0, sd * math.sqrt(1 - rho**2), size=(n, 2))
    out = np.zeros((n, 2))
    out[0] = rng.normal(0.0, sd, size=2)
    for i in range(1, n):
        out[i] = rho * out[i - 1] + e[i]
    return out


def sample(rid, label, traj, rng, accuracy, lateral=0.0, times=None, lateral_fn=None):
    t_fine, s_fine = traj
    if times is None:
        times = np.arange(math.ceil(t_fine[0]), math.floor(t_fine[-1]) + 1, 1.0)
    s = np.interp(times, t_fine, s_fine)
    lat_off = np.full_like(s, lateral) if lateral_fn is None else np.array([lateral_fn(t) for t in times])
    xy = position(s, lat_off) + ar1_noise(rng, len(times), accuracy / math.sqrt(2))
    ll = to_latlon(xy)
    return {
        "id": rid,
        "label": label,
        "samples": {
            "t": [round(float(t), 2) for t in times],
            "lat": [round(float(v), 7) for v in ll[:, 0]],
            "lon": [round(float(v), 7) for v in ll[:, 1]],
            "accuracy_m": [accuracy] * len(times),
        },
    }


def main() -> None:
    rng = np.random.default_rng(20261103)
    riders = []
    const = lambda v: lambda t, s: v  # noqa: E731

    r01 = integrate(0, const(10.6))
    r02 = integrate(30, follow(r01, 6.0, 0, 491, 11.6, 10.0))
    r03 = integrate(60, const(10.4))
    r04 = integrate(90, const(11.0))
    r05 = integrate(120, const(10.5))
    hold = {"t": None}

    def r06_speed(t, s):
        gap = s_at(r05, t) - s
        if hold["t"] is None and math.isfinite(gap) and gap <= 8.0:
            hold["t"] = t
        if hold["t"] is None:
            return 11.3
        return 10.5 if t < hold["t"] + 10 else 9.8

    r06 = integrate(150, r06_speed)
    r07 = integrate(180, const(10.3))
    r08 = integrate(210, follow(r07, 11.0, 0, 661, 11.2, 9.9))

    def r09_speed(t, s):
        if t < 440 and s < 2000:
            return 10.0
        if t < 450:
            return 0.0
        return -7.0

    r09 = integrate(240, r09_speed)
    r10 = integrate(270, const(10.8))
    r11 = integrate(300, const(10.7))
    r12 = integrate(315, follow(r11, 4.0, 0, 470, 12.5, 11.7))

    riders.append(sample("R01", "Rider 01", r01, rng, 1.5))
    riders.append(sample("R02", "Rider 02 (sustained following)", r02, rng, 1.5))
    riders.append(sample("R03", "Rider 03", r03, rng, 1.5))
    riders.append(sample("R04", "Rider 04 (legal pass)", r04, rng, 1.5))
    riders.append(sample("R05", "Rider 05", r05, rng, 1.5))
    riders.append(sample("R06", "Rider 06 (brief encounter)", r06, rng, 1.5))

    # Ambiguous GNSS: poor accuracy, irregular & jittered timestamps, two dropouts.
    def irregular_times(traj, seed_offset):
        local = np.random.default_rng(20261103 + seed_offset)
        t, out = math.ceil(traj[0][0]), []
        while t <= traj[0][-1]:
            out.append(t + local.uniform(-0.3, 0.3))
            t += local.choice([1.0, 1.0, 1.0, 2.0])
        out = np.array(sorted(out))
        out = out[(out >= traj[0][0]) & (out <= traj[0][-1])]
        drop = ((out > 560) & (out < 568)) | ((out > 610) & (out < 619))
        return out[~drop]

    riders.append(sample("R07", "Rider 07 (poor GNSS)", r07, rng, 9.0, times=irregular_times(r07, 7)))
    riders.append(sample("R08", "Rider 08 (poor GNSS, near zone edge)", r08, rng, 9.0, times=irregular_times(r08, 8)))
    riders.append(sample("R09", "Rider 09 (abandons, rides back)", r09, rng, 1.5, lateral=1.5))
    riders.append(sample("R10", "Rider 10", r10, rng, 1.5))
    riders.append(sample("R11", "Rider 11 (precise GNSS)", r11, rng, 0.3))
    riders.append(
        sample(
            "R12", "Rider 12 (precise GNSS, alongside R11)", r12, rng, 0.3, lateral_fn=lambda t: 3.0 if t < 520 else 0.0
        )
    )

    course_ll = to_latlon(XY[::2])
    doc = {
        "format": "vector-pacing-riders/1",
        "name": "Out-and-back ITT — illustrative drafting scenario",
        "description": (
            "Synthetic, deterministic scenario for demonstrating potential-drafting detection. "
            "12 riders start at 15–30 s intervals on an 8 km out-and-back course (lanes 8 m apart). "
            "Not real data."
        ),
        "illustrative": True,
        "start_time": "2026-06-14T08:00:00Z",
        "course": {
            "name": "Illustrative out-and-back (8 km)",
            "points": [[round(a, 7), round(b, 7)] for a, b in course_ll],
        },
        "riders": riders,
    }
    OUT.write_text(json.dumps(doc, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size / 1024:.0f} KiB, course {LENGTH:.0f} m)")


if __name__ == "__main__":
    main()
