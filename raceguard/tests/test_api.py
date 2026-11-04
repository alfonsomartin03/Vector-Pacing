import unittest

from fastapi.testclient import TestClient

from raceguard.api import app


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_csv_upload_returns_analysis(self) -> None:
        with open("examples/sample_race.csv", "rb") as sample:
            response = self.client.post(
                "/api/analyze",
                files={"file": ("sample_race.csv", sample, "text/csv")},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["summary"]["assessment"], "review_recommended")

    def test_fit_rider_id_is_read_from_upload_form(self) -> None:
        response = self.client.post(
            "/api/analyze",
            data={"rider_id": "142"},
            files={"file": ("activity.fit", b"not-a-fit-file", "application/octet-stream")},
        )

        self.assertEqual(response.status_code, 422)
        self.assertNotIn("rider_id is required", response.text)
        self.assertIn("invalid FIT file", response.text)

    def test_multiple_activity_files_are_analyzed_together(self) -> None:
        with open("examples/sample_race.csv", "rb") as sample_file:
            sample = sample_file.read()
        second_race = sample.replace(b"142,", b"242,").replace(b"138,", b"238,")

        response = self.client.post(
            "/api/analyze",
            files=[
                ("file", ("race-a.csv", sample, "text/csv")),
                ("file", ("race-b.csv", second_race, "text/csv")),
            ],
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["summary"]["points_analyzed"], 24)
        self.assertEqual(response.json()["summary"]["riders_analyzed"], ["138", "142", "238", "242"])

    def test_combined_upload_limit_is_enforced(self) -> None:
        response = self.client.post(
            "/api/analyze",
            files={"file": ("oversized.csv", b"x" * (4 * 1024 * 1024 + 1), "text/csv")},
        )

        self.assertEqual(response.status_code, 413)
        self.assertIn("4 MB", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
