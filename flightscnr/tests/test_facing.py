"""Unit tests for radar facing / compass reorientation."""

from __future__ import annotations

import json
import math
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_DATA_DIR = tempfile.mkdtemp(prefix="flightscnr-facing-")
os.environ["FLIGHTSCNR_DATA_DIR"] = _DATA_DIR
os.environ.setdefault("HOME_LAT", "51.5")
os.environ.setdefault("HOME_LON", "-0.1")


class TestRotateOffset(unittest.TestCase):
    def test_identity_at_zero(self):
        from display.round_touch.geo import rotate_offset

        self.assertEqual(rotate_offset(3.0, 4.0, 0), (3.0, 4.0))

    def test_east_up_maps_east_to_screen_up(self):
        from display.round_touch.geo import rotate_offset

        # Facing east: geographic east (+dx) → screen-up (+dy')
        dx, dy = rotate_offset(5.0, 0.0, 90.0)
        self.assertAlmostEqual(dx, 0.0, places=6)
        self.assertAlmostEqual(dy, 5.0, places=6)

    def test_south_up_flips_north(self):
        from display.round_touch.geo import rotate_offset

        dx, dy = rotate_offset(0.0, 4.0, 180.0)
        self.assertAlmostEqual(dx, 0.0, places=6)
        self.assertAlmostEqual(dy, -4.0, places=6)

    def test_preserves_distance(self):
        from display.round_touch.geo import rotate_offset

        for facing in (0, 45, 90, 135, 180, 270, 359):
            dx, dy = rotate_offset(3.0, 4.0, facing)
            self.assertAlmostEqual(math.hypot(dx, dy), 5.0, places=6)


class TestScreenHeading(unittest.TestCase):
    def test_subtracts_facing(self):
        from display.round_touch.geo import screen_heading

        self.assertAlmostEqual(screen_heading(90, 0), 90)
        self.assertAlmostEqual(screen_heading(90, 90), 0)
        self.assertAlmostEqual(screen_heading(0, 180), -180)


class TestFacingSettings(unittest.TestCase):
    def setUp(self):
        import display.round_touch.settings as settings

        self.settings = settings
        settings._facing_preview = None
        settings._state = dict(settings._defaults)
        settings._state["facing_deg"] = 0.0

    def tearDown(self):
        self.settings.set_facing_preview(None)

    def test_normalize_wraps(self):
        self.assertAlmostEqual(self.settings._normalize_facing(360), 0.0)
        self.assertAlmostEqual(self.settings._normalize_facing(-90), 270.0)
        self.assertAlmostEqual(self.settings._normalize_facing("bad"), 0.0)

    def test_facing_label(self):
        self.assertEqual(self.settings.facing_label(0), "N")
        self.assertEqual(self.settings.facing_label(90), "E")
        self.assertEqual(self.settings.facing_label(180), "S")
        self.assertEqual(self.settings.facing_label(270), "W")
        self.assertEqual(self.settings.facing_label(123), "123°")
        self.assertEqual(self.settings.facing_label(3), "3°")

    def test_set_facing_persists(self):
        path = self.settings.SETTINGS_PATH
        self.settings.set_facing_deg(180)
        self.assertAlmostEqual(self.settings.facing_deg(), 180.0)
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertAlmostEqual(float(data["facing_deg"]), 180.0)

    def test_preview_overrides_effective(self):
        self.settings.set_facing_deg(0)
        self.settings.set_facing_preview(90)
        self.assertAlmostEqual(self.settings.effective_facing_deg(), 90.0)
        self.settings.set_facing_preview(None)
        self.assertAlmostEqual(self.settings.effective_facing_deg(), 0.0)


class TestLatLonFacing(unittest.TestCase):
    def setUp(self):
        import display.round_touch.settings as settings
        import display.round_touch.theme as theme
        from display.round_touch import scale

        self.settings = settings
        settings._facing_preview = None
        settings._state = dict(settings._defaults)
        settings._state["facing_deg"] = 0.0
        # Stable geometry for assertions
        theme.set_framebuffer_side(720)
        # Pin a deterministic range band. screen projection quantizes to integer
        # pixels (enu_to_screen), so the lat/lon roundtrip precision is
        # scale-dependent; don't ride on the module default band.
        self._saved_units = scale.active_units()
        self._saved_index = scale.active_index()
        scale.set_units("mi")
        scale.select(1)

    def tearDown(self):
        self.settings.set_facing_preview(None)
        from display.round_touch import scale

        scale.set_units(self._saved_units)
        scale.select(self._saved_index)

    def test_north_of_home_is_above_center_when_north_up(self):
        from display.round_touch import geo

        with patch("display.round_touch.geo.LOCATION_HOME", [51.5, -0.1]):
            self.settings.set_facing_deg(0)
            x, y = geo.lat_lon_to_screen(51.5 + 0.05, -0.1)
            self.assertEqual(x, geo.theme.CENTER_X)
            self.assertLess(y, geo.theme.CENTER_Y)

    def test_north_of_home_is_below_center_when_south_up(self):
        from display.round_touch import geo

        with patch("display.round_touch.geo.LOCATION_HOME", [51.5, -0.1]):
            self.settings.set_facing_deg(180)
            x, y = geo.lat_lon_to_screen(51.5 + 0.05, -0.1)
            self.assertEqual(x, geo.theme.CENTER_X)
            self.assertGreater(y, geo.theme.CENTER_Y)

    def test_screen_to_lat_lon_roundtrip(self):
        from display.round_touch import geo

        with patch("display.round_touch.geo.LOCATION_HOME", [51.5, -0.1]):
            self.settings.set_facing_deg(0)
            target = (51.52, -0.08)
            x, y = geo.lat_lon_to_screen(*target)
            lat, lon = geo.screen_to_lat_lon(x, y)
            self.assertAlmostEqual(lat, target[0], places=4)
            self.assertAlmostEqual(lon, target[1], places=4)


if __name__ == "__main__":
    unittest.main()
