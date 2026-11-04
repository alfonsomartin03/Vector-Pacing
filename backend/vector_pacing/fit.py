"""Minimal reader (and test writer) for Garmin FIT activity files.

Only what the drafting check needs is decoded: ``record`` messages (global number 20) with
``timestamp`` (253), ``position_lat`` (0), ``position_long`` (1), ``gps_accuracy`` (31) and, for the
power screen, ``speed``/``enhanced_speed`` (6/73), ``power`` (7), ``cadence`` (4), ``heart_rate`` (3),
``altitude``/``enhanced_altitude`` (2/78) and ``distance`` (5). Every other message and field is
skipped by size, so files from any head unit or app are accepted as
long as they follow the FIT container format (normal and compressed-timestamp headers, developer
fields, either byte order, chained files). The file CRC is verified.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from .drafting import ScenarioError

MAX_FIT_BYTES = 20 * 1024 * 1024
FIT_EPOCH_S = 631_065_600  # 1989-12-31T00:00:00Z as a Unix timestamp
SEMICIRCLE_DEG = 180.0 / 2**31

_MESG_RECORD = 20
_F_TIMESTAMP, _F_LAT, _F_LON, _F_ACCURACY = 253, 0, 1, 31
_F_ALT, _F_HR, _F_CAD, _F_DIST, _F_SPEED, _F_POWER, _F_ESPEED, _F_EALT = 2, 3, 4, 5, 6, 7, 73, 78
# field number -> (struct code, scale, offset); value = raw / scale - offset
_FIELDS: dict[int, tuple[str, float, float]] = {
    _F_TIMESTAMP: ("I", 1, 0),
    _F_LAT: ("i", 1, 0),
    _F_LON: ("i", 1, 0),
    _F_ACCURACY: ("B", 1, 0),
    _F_ALT: ("H", 5, 500),
    _F_HR: ("B", 1, 0),
    _F_CAD: ("B", 1, 0),
    _F_DIST: ("I", 100, 0),
    _F_SPEED: ("H", 1000, 0),
    _F_POWER: ("H", 1, 0),
    _F_ESPEED: ("I", 1000, 0),
    _F_EALT: ("I", 5, 500),
}
_SIZE = {"B": 1, "H": 2, "I": 4, "i": 4}
_INVALID = {"B": 0xFF, "H": 0xFFFF, "I": 0xFFFFFFFF, "i": 0x7FFFFFFF}

_CRC_TABLE = (
    0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
    0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400,
)  # fmt: skip


def fit_crc(data: bytes, crc: int = 0) -> int:
    for byte in data:
        tmp = _CRC_TABLE[crc & 0xF]
        crc = ((crc >> 4) & 0x0FFF) ^ tmp ^ _CRC_TABLE[byte & 0xF]
        tmp = _CRC_TABLE[crc & 0xF]
        crc = ((crc >> 4) & 0x0FFF) ^ tmp ^ _CRC_TABLE[(byte >> 4) & 0xF]
    return crc


@dataclass
class FitTrack:
    """Positions from one FIT file. ``t`` is Unix time in seconds."""

    t: list[float] = field(default_factory=list)
    lat: list[float] = field(default_factory=list)
    lon: list[float] = field(default_factory=list)
    accuracy_m: list[float | None] = field(default_factory=list)
    speed_mps: list[float | None] = field(default_factory=list)
    power_w: list[float | None] = field(default_factory=list)
    cadence_rpm: list[float | None] = field(default_factory=list)
    heart_rate_bpm: list[float | None] = field(default_factory=list)
    altitude_m: list[float | None] = field(default_factory=list)
    distance_m: list[float | None] = field(default_factory=list)
    records_without_position: int = 0

    @property
    def has_power(self) -> bool:
        return any(p is not None for p in self.power_w)


@dataclass
class _Def:
    little: bool
    global_num: int
    fields: list[tuple[int, int]]  # (field number, size)
    size: int


def read_fit(data: bytes, name: str = "file") -> FitTrack:
    """Extract timestamped positions from a FIT file. Raises :class:`ScenarioError`."""
    if len(data) == 0:
        raise ScenarioError(f"{name}: the file is empty.")
    if len(data) > MAX_FIT_BYTES:
        raise ScenarioError(f"{name}: the file is larger than {MAX_FIT_BYTES // (1024 * 1024)} MB.")
    track = FitTrack()
    pos = 0
    while pos < len(data):
        if len(data) - pos < 12:
            if pos == 0:
                raise ScenarioError(f"{name}: not a FIT file (too short).")
            break  # trailing padding after a complete file
        header_size = data[pos]
        if header_size not in (12, 14) or data[pos + 8 : pos + 12] != b".FIT":
            raise ScenarioError(f"{name}: not a FIT file (missing .FIT header).")
        (data_size,) = struct.unpack_from("<I", data, pos + 4)
        end = pos + header_size + data_size
        if end + 2 > len(data):
            raise ScenarioError(f"{name}: the FIT file is truncated.")
        (stored_crc,) = struct.unpack_from("<H", data, end)
        if fit_crc(data[pos:end]) != stored_crc:
            raise ScenarioError(f"{name}: the FIT file is corrupted (checksum mismatch).")
        _read_records(data, pos + header_size, end, track, name)
        pos = end + 2
    return track


def _read_records(data: bytes, pos: int, end: int, track: FitTrack, name: str) -> None:
    defs: dict[int, _Def] = {}
    last_ts: int | None = None
    try:
        while pos < end:
            h = data[pos]
            pos += 1
            ts: int | None = None
            if h & 0x80:  # compressed timestamp header
                local = (h >> 5) & 0x03
                offset = h & 0x1F
                if last_ts is not None:
                    ts = (last_ts & ~0x1F) + offset
                    if offset < (last_ts & 0x1F):
                        ts += 0x20
                    last_ts = ts
            elif h & 0x40:  # definition message
                local = h & 0x0F
                little = data[pos + 1] == 0
                (global_num,) = struct.unpack_from("<H" if little else ">H", data, pos + 2)
                n = data[pos + 4]
                pos += 5
                fields = [(data[pos + 3 * i], data[pos + 3 * i + 1]) for i in range(n)]
                pos += 3 * n
                size = sum(s for _, s in fields)
                if h & 0x20:  # developer fields: skipped, only their size matters
                    nd = data[pos]
                    pos += 1
                    size += sum(data[pos + 3 * i + 1] for i in range(nd))
                    pos += 3 * nd
                defs[local] = _Def(little, global_num, fields, size)
                continue
            else:
                local = h & 0x0F
            d = defs.get(local)
            if d is None:
                raise ScenarioError(f"{name}: data message before its definition; the FIT file is malformed.")
            if pos + d.size > end:
                raise IndexError
            values: dict[int, float] = {}
            p = pos
            order = "<" if d.little else ">"
            for num, size in d.fields:
                spec = _FIELDS.get(num)
                if spec is not None and _SIZE[spec[0]] == size:
                    (raw,) = struct.unpack_from(order + spec[0], data, p)
                    if raw != _INVALID[spec[0]] and not (spec[0] == "i" and raw == -0x80000000):
                        values[num] = raw / spec[1] - spec[2] if num not in (_F_LAT, _F_LON) else raw
                p += size
            pos += d.size
            raw_ts = values.get(_F_TIMESTAMP)
            if raw_ts is not None:
                ts = last_ts = int(raw_ts)
            if d.global_num != _MESG_RECORD or ts is None:
                continue
            lat, lon = values.get(_F_LAT), values.get(_F_LON)
            if lat is None or lon is None:
                track.records_without_position += 1
                continue
            acc = values.get(_F_ACCURACY)
            track.t.append(float(ts + FIT_EPOCH_S))
            track.lat.append(lat * SEMICIRCLE_DEG)
            track.lon.append(lon * SEMICIRCLE_DEG)
            track.accuracy_m.append(acc if acc else None)
            track.speed_mps.append(values.get(_F_ESPEED, values.get(_F_SPEED)))
            track.power_w.append(values.get(_F_POWER))
            track.cadence_rpm.append(values.get(_F_CAD))
            track.heart_rate_bpm.append(values.get(_F_HR))
            track.altitude_m.append(values.get(_F_EALT, values.get(_F_ALT)))
            track.distance_m.append(values.get(_F_DIST))
    except (IndexError, struct.error):
        raise ScenarioError(f"{name}: the FIT file is malformed (a message runs past the end).") from None


_WRITE_EXTRAS = (  # (key, field number, struct code, scale, offset)
    ("speed_mps", _F_SPEED, "H", 1000, 0),
    ("power_w", _F_POWER, "H", 1, 0),
    ("cadence_rpm", _F_CAD, "B", 1, 0),
    ("heart_rate_bpm", _F_HR, "B", 1, 0),
    ("altitude_m", _F_ALT, "H", 5, 500),
    ("distance_m", _F_DIST, "I", 100, 0),
)
_BASE_TYPE = {"B": 0x02, "H": 0x84, "I": 0x86, "i": 0x85}


def write_fit(
    samples: list[tuple[float, float, float, float | None]],
    telemetry: list[dict[str, float | None]] | None = None,
) -> bytes:
    """Encode ``(unix_time, lat, lon, accuracy_m | None)`` samples as a minimal FIT activity file.

    ``telemetry`` (optional, one dict per sample) adds ``speed_mps``, ``power_w``, ``cadence_rpm``,
    ``heart_rate_bpm``, ``altitude_m`` and ``distance_m``. Used for bundled samples and tests; real
    files come from head units and apps.
    """
    extras = [e for e in _WRITE_EXTRAS if telemetry and any(row.get(e[0]) is not None for row in telemetry)]
    out = bytearray()
    # file_id (global 0): type = activity (4)
    out += bytes([0x40, 0, 0]) + struct.pack("<HB", 0, 1) + bytes([0, 1, 0x00])
    out += bytes([0x00, 4])
    # record (global 20): timestamp, position_lat, position_long, gps_accuracy, extras
    out += bytes([0x41, 0, 0]) + struct.pack("<HB", _MESG_RECORD, 4 + len(extras))
    out += bytes([_F_TIMESTAMP, 4, 0x86, _F_LAT, 4, 0x85, _F_LON, 4, 0x85, _F_ACCURACY, 1, 0x02])
    for _, num, code, _, _ in extras:
        out += bytes([num, _SIZE[code], _BASE_TYPE[code]])
    fmt = "<IiiB" + "".join(e[2] for e in extras)
    for i, (t, lat, lon, acc) in enumerate(samples):
        a = _INVALID["B"] if acc is None else max(1, min(254, round(acc)))
        row = telemetry[i] if telemetry else {}
        vals = []
        for key, _, code, scale, offset in extras:
            v = row.get(key)
            vals.append(_INVALID[code] if v is None else round((v + offset) * scale))
        out += bytes([0x01]) + struct.pack(
            fmt, round(t) - FIT_EPOCH_S, round(lat / SEMICIRCLE_DEG), round(lon / SEMICIRCLE_DEG), a, *vals
        )
    header = bytearray(struct.pack("<BBHI4s", 14, 0x20, 2132, len(out), b".FIT"))
    header += struct.pack("<H", fit_crc(bytes(header)))
    body = bytes(header) + bytes(out)
    return body + struct.pack("<H", fit_crc(body))
