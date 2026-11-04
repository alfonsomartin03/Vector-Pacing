import unittest
from datetime import UTC, datetime, timedelta

from raceguard.analysis import _stable_heading, analyze
from raceguard.ingest import load_csv
from raceguard.models import AnalysisConfig, RiderProfile, TelemetryPoint
from raceguard.physics import expected_solo_power
from raceguard.reporting import result_to_dict, result_to_text


class AnalysisTests(unittest.TestCase):
    def test_sample_race_surfaces_reviewable_segments(self) -> None:
        result = analyze(load_csv("examples/sample_race.csv"))
        rider_142 = [segment for segment in result.segments if segment.rider_id == "142"]
        self.assertTrue(rider_142)
        self.assertEqual(rider_142[0].nearest_rider_id, "138")
        self.assertLess(rider_142[0].average_separation_m or 100, 15)
        self.assertAlmostEqual(rider_142[0].latitude or 0, 40.00125, places=5)
        self.assertAlmostEqual(rider_142[0].longitude or 1, -74.0, places=5)
        self.assertTrue(result.is_suspicious)
        self.assertGreater(result.confidence, 0.5)
        self.assertFalse(any(segment.rider_id == "138" for segment in result.segments))

    def test_reports_use_screening_language(self) -> None:
        result = analyze(load_csv("examples/sample_race.csv"))
        text = result_to_text(result).lower()
        payload = result_to_dict(result)
        self.assertIn("not a finding", text)
        self.assertIn("official", payload["disclaimer"].lower())
        self.assertNotIn("cheated", text)
        self.assertTrue(payload["summary"]["is_suspicious"])
        self.assertEqual(payload["summary"]["assessment"], "review_recommended")
        self.assertIn("latitude", payload["segments"][0])
        self.assertIn("Location:", result_to_text(result))

    def test_review_locations_are_chronological(self) -> None:
        result = analyze(load_csv("examples/sample_race.csv"))
        starts = [segment.start_time for segment in result.segments]

        self.assertEqual(starts, sorted(starts))

    def test_empty_input_is_safe(self) -> None:
        result = analyze([])
        self.assertEqual(result.segments, ())
        self.assertTrue(result.warnings)
        self.assertFalse(result.is_suspicious)
        self.assertEqual(result.confidence, 0.0)

    def test_short_single_rider_file_lacks_a_comparison_baseline(self) -> None:
        points = [
            point for point in load_csv("examples/sample_race.csv") if point.rider_id == "142"
        ]

        result = analyze(points)

        self.assertFalse(result.is_suspicious)
        self.assertIn("repeated comparable conditions", result.warnings[0])

    def test_fast_aero_rider_is_not_flagged_without_personal_deviation(self) -> None:
        profile = RiderProfile("aero", cda_m2=0.15)
        points = self._activity_points(profile, outlier_samples=0, baseline_speed=16.0)

        result = analyze(points)

        self.assertFalse(result.is_suspicious)
        self.assertTrue(any("no sustained power anomaly" in warning for warning in result.warnings))

    def test_sustained_speed_outlier_is_flagged_against_rider_baseline(self) -> None:
        profile = RiderProfile("aero", cda_m2=0.18)
        points = self._activity_points(profile, outlier_samples=40)

        result = analyze(points)

        self.assertTrue(result.is_suspicious)
        self.assertGreaterEqual(result.confidence, 0.55)
        self.assertGreater(result.segments[0].expected_power_w, result.segments[0].average_power_w)

    def test_coasting_at_high_speed_is_not_flagged(self) -> None:
        profile = RiderProfile("coasting", cda_m2=0.18)
        points = self._activity_points(profile, outlier_samples=0)
        started = points[0].timestamp
        points.extend(
            TelemetryPoint(
                rider_id=profile.rider_id,
                timestamp=started + timedelta(seconds=60 + index),
                latitude=40.006 + index * 0.0001,
                longitude=-74.0,
                speed_mps=15.0,
                power_w=0.0,
                cadence_rpm=0.0,
                gradient=-0.02,
            )
            for index in range(25)
        )

        result = analyze(points)

        self.assertFalse(result.is_suspicious)

    def test_short_soft_pedaling_and_restart_do_not_create_flag(self) -> None:
        profile = RiderProfile("transition", cda_m2=0.18)
        points = self._activity_points(profile, outlier_samples=0)
        started = points[0].timestamp
        baseline_power = points[0].power_w
        for index in range(16):
            restarting = index >= 8
            points.append(
                TelemetryPoint(
                    rider_id=profile.rider_id,
                    timestamp=started + timedelta(seconds=60 + index),
                    latitude=40.006 + index * 0.0001,
                    longitude=-74.0,
                    speed_mps=15.0,
                    power_w=baseline_power if restarting else 40.0,
                    cadence_rpm=85.0 if restarting else 20.0,
                    gradient=0.01,
                )
            )

        result = analyze(points)

        self.assertFalse(result.is_suspicious)

    def test_derived_downhill_gradient_prevents_fast_descent_flag(self) -> None:
        profile = RiderProfile("descender", cda_m2=0.18)
        started = datetime(2026, 6, 1, tzinfo=UTC)
        flat_power = expected_solo_power(15.0, 0.0, profile)
        downhill_power = expected_solo_power(15.0, -0.01, profile)
        points: list[TelemetryPoint] = []
        for index in range(85):
            descending = index >= 60
            descent_distance = max(0.0, (index - 59) * 15.0)
            points.append(
                TelemetryPoint(
                    rider_id=profile.rider_id,
                    timestamp=started + timedelta(seconds=index),
                    latitude=40.0 + index * 0.0001,
                    longitude=-74.0,
                    speed_mps=15.0,
                    power_w=downhill_power if descending else flat_power,
                    elevation_m=100.0 - 0.01 * descent_distance,
                    distance_m=index * 15.0,
                    cadence_rpm=90.0,
                    # FIT records do not include grade; analysis must derive it.
                    gradient=0.0,
                )
            )

        result = analyze(points)

        self.assertFalse(result.is_suspicious)

    def test_lower_power_on_a_descent_is_not_compared_with_flat_riding(self) -> None:
        profile = RiderProfile("hill", cda_m2=0.25)
        flat = expected_solo_power(12, 0, profile)
        downhill = expected_solo_power(12, -0.02, profile)
        points = self._phased_activity(
            "hill", [(50, 12, 0, flat), (40, 12, -0.02, downhill), (50, 12, 0, flat)]
        )

        self.assertFalse(analyze(points).is_suspicious)

    def test_opposite_direction_is_not_a_power_reference(self) -> None:
        profile = RiderProfile("turnaround", cda_m2=0.25)
        normal = expected_solo_power(12, 0, profile)
        points = self._phased_activity(
            "turnaround", [(60, 12, 0, normal), (40, 12, 0, normal - 100)]
        )
        points = [
            TelemetryPoint(
                rider_id=point.rider_id,
                timestamp=point.timestamp,
                latitude=40.006 - (index - 60) * 0.0001 if index >= 60 else point.latitude,
                longitude=point.longitude,
                speed_mps=point.speed_mps,
                power_w=point.power_w,
                cadence_rpm=point.cadence_rpm,
                gradient=point.gradient,
            )
            for index, point in enumerate(points)
        ]

        self.assertFalse(analyze(points).is_suspicious)

    def test_side_by_side_riders_are_not_trailing_evidence(self) -> None:
        started = datetime(2026, 6, 1, tzinfo=UTC)
        points = [
            TelemetryPoint(
                rider_id=rider_id,
                timestamp=started + timedelta(seconds=index),
                latitude=40 + index * 0.0001,
                longitude=-74 + longitude_offset,
                speed_mps=12,
                power_w=300,
                cadence_rpm=90,
            )
            for index in range(40)
            for rider_id, longitude_offset in (("a", 0.0), ("b", 0.0001))
        ]

        self.assertFalse(analyze(points).is_suspicious)

    def test_trailing_gps_still_works_without_power(self) -> None:
        points = [
            TelemetryPoint(
                rider_id=point.rider_id,
                timestamp=point.timestamp,
                latitude=point.latitude,
                longitude=point.longitude,
                speed_mps=point.speed_mps,
            )
            for point in load_csv("examples/sample_race.csv")
        ]

        result = analyze(points)

        self.assertEqual([segment.rider_id for segment in result.segments], ["142"])
        self.assertIsNone(result.segments[0].average_power_w)

    def test_multi_rider_output_labels_ahead_and_behind_metrics(self) -> None:
        result = analyze(load_csv("examples/sample_race.csv"))
        segment = next(segment for segment in result.segments if segment.rider_id == "142")

        self.assertEqual(segment.rider_ahead_id, "138")
        self.assertEqual(segment.rider_behind_id, "142")
        self.assertIsNotNone(segment.rider_ahead_speed_mps)
        self.assertIsNotNone(segment.rider_behind_speed_mps)
        self.assertIsNotNone(segment.rider_ahead_power_w)
        self.assertIsNotNone(segment.rider_behind_power_w)
        self.assertAlmostEqual(segment.direction_heading_deg or -1, 0.0, delta=1.0)
        self.assertIn("Rider ahead: 138", result_to_text(result))
        self.assertIn("Rider behind: 142", result_to_text(result))
        self.assertIn("Direction of travel: N", result_to_text(result))

    def test_heading_uses_coherent_travel_direction(self) -> None:
        started = datetime(2026, 6, 1, tzinfo=UTC)
        points = [
            TelemetryPoint(
                rider_id="northbound",
                timestamp=started + timedelta(seconds=index),
                latitude=40 + index * 0.0001,
                longitude=-74.0,
                speed_mps=12.0,
            )
            for index in range(6)
        ]

        heading = _stable_heading(points, AnalysisConfig())

        self.assertIsNotNone(heading)
        self.assertAlmostEqual(heading or -1, 0.0, delta=1.0)

    def test_heading_rejects_a_turnaround_as_ambiguous(self) -> None:
        started = datetime(2026, 6, 1, tzinfo=UTC)
        latitudes = [40.0, 40.0001, 40.0002, 40.0001, 40.0]
        points = [
            TelemetryPoint(
                rider_id="turnaround",
                timestamp=started + timedelta(seconds=index),
                latitude=latitude,
                longitude=-74.0,
                speed_mps=12.0,
            )
            for index, latitude in enumerate(latitudes)
        ]

        self.assertIsNone(_stable_heading(points, AnalysisConfig()))

    def test_close_pass_under_ten_seconds_is_not_proximity_evidence(self) -> None:
        started = datetime(2026, 6, 1, tzinfo=UTC)
        points = [
            TelemetryPoint(
                rider_id=rider_id,
                timestamp=started + timedelta(seconds=index),
                latitude=40 + index * 0.0001 + latitude_offset,
                longitude=-74.0,
                speed_mps=12.0,
            )
            for index in range(10)
            for rider_id, latitude_offset in (("ahead", 0.00006), ("behind", 0.0))
        ]

        result = analyze(points)

        self.assertFalse(result.is_suspicious)

    def test_repeated_low_power_sections_are_compared_with_similar_sections(self) -> None:
        profile = RiderProfile("repeat", cda_m2=0.25)
        normal_power = expected_solo_power(12.0, 0.01, profile)
        filler_power = expected_solo_power(9.0, 0.03, profile)
        phases = [
            (40, 12.0, 0.01, normal_power),
            (40, 9.0, 0.03, filler_power),
            (40, 12.0, 0.01, normal_power - 100),
            (40, 9.0, 0.03, filler_power),
            (40, 12.0, 0.01, normal_power),
            (40, 9.0, 0.03, filler_power),
            (40, 12.0, 0.01, normal_power - 100),
            (40, 9.0, 0.03, filler_power),
            (40, 12.0, 0.01, normal_power),
        ]
        points = self._phased_activity(profile.rider_id, phases)

        result = analyze(points)

        contextual = [
            segment
            for segment in result.segments
            if any("comparable sections" in note for note in segment.evidence.notes)
        ]
        self.assertGreaterEqual(len(contextual), 2)
        self.assertEqual(
            [segment.start_time for segment in contextual],
            sorted(segment.start_time for segment in contextual),
        )

    def test_single_rider_event_between_fixed_boundaries_is_detected(self) -> None:
        profile = RiderProfile("offset", cda_m2=0.25)
        normal_power = expected_solo_power(12.0, 0.01, profile)
        points = self._phased_activity(
            profile.rider_id,
            [
                (127, 12.0, 0.01, normal_power),
                (18, 12.0, 0.01, normal_power - 100),
                (127, 12.0, 0.01, normal_power),
            ],
        )

        result = analyze(points)

        self.assertTrue(result.is_suspicious)
        event_start = points[127].timestamp
        event_end = points[144].timestamp
        self.assertTrue(
            any(
                segment.start_time <= event_end and segment.end_time >= event_start
                for segment in result.segments
            )
        )

    def test_multiple_single_rider_events_are_reported_separately(self) -> None:
        profile = RiderProfile("multiple", cda_m2=0.25)
        normal_power = expected_solo_power(12.0, 0.01, profile)
        points = self._phased_activity(
            profile.rider_id,
            [
                (100, 12.0, 0.01, normal_power),
                (22, 12.0, 0.01, normal_power - 100),
                (70, 12.0, 0.01, normal_power),
                (22, 12.0, 0.01, normal_power - 100),
                (100, 12.0, 0.01, normal_power),
            ],
        )

        result = analyze(points)

        self.assertGreaterEqual(len(result.segments), 2)
        self.assertGreater(
            (result.segments[1].start_time - result.segments[0].end_time).total_seconds(),
            30,
        )

    @staticmethod
    def _phased_activity(
        rider_id: str, phases: list[tuple[int, float, float, float]]
    ) -> list[TelemetryPoint]:
        started = datetime(2026, 6, 1, tzinfo=UTC)
        points: list[TelemetryPoint] = []
        sample_index = 0
        for duration, speed, gradient, power in phases:
            for _ in range(duration):
                points.append(
                    TelemetryPoint(
                        rider_id=rider_id,
                        timestamp=started + timedelta(seconds=sample_index),
                        latitude=40.0 + sample_index * 0.0001,
                        longitude=-74.0,
                        speed_mps=speed,
                        power_w=power,
                        cadence_rpm=90.0,
                        gradient=gradient,
                    )
                )
                sample_index += 1
        return points

    @staticmethod
    def _activity_points(
        profile: RiderProfile, outlier_samples: int, baseline_speed: float = 12.0
    ) -> list[TelemetryPoint]:
        started = datetime(2026, 6, 1, tzinfo=UTC)
        baseline_samples = 60
        baseline_power = expected_solo_power(baseline_speed, 0.01, profile)
        points = [
            TelemetryPoint(
                rider_id=profile.rider_id,
                timestamp=started + timedelta(seconds=index),
                latitude=40.0 + index * 0.0001,
                longitude=-74.0,
                speed_mps=baseline_speed,
                power_w=baseline_power,
                gradient=0.01,
            )
            for index in range(baseline_samples)
        ]
        points.extend(
            TelemetryPoint(
                rider_id=profile.rider_id,
                timestamp=started + timedelta(seconds=baseline_samples + index),
                latitude=40.006 + index * 0.0001,
                longitude=-74.0,
                speed_mps=15.0,
                power_w=baseline_power,
                gradient=0.01,
            )
            for index in range(outlier_samples)
        )
        return points


if __name__ == "__main__":
    unittest.main()
