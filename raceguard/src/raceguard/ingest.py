"""Telemetry ingestion and normalization."""

from __future__ import annotations

import csv
import math
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

from .models import TelemetryPoint

EARTH_RADIUS_M = 6_371_008.8


class TelemetryError(ValueError):
    """Raised when telemetry cannot be normalized safely."""


def parse_timestamp(value: str) -> datetime:
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise TelemetryError(f"invalid timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _optional_float(row: dict[str, str], key: str) -> float | None:
    value = row.get(key, "").strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError as exc:
        raise TelemetryError(f"invalid {key}: {value!r}") from exc


def load_csv(path: str | Path) -> list[TelemetryPoint]:
    """Load the documented interchange CSV format.

    Required columns: rider_id, timestamp, latitude, longitude and either
    speed_mps or speed_kph. Optional columns mirror ``TelemetryPoint`` fields.
    """

    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fields = set(reader.fieldnames or ())
        required = {"rider_id", "timestamp", "latitude", "longitude"}
        missing = required - fields
        if missing or not ({"speed_mps", "speed_kph"} & fields):
            details = sorted(missing | ({"speed_mps"} if not ({"speed_mps", "speed_kph"} & fields) else set()))
            raise TelemetryError(f"missing required CSV columns: {', '.join(details)}")

        points: list[TelemetryPoint] = []
        for line_number, row in enumerate(reader, start=2):
            try:
                speed = _optional_float(row, "speed_mps")
                if speed is None:
                    speed_kph = _optional_float(row, "speed_kph")
                    speed = speed_kph / 3.6 if speed_kph is not None else None
                if speed is None:
                    raise TelemetryError("speed is blank")
                points.append(
                    TelemetryPoint(
                        rider_id=row["rider_id"].strip(),
                        timestamp=parse_timestamp(row["timestamp"]),
                        latitude=float(row["latitude"]),
                        longitude=float(row["longitude"]),
                        speed_mps=speed,
                        power_w=_optional_float(row, "power_w"),
                        elevation_m=_optional_float(row, "elevation_m"),
                        cadence_rpm=_optional_float(row, "cadence_rpm"),
                        heart_rate_bpm=_optional_float(row, "heart_rate_bpm"),
                        distance_m=_optional_float(row, "distance_m"),
                        gradient=_optional_float(row, "gradient") or 0.0,
                    )
                )
            except (KeyError, ValueError, TelemetryError) as exc:
                raise TelemetryError(f"line {line_number}: {exc}") from exc
    return clean_points(points)


def load_fit(path: str | Path, rider_id: str) -> list[TelemetryPoint]:
    """Load a FIT activity when the optional ``fitparse`` dependency is installed."""

    try:
        from fitparse import FitFile  # type: ignore[import-not-found]
        from fitparse.utils import FitParseError  # type: ignore[import-not-found]
    except ImportError as exc:
        raise TelemetryError("FIT support requires: pip install -e '.[fit]'") from exc

    points: list[TelemetryPoint] = []
    try:
        for record in FitFile(str(path)).get_messages("record"):
            values = record.get_values()
            if not all(key in values for key in ("timestamp", "position_lat", "position_long")):
                continue
            speed = values.get("enhanced_speed", values.get("speed"))
            if speed is None:
                continue
            timestamp = values["timestamp"]
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            points.append(
                TelemetryPoint(
                    rider_id=rider_id,
                    timestamp=timestamp.astimezone(timezone.utc),
                    latitude=float(values["position_lat"]) * (180.0 / 2**31),
                    longitude=float(values["position_long"]) * (180.0 / 2**31),
                    speed_mps=float(speed),
                    power_w=_as_float(values.get("power")),
                    elevation_m=_as_float(values.get("enhanced_altitude", values.get("altitude"))),
                    cadence_rpm=_as_float(values.get("cadence")),
                    heart_rate_bpm=_as_float(values.get("heart_rate")),
                    distance_m=_as_float(values.get("distance")),
                )
            )
    except (FitParseError, OSError, TypeError, ValueError) as exc:
        raise TelemetryError(f"invalid FIT file: {exc}") from exc
    return clean_points(points)


def _as_float(value: object) -> float | None:
    return float(value) if value is not None else None


def clean_points(points: Iterable[TelemetryPoint]) -> list[TelemetryPoint]:
    """Drop impossible samples and duplicate rider/timestamp records."""

    unique: dict[tuple[str, datetime], TelemetryPoint] = {}
    for point in points:
        if not point.rider_id or not (-90 <= point.latitude <= 90):
            continue
        if not (-180 <= point.longitude <= 180) or not (0 <= point.speed_mps <= 45):
            continue
        if point.power_w is not None and not (0 <= point.power_w <= 2500):
            continue
        unique[(point.rider_id, point.timestamp)] = point
    return sorted(unique.values(), key=lambda item: (item.timestamp, item.rider_id))


def haversine_m(a: TelemetryPoint, b: TelemetryPoint) -> float:
    lat1, lat2 = math.radians(a.latitude), math.radians(b.latitude)
    dlat = lat2 - lat1
    dlon = math.radians(b.longitude - a.longitude)
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))
