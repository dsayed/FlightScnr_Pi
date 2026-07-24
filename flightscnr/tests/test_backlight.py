import unittest
from unittest.mock import patch

from display.round_touch import backlight


class TestSoftwareDimAlpha(unittest.TestCase):
    def test_full_brightness_no_overlay(self):
        self.assertEqual(backlight.software_dim_alpha(100), 0)

    def test_zero_brightness_full_black(self):
        self.assertEqual(backlight.software_dim_alpha(0), 255)

    def test_half_brightness_is_half_alpha(self):
        # 50% brightness -> ~50% black overlay (luminance scales linearly).
        self.assertEqual(backlight.software_dim_alpha(50), 128)

    def test_monotonic_decreasing_with_brightness(self):
        prev = 256
        for pct in range(0, 101, 5):
            a = backlight.software_dim_alpha(pct)
            self.assertLessEqual(a, prev)
            prev = a

    def test_clamps_out_of_range(self):
        self.assertEqual(backlight.software_dim_alpha(-20), 255)
        self.assertEqual(backlight.software_dim_alpha(150), 0)


class TestHasHardware(unittest.TestCase):
    def test_true_when_backlight_paths_exist(self):
        with patch.object(backlight, "_backlight_paths", return_value=["/sys/x/brightness"]):
            self.assertTrue(backlight.has_hardware())

    def test_false_when_no_backlight_paths(self):
        with patch.object(backlight, "_backlight_paths", return_value=[]):
            self.assertFalse(backlight.has_hardware())


if __name__ == "__main__":
    unittest.main()
