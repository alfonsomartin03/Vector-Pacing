"""RaceGuard command-line entry point."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .analysis import analyze
from .ingest import TelemetryError, load_csv, load_fit
from .reporting import result_to_dict, result_to_text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Screen race telemetry for segments warranting review")
    parser.add_argument("input", type=Path, help="CSV or FIT telemetry file")
    parser.add_argument("--rider-id", help="Rider identifier (required for a FIT file)")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    parser.add_argument("--output", type=Path, help="Write the report to a file")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.input.suffix.lower() == ".fit":
            if not args.rider_id:
                raise TelemetryError("--rider-id is required for FIT input")
            points = load_fit(args.input, args.rider_id)
        else:
            points = load_csv(args.input)
        result = analyze(points)
    except (OSError, TelemetryError) as exc:
        build_parser().error(str(exc))

    report = json.dumps(result_to_dict(result), indent=2) if args.json else result_to_text(result)
    if args.output:
        args.output.write_text(report + "\n", encoding="utf-8")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

