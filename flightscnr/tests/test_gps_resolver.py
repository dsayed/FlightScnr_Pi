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


def _cfg():
    from utilities.gps_resolver import ResolverConfig

    return ResolverConfig(
        min_sats=4, max_hdop=5.0, rehome_min_m=250.0,
        settle_s=25.0, nofix_grace_s=180.0, divergence_km=25.0,
    )


def _fix(lat, lon, sats=7, hdop=1.2):
    from utilities.gps_resolver import GpsReport

    return GpsReport(fix="3d", lat=lat, lon=lon, sats_used=sats, hdop=hdop, speed_mps=0.0)


class TestResolverRehome(unittest.TestCase):
    def test_settled_move_beyond_threshold_rehomes(self):
        from utilities.gps_resolver import LocationResolver

        res = LocationResolver(_cfg(), geoip_lookup=lambda: None)
        home = (47.6000, -122.3000)
        far = (47.6100, -122.3000)  # ~1.1 km away
        # first sighting starts the settle timer, no action yet
        d0 = res.observe(_fix(*far), now=0.0, home=home, mode="auto", has_saved_home=True)
        self.assertEqual(d0.action, "none")
        # still settling before settle_s elapses
        d1 = res.observe(_fix(*far), now=10.0, home=home, mode="auto", has_saved_home=True)
        self.assertEqual(d1.action, "none")
        # after settle window, commit re-home
        d2 = res.observe(_fix(*far), now=30.0, home=home, mode="auto", has_saved_home=True)
        self.assertEqual(d2.action, "rehome")
        self.assertAlmostEqual(d2.lat, far[0])
        self.assertEqual(d2.source, "gps")

    def test_small_move_does_not_rehome(self):
        from utilities.gps_resolver import LocationResolver

        res = LocationResolver(_cfg(), geoip_lookup=lambda: None)
        home = (47.6000, -122.3000)
        near = (47.6001, -122.3000)  # ~11 m
        for t in (0.0, 10.0, 30.0, 60.0):
            d = res.observe(_fix(*near), now=t, home=home, mode="auto", has_saved_home=True)
            self.assertEqual(d.action, "none")

    def test_stray_during_settle_restarts_timer(self):
        from utilities.gps_resolver import LocationResolver

        res = LocationResolver(_cfg(), geoip_lookup=lambda: None)
        home = (47.6000, -122.3000)
        far_a = (47.6100, -122.3000)
        far_b = (47.6300, -122.3000)  # >250 m from far_a, resets cluster
        res.observe(_fix(*far_a), now=0.0, home=home, mode="auto", has_saved_home=True)
        res.observe(_fix(*far_b), now=10.0, home=home, mode="auto", has_saved_home=True)
        # only 20 s on the far_b cluster — not settled yet
        d = res.observe(_fix(*far_b), now=30.0, home=home, mode="auto", has_saved_home=True)
        self.assertEqual(d.action, "none")


class TestResolverRejectsBadFixes(unittest.TestCase):
    def test_high_hdop_fix_ignored(self):
        from utilities.gps_resolver import LocationResolver

        res = LocationResolver(_cfg(), geoip_lookup=lambda: None)
        home = (47.6000, -122.3000)
        bad = _fix(47.6100, -122.3000, hdop=9.9)
        for t in (0.0, 30.0):
            d = res.observe(bad, now=t, home=home, mode="auto", has_saved_home=True)
            self.assertEqual(d.action, "none")

    def test_too_few_sats_ignored(self):
        from utilities.gps_resolver import LocationResolver

        res = LocationResolver(_cfg(), geoip_lookup=lambda: None)
        home = (47.6000, -122.3000)
        bad = _fix(47.6100, -122.3000, sats=2)
        d = res.observe(bad, now=30.0, home=home, mode="auto", has_saved_home=True)
        self.assertEqual(d.action, "none")


class TestResolverNoFix(unittest.TestCase):
    def test_nofix_within_grace_keeps_quiet(self):
        from utilities.gps_resolver import GpsReport, LocationResolver

        res = LocationResolver(_cfg(), geoip_lookup=lambda: (99.0, 99.0, "Nowhere"))
        home = (47.6000, -122.3000)
        d = res.observe(GpsReport(fix="none"), now=60.0, home=home, mode="auto", has_saved_home=True)
        self.assertEqual(d.action, "none")

    def test_persistent_nofix_far_ip_raises_divergence(self):
        from utilities.gps_resolver import GpsReport, LocationResolver

        # IP far (>25 km) from home → diverge
        res = LocationResolver(_cfg(), geoip_lookup=lambda: (40.0, -75.0, "Philadelphia"))
        home = (47.6000, -122.3000)
        res.observe(GpsReport(fix="none"), now=0.0, home=home, mode="auto", has_saved_home=True)
        d = res.observe(GpsReport(fix="none"), now=200.0, home=home, mode="auto", has_saved_home=True)
        self.assertEqual(d.action, "diverge")
        self.assertEqual(d.source, None)  # notice only; no location change

    def test_persistent_nofix_near_ip_stays_quiet(self):
        from utilities.gps_resolver import GpsReport, LocationResolver

        res = LocationResolver(_cfg(), geoip_lookup=lambda: (47.61, -122.31, "Seattle"))
        home = (47.6000, -122.3000)
        res.observe(GpsReport(fix="none"), now=0.0, home=home, mode="auto", has_saved_home=True)
        d = res.observe(GpsReport(fix="none"), now=200.0, home=home, mode="auto", has_saved_home=True)
        self.assertEqual(d.action, "none")

    def test_fresh_unit_no_home_bootstraps_from_ip(self):
        from utilities.gps_resolver import GpsReport, LocationResolver

        res = LocationResolver(_cfg(), geoip_lookup=lambda: (47.61, -122.31, "Seattle"))
        d = res.observe(GpsReport(fix="none"), now=0.0, home=None, mode="auto", has_saved_home=False)
        self.assertEqual(d.action, "bootstrap")
        self.assertEqual(d.source, "estimated")
        self.assertAlmostEqual(d.lat, 47.61)


class TestResolverManualMode(unittest.TestCase):
    def test_manual_mode_never_acts(self):
        from utilities.gps_resolver import LocationResolver

        res = LocationResolver(_cfg(), geoip_lookup=lambda: (40.0, -75.0, "Philadelphia"))
        home = (47.6000, -122.3000)
        far = (47.6100, -122.3000)
        d = res.observe(_fix(*far), now=30.0, home=home, mode="manual", has_saved_home=True)
        self.assertEqual(d.action, "none")
