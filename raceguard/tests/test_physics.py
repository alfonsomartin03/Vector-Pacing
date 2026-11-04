import unittest

from raceguard.models import RiderProfile
from raceguard.physics import expected_solo_power


class PhysicsTests(unittest.TestCase):
    def test_expected_power_rises_with_speed(self) -> None:
        rider = RiderProfile("142")
        self.assertGreater(
            expected_solo_power(12.0, 0.0, rider),
            expected_solo_power(8.0, 0.0, rider),
        )

    def test_expected_power_accounts_for_gradient(self) -> None:
        rider = RiderProfile("142")
        flat = expected_solo_power(10.0, 0.0, rider)
        uphill = expected_solo_power(10.0, 0.05, rider)
        self.assertGreater(uphill, flat)

    def test_negative_speed_is_rejected(self) -> None:
        rider = RiderProfile("142")
        with self.assertRaisesRegex(ValueError, "speed"):
            expected_solo_power(-1.0, 0.0, rider)


if __name__ == "__main__":
    unittest.main()
