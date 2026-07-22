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

    def test_sky_uses_usat_count_when_no_satellite_array(self):
        from utilities.gpsd_reader import apply_gpsd_object

        # Terse gpsd SKY: DOP values + uSat, no per-satellite array.
        r = apply_gpsd_object({"class": "SKY", "hdop": 2.5, "uSat": 7}, self._empty())
        self.assertEqual(r.sats_used, 7)
        self.assertAlmostEqual(r.hdop, 2.5)

    def test_sky_dop_only_keeps_prior_sats_count(self):
        from utilities.gpsd_reader import apply_gpsd_object
        from utilities.gps_resolver import GpsReport

        prior = GpsReport(fix="3d", lat=47.6, lon=-122.3, sats_used=8, hdop=1.1)
        # A DOP-only SKY (no uSat, no satellites) must NOT reset the used count.
        r = apply_gpsd_object({"class": "SKY", "hdop": 2.0}, prior)
        self.assertEqual(r.sats_used, 8)


class TestStreamReports(unittest.TestCase):
    def test_reads_reports_from_a_fake_gpsd(self):
        import socket
        import threading
        import time

        from utilities.gpsd_reader import stream_reports

        # A minimal fake gpsd: accept one client, expect the WATCH line, emit
        # a SKY then a TPV, then close.
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        def serve():
            conn, _ = srv.accept()
            conn.recv(1024)  # consume ?WATCH
            conn.sendall(b'{"class":"SKY","hdop":1.5,"satellites":[{"PRN":1,"used":true}]}\n')
            conn.sendall(b'{"class":"TPV","mode":3,"lat":47.6,"lon":-122.3}\n')
            time.sleep(0.2)
            conn.close()

        threading.Thread(target=serve, daemon=True).start()

        reports = []
        states = []
        stop = threading.Event()

        def on_report(r):
            reports.append(r)
            if r.has_fix():
                stop.set()  # got what we need

        def on_state(s):
            states.append(s)

        stream_reports("127.0.0.1", port, stop, on_report, on_state, connect_timeout=2.0)

        self.assertIn("present", states)
        self.assertTrue(any(r.fix == "3d" for r in reports))
        got = [r for r in reports if r.fix == "3d"][-1]
        self.assertAlmostEqual(got.lat, 47.6)
        self.assertEqual(got.sats_used, 1)  # SKY applied before TPV
        srv.close()
