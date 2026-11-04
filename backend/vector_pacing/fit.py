"""Minimal reader (and test writer) for Garmin FIT activity files.

Only what the drafting check needs is decoded: ``record`` messages (global number 20) with
``timestamp`` (253), ``position_lat`` (0), ``position_long`` (1) and ``gps_accuracy`` (31). Every
other message and field is skipped by size, so files from any head unit or app are accepted as
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
_INVALID = {1: 0xFF, 4: 0xFFFFFFFF}
_INVALID_SINT32 = 0x7FFFFFFF

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
    records_without_position: int = 0


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
            values: dict[int, int] = {}
            p = pos
            for num, size in d.fields:
                if num in (_F_TIMESTAMP, _F_LAT, _F_LON, _F_ACCURACY) and size in (1, 4):
                    signed = num in (_F_LAT, _F_LON)
                    fmt = ("b" if signed else "B") if size == 1 else ("i" if signed else "I")
                    (values[num],) = struct.unpack_from(("<" if d.little else ">") + fmt, data, p)
                p += size
            pos += d.size
            raw_ts = values.get(_F_TIMESTAMP)
            if raw_ts is not None and raw_ts != _INVALID[4]:
                ts = last_ts = raw_ts
            if d.global_num != _MESG_RECORD:
                continue
            lat, lon = values.get(_F_LAT), values.get(_F_LON)
            if ts is None:
                continue
            if lat is None or lon is None or _INVALID_SINT32 in (lat, lon) or -_INVALID_SINT32 - 1 in (lat, lon):
                track.records_without_position += 1
                continue
            acc = values.get(_F_ACCURACY)
            track.t.append(float(ts + FIT_EPOCH_S))
            track.lat.append(lat * SEMICIRCLE_DEG)
            track.lon.append(lon * SEMICIRCLE_DEG)
            track.accuracy_m.append(float(acc) if acc not in (None, 0, _INVALID[1]) else None)
    except (IndexError, struct.error):
        raise ScenarioError(f"{name}: the FIT file is malformed (a message runs past the end).") from None


def write_fit(samples: list[tuple[float, float, float, float | None]]) -> bytes:
    """Encode ``(unix_time, lat, lon, accuracy_m | None)`` samples as a minimal FIT activity file.

    Used for bundled samples and tests; real files come from head units and apps.
    """
    out = bytearray()
    # file_id (global 0): type = activity (4)
    out += bytes([0x40, 0, 0]) + struct.pack("<HB", 0, 1) + bytes([0, 1, 0x00])
    out += bytes([0x00, 4])
    # record (global 20): timestamp, position_lat, position_long, gps_accuracy
    out += bytes([0x41, 0, 0]) + struct.pack("<HB", _MESG_RECORD, 4)
    out += bytes([_F_TIMESTAMP, 4, 0x86, _F_LAT, 4, 0x85, _F_LON, 4, 0x85, _F_ACCURACY, 1, 0x02])
    for t, lat, lon, acc in samples:
        a = _INVALID[1] if acc is None else max(1, min(254, round(acc)))
        out += bytes([0x01]) + struct.pack(
            "<IiiB", round(t) - FIT_EPOCH_S, round(lat / SEMICIRCLE_DEG), round(lon / SEMICIRCLE_DEG), a
        )
    header = bytearray(struct.pack("<BBHI4s", 14, 0x20, 2132, len(out), b".FIT"))
    header += struct.pack("<H", fit_crc(bytes(header)))
    body = bytes(header) + bytes(out)
    return body + struct.pack("<H", fit_crc(body))
