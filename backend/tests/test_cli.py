"""Command-line drafting review."""

import json
from pathlib import Path

from vector_pacing.cli import main

D = Path(__file__).resolve().parents[2] / "samples" / "drafting_with_power"


def test_text_report(capsys):
    assert main([str(D / "R01.fit"), str(D / "R02.fit"), "--course", str(D / "course.gpx")]) == 0
    out = capsys.readouterr().out
    assert "not a finding" in out and "E001  R02 behind R01 — potential drafting" in out
    assert "P001  rider R02" in out and "GPS: E001" in out
    assert "cheat" not in out.lower()


def test_json_report_to_file(tmp_path):
    out = tmp_path / "r.json"
    assert main([str(D / "R02.fit"), "--json", "--output", str(out)]) == 0
    body = json.loads(out.read_text())
    assert body["events"] == [] and len(body["power_screen"]["anomalies"]) == 1


def test_errors_exit_2(capsys, tmp_path):
    assert main([str(D / "course.gpx")]) == 2
    assert main([str(D / "R01.fit"), "--settings", '{"zone_length_m": 0}']) == 2
    assert "error:" in capsys.readouterr().err
