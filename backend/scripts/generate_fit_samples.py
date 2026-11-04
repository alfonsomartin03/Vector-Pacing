"""Write the drafting upload samples in samples/ (deterministic).

* ``samples/drafting_two_riders/A.fit`` and ``B.fit``: two riders on a straight 3 km course, B riding
  5 m behind A at ~40 km/h for 4 minutes. No GPS accuracy is recorded (the assumed accuracy applies).
* ``samples/drafting_two_riders/course.gpx``: the course line.
* ``samples/drafting_invalid.fit``: a truncated FIT file, to show the validation error.

Run from the repository root:  backend/.venv/bin/python backend/scripts/generate_fit_samples.py
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from pathlib import Path

from vector_pacing.fit import write_fit

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
    print(f"Wrote samples to {out} and {ROOT / 'drafting_invalid.fit'}")


if __name__ == "__main__":
    main()
