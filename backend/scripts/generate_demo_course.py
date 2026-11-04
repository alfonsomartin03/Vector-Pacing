"""Regenerate the bundled demo course GPX deterministically (no randomness, no network).

Usage: python scripts/generate_demo_course.py
"""

from __future__ import annotations

import math
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "vector_pacing" / "data" / "demo_course.gpx"
LENGTH_M = 32_000.0
SPACING_M = 20.0
START_LAT, START_LON = 46.2000, 7.3000
M_PER_DEG_LAT = 111_195.0


def elevation(d: float) -> float:
    """Rolling start, a 2.4 km climb (~6 %), a fast descent, rollers, flat finish."""
    km = d / 1000.0
    e = 420.0 + 6.0 * math.sin(km * 1.3)
    # Main climb: km 9.0-11.4 rising ~145 m, then descent km 11.4-14.4.
    if km < 9.0:
        e += 0.0
    elif km < 11.4:
        e += 145.0 * (km - 9.0) / 2.4
    elif km < 14.4:
        e += 145.0 * (1 - (km - 11.4) / 3.0)
    # Rollers km 18-26.
    if 18.0 <= km <= 26.0:
        e += 22.0 * math.sin((km - 18.0) / 8.0 * 4 * math.pi) ** 2
    # Short punch near the end: km 28.0-28.8, ~40 m.
    if 28.0 <= km < 28.8:
        e += 40.0 * (km - 28.0) / 0.8
    elif 28.8 <= km < 30.0:
        e += 40.0 * (1 - (km - 28.8) / 1.2)
    return round(e, 1)


def main() -> None:
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="Vector Pacing demo generator" xmlns="http://www.topografix.com/GPX/1/1">',
        "  <metadata><name>Vector Valley TT (demo)</name></metadata>",
        "  <trk>",
        "    <name>Vector Valley TT (demo)</name>",
        "    <trkseg>",
    ]
    lat, lon = START_LAT, START_LON
    n = int(LENGTH_M / SPACING_M)
    for i in range(n + 1):
        d = i * SPACING_M
        lines.append(f'      <trkpt lat="{lat:.6f}" lon="{lon:.6f}"><ele>{elevation(d)}</ele></trkpt>')
        heading = math.radians(60.0 + 50.0 * math.sin(d / 4000.0))  # gently winding road
        lat += SPACING_M * math.cos(heading) / M_PER_DEG_LAT
        lon += SPACING_M * math.sin(heading) / (M_PER_DEG_LAT * math.cos(math.radians(lat)))
    lines += ["    </trkseg>", "  </trk>", "</gpx>", ""]
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {OUT} ({n + 1} points)")


if __name__ == "__main__":
    main()
