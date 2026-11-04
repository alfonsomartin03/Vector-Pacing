"""FIT container decoding: round trip, header variants, corruption and missing positions."""

import struct

import pytest

from vector_pacing.drafting import ScenarioError
from vector_pacing.fit import FIT_EPOCH_S, SEMICIRCLE_DEG, fit_crc, read_fit, write_fit

T0 = 1_782_896_400.0  # 2026-07-01T09:00:00Z
SAMPLES = [(T0 + i, 47.0 + i * 1e-5, 8.0 + i * 2e-5, 3.0 if i % 2 else None) for i in range(10)]


def wrap(records: bytes) -> bytes:
    header = struct.pack("<BBHI4s", 12, 0x20, 2132, len(records), b".FIT")
    body = header + records
    return body + struct.pack("<H", fit_crc(body))


def test_round_trip():
    tr = read_fit(write_fit(SAMPLES))
    assert tr.t == [s[0] for s in SAMPLES]
    assert tr.lat == pytest.approx([s[1] for s in SAMPLES], abs=1e-7)
    assert tr.lon == pytest.approx([s[2] for s in SAMPLES], abs=1e-7)
    assert tr.accuracy_m == [s[3] for s in SAMPLES]


def test_chained_files_are_concatenated():
    tr = read_fit(write_fit(SAMPLES[:4]) + write_fit(SAMPLES[4:]))
    assert len(tr.t) == 10


def test_big_endian_compressed_timestamps_and_developer_fields():
    sc = lambda deg: round(deg / SEMICIRCLE_DEG)  # noqa: E731
    ts = int(T0) - FIT_EPOCH_S
    rec = bytearray()
    # big-endian record definition (local 0) with one developer field of 2 bytes
    rec += bytes([0x60, 0, 1]) + struct.pack(">HB", 20, 3) + bytes([253, 4, 0x86, 0, 4, 0x85, 1, 4, 0x85])
    rec += bytes([1, 99, 2, 0])
    rec += bytes([0x00]) + struct.pack(">Iii", ts, sc(47.0), sc(8.0)) + b"\x00\x00"
    # local 1: position only, timestamps via compressed headers
    rec += bytes([0x41, 0, 1]) + struct.pack(">HB", 20, 2) + bytes([0, 4, 0x85, 1, 4, 0x85])
    for k in (1, 2, 20, 45):  # steps < 32 s; crossing a 32 s boundary wraps the 5-bit counter
        rec += bytes([0x80 | (1 << 5) | ((ts + k) & 0x1F)]) + struct.pack(">ii", sc(47.0 + k * 1e-5), sc(8.0))
    # record without position
    rec += bytes([0x00]) + struct.pack(">Iii", ts + 46, 0x7FFFFFFF, 0x7FFFFFFF) + b"\x00\x00"
    tr = read_fit(wrap(bytes(rec)))
    assert tr.t == [T0, T0 + 1, T0 + 2, T0 + 20, T0 + 45]
    assert tr.lat[4] == pytest.approx(47.00045, abs=1e-7)
    assert tr.records_without_position == 1


@pytest.mark.parametrize(
    ("data", "fragment"),
    [
        (b"", "empty"),
        (b"<gpx></gpx>", "not a FIT file"),
        (b"\x0e\x20\x54\x08\x00\x00\x00\x00.GPX\x00\x00\x00\x00", "missing .FIT header"),
        (write_fit(SAMPLES)[:-20], "truncated"),
        (write_fit(SAMPLES)[:-1] + b"\x00", "checksum"),
        (wrap(bytes([0x00, 1, 2, 3])), "before its definition"),
    ],
)
def test_invalid_files(data, fragment):
    with pytest.raises(ScenarioError, match=fragment):
        read_fit(data, "x.fit")


def test_telemetry_fields_round_trip():
    tele = [
        {"speed_mps": 12.345, "power_w": 250.0 + i, "cadence_rpm": 90.0, "heart_rate_bpm": 150.0,
         "altitude_m": 412.4, "distance_m": 12.34 * i}
        for i in range(10)
    ]  # fmt: skip
    tele[3]["power_w"] = None
    tr = read_fit(write_fit(SAMPLES, tele))
    assert tr.has_power
    assert tr.speed_mps[0] == pytest.approx(12.345)
    assert tr.power_w[3] is None and tr.power_w[4] == 254.0
    assert tr.cadence_rpm[0] == 90.0 and tr.heart_rate_bpm[0] == 150.0
    assert tr.altitude_m[0] == pytest.approx(412.4)
    assert tr.distance_m[9] == pytest.approx(111.06)
    assert not read_fit(write_fit(SAMPLES)).has_power
