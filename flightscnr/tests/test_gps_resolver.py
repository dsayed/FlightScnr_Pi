"""Unit tests for the pure GPS location-resolution logic (no gpsd, no threads)."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestHaversineMeters(unittest.TestCase):
    def test_zero_distance(self):
        from utilities.gps_resolver import haversine_m

        self.assertAlmostEqual(haversine_m(47.6, -122.3, 47.6, -122.3), 0.0, places=3)

    def test_known_short_distance(self):
        from utilities.gps_resolver import haversine_m

        # ~0.001 deg latitude ≈ 111.2 m
        d = haversine_m(47.6000, -122.3000, 47.6010, -122.3000)
        self.assertTrue(110.0 < d < 113.0, f"expected ~111 m, got {d}")


class TestGpsReport(unittest.TestCase):
    def test_no_fix_report(self):
        from utilities.gps_resolver import GpsReport

        r = GpsReport(fix="none", lat=None, lon=None, sats_used=0, hdop=None, speed_mps=None)
        self.assertFalse(r.has_fix())

    def test_3d_fix_report(self):
        from utilities.gps_resolver import GpsReport

        r = GpsReport(fix="3d", lat=47.6, lon=-122.3, sats_used=7, hdop=1.2, speed_mps=0.1)
        self.assertTrue(r.has_fix())
