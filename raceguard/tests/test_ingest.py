import tempfile
import unittest
from pathlib import Path

from raceguard.ingest import TelemetryError, haversine_m, load_csv


class IngestTests(unittest.TestCase):
    def test_sample_csv_is_normalized(self) -> None:
        points = load_csv("examples/sample_race.csv")
        self.assertEqual(len(points), 12)
        rider_142 = next(point for point in points if point.rider_id == "142")
        self.assertAlmostEqual(rider_142.speed_mps, 47.8 / 3.6)
        self.assertIsNotNone(points[0].timestamp.tzinfo)

    def test_haversine_distance_is_reasonable(self) -> None:
        points = load_csv("examples/sample_race.csv")
        self.assertAlmostEqual(haversine_m(points[0], points[1]), 10.0, delta=0.3)

    def test_missing_columns_are_explained(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.csv"
            path.write_text("rider_id,timestamp\n1,2026-01-01T00:00:00Z\n", encoding="utf-8")
            with self.assertRaisesRegex(TelemetryError, "missing required CSV columns"):
                load_csv(path)


if __name__ == "__main__":
    unittest.main()
