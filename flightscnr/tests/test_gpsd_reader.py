"""Unit tests for parsing gpsd JSON reports (no socket)."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestApplyGpsdObject(unittest.TestCase):
    def _empty(self):
        from utilities.gps_resolver import GpsReport

        return GpsReport()

    def test_tpv_3d_sets_position_and_fix(self):
        from utilities.gpsd_reader import apply_gpsd_object

        obj = {"class": "TPV", "mode": 3, "lat": 47.5899, "lon": -122.1186, "speed": 0.08}
        r = apply_gpsd_object(obj, self._empty())
        self.assertEqual(r.fix, "3d")
        self.assertAlmostEqual(r.lat, 47.5899)
        self.assertAlmostEqual(r.lon, -122.1186)
        self.assertAlmostEqual(r.speed_mps, 0.08)

    def test_tpv_mode_1_is_no_fix(self):
        from utilities.gpsd_reader import apply_gpsd_object

        r = apply_gpsd_object({"class": "TPV", "mode": 1}, self._empty())
        self.assertEqual(r.fix, "none")

    def test_sky_sets_hdop_and_used_satellites(self):
        from utilities.gpsd_reader import apply_gpsd_object

        obj = {
            "class": "SKY",
            "hdop": 2.36,
            "satellites": [
                {"PRN": 4, "used": True},
                {"PRN": 7, "used": True},
                {"PRN": 9, "used": False},
            ],
        }
        r = apply_gpsd_object(obj, self._empty())
        self.assertAlmostEqual(r.hdop, 2.36)
        self.assertEqual(r.sats_used, 2)

    def test_unrelated_class_returns_current_unchanged(self):
        from utilities.gpsd_reader import apply_gpsd_object

        cur = self._empty()
        r = apply_gpsd_object({"class": "VERSION", "release": "3.25"}, cur)
        self.assertEqual(r.fix, "none")

    def test_tpv_does_not_clobber_prior_sky_fields(self):
        from utilities.gpsd_reader import apply_gpsd_object

        after_sky = apply_gpsd_object(
            {"class": "SKY", "hdop": 1.5, "satellites": [{"PRN": 1, "used": True}]},
            self._empty(),
        )
        after_tpv = apply_gpsd_object(
            {"class": "TPV", "mode": 3, "lat": 47.6, "lon": -122.3}, after_sky
        )
        self.assertAlmostEqual(after_tpv.hdop, 1.5)
        self.assertEqual(after_tpv.sats_used, 1)
        self.assertEqual(after_tpv.fix, "3d")

    def test_no_fix_tpv_with_coords_keeps_prior_position(self):
        from utilities.gpsd_reader import apply_gpsd_object
        from utilities.gps_resolver import GpsReport

        prior = GpsReport(fix="3d", lat=47.6, lon=-122.3, sats_used=7, hdop=1.0, speed_mps=0.0)
        # A degraded TPV dropping to mode 1 with stale coords must NOT overwrite the fix.
        r = apply_gpsd_object({"class": "TPV", "mode": 1, "lat": 0.0, "lon": 0.0}, prior)
        self.assertEqual(r.fix, "none")
        self.assertAlmostEqual(r.lat, 47.6)
        self.assertAlmostEqual(r.lon, -122.3)
