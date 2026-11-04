"""Write the drafting upload samples in samples/ (deterministic).

* ``samples/drafting_two_riders/A.fit`` and ``B.fit``: two riders on a straight 3 km course, B riding
  5 m behind A at ~40 km/h for 4 minutes. No GPS accuracy is recorded (the assumed accuracy applies).
* ``samples/drafting_two_riders/course.gpx``: the course line.
* ``samples/drafting_invalid.fit``: a truncated FIT file, to show the validation error.
* ``samples/drafting_with_power/R01.fit`` and ``R02.fit`` (+ ``course.gpx``): a 15-minute flat ride
  with power, speed and cadence. R02 starts 20 s after R01, closes the gap at t = 270 s, then sits
  5 m behind R01 for 90 s at ~100 W less than its own solo power at the same speed, then drops back.
  Both the GPS following check and the within-rider power screen should flag that stretch.

Run from the repository root:  backend/.venv/bin/python backend/scripts/generate_fit_samples.py
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from vector_pacing.fit import write_fit
from vector_pacing.power_screen import RiderProfile, expected_solo_power

ROOT = Path(__file__).resolve().parents[2] / "samples"
LAT0, LON0 = 47.0, 8.0
M_PER_DEG_LON = 111_320.0 * math.cos(math.radians(LAT0))
SPEED_MS = 11.0
GAP_M = 5.0
DURATION_S = 240
START = datetime(2026, 7, 1, 9, 0, tzinfo=UTC).timestamp()


def lon_at(x_m: float) -> float:
    return round(LON0 + x_m / M_PER_DEG_LON, 7)


def main() -> None:
    out = ROOT / "drafting_two_riders"
    out.mkdir(parents=True, exist_ok=True)
    a = [(START + t, LAT0, lon_at(SPEED_MS * t), None) for t in range(DURATION_S)]
    b = [(START + t, LAT0, lon_at(max(0.0, SPEED_MS * t - GAP_M)), None) for t in range(DURATION_S)]
    (out / "A.fit").write_bytes(write_fit(a))
    (out / "B.fit").write_bytes(write_fit(b))
    pts = "\n".join(f'      <trkpt lat="{LAT0}" lon="{lon_at(50.0 * i)}"/>' for i in range(61))
    (out / "course.gpx").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<gpx version="1.1" creator="Vector Pacing sample" xmlns="http://www.topografix.com/GPX/1/1">\n'
        f"  <trk>\n    <name>Straight 3 km</name>\n    <trkseg>\n{pts}\n    </trkseg>\n  </trk>\n</gpx>\n",
        encoding="utf-8",
    )
    full = write_fit(a)
    (ROOT / "drafting_invalid.fit").write_bytes(full[: len(full) // 2])
    write_power_sample(ROOT / "drafting_with_power")
    print(f"Wrote samples to {out}, {ROOT / 'drafting_invalid.fit'} and {ROOT / 'drafting_with_power'}")


def course_gpx(name: str, length_m: float, spacing_m: float = 50.0) -> str:
    pts = "\n".join(
        f'      <trkpt lat="{LAT0}" lon="{lon_at(spacing_m * i)}"/>' for i in range(int(length_m / spacing_m) + 1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<gpx version="1.1" creator="Vector Pacing sample" xmlns="http://www.topografix.com/GPX/1/1">\n'
        f"  <trk>\n    <name>{name}</name>\n    <trkseg>\n{pts}\n    </trkseg>\n  </trk>\n</gpx>\n"
    )


def write_power_sample(out: Path) -> None:
    rng = np.random.default_rng(7)
    out.mkdir(parents=True, exist_ok=True)
    v, duration, start2 = 12.0, 900, 20
    solo = expected_solo_power(v, 0.0, RiderProfile("sample"))
    fast = expected_solo_power(15.25, 0.0, RiderProfile("sample"))

    def ride(xs: list[float], speeds: list[float], powers: list[float], t_start: int) -> bytes:
        samples = [(START + t_start + i, LAT0, lon_at(x), 1.0) for i, x in enumerate(xs)]
        tele = [
            {"speed_mps": s, "power_w": max(0.0, p + rng.normal(0, 12)), "cadence_rpm": 90.0 if p > 0 else 0.0,
             "altitude_m": 400.0, "distance_m": x}
            for x, s, p in zip(xs, speeds, powers, strict=True)
        ]  # fmt: skip
        return write_fit(samples, tele)

    x1 = [v * t for t in range(duration)]
    (out / "R01.fit").write_bytes(ride(x1, [v] * duration, [solo] * duration, 0))
    xs, speeds, powers, x = [], [], [], 0.0
    for t in range(start2, duration):
        lead = x1[t]
        if 270 <= t < 420 and lead - x > 5.0 and t < 330:
            s, p = 15.25, fast  # closing the gap
        elif 270 <= t < 420:
            s, p = v, solo - 100.0  # sitting 5 m behind R01
            x = lead - 5.0 - s
        elif 420 <= t < 450:
            s, p = 5.5, 60.0  # sitting up, dropping back
        else:
            s, p = v, solo
        x += s
        xs.append(x)
        speeds.append(s)
        powers.append(p)
    (out / "R02.fit").write_bytes(ride(xs, speeds, powers, start2))
    (out / "course.gpx").write_text(course_gpx("Flat 11 km", 11_000.0), encoding="utf-8")


if __name__ == "__main__":
    main()
