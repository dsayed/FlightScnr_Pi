"""GpsClient orchestration with an injected fake reader (no gpsd, no real socket)."""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestGpsClientDecisions(unittest.TestCase):
    def test_rehome_decision_writes_location_file(self):
        import config
        from utilities.gps_client import GpsClient
        from utilities.gps_resolver import Decision

        d = tempfile.mkdtemp()
        old = config.LOCATION_FILE
        try:
            config.LOCATION_FILE = os.path.join(d, "location.json")
            gc = GpsClient(data_dir=d)
            gc.apply_decision(Decision(action="rehome", lat=47.61, lon=-122.33, source="gps"))
            self.assertTrue(os.path.isfile(config.LOCATION_FILE))
            self.assertAlmostEqual(config.LOCATION_HOME[0], 47.61)
            self.assertEqual(config.location_source(), "gps")
        finally:
            config.LOCATION_FILE = old

    def test_status_file_written(self):
        from utilities.gps_client import GpsClient
        from utilities.gps_resolver import GpsReport

        d = tempfile.mkdtemp()
        gc = GpsClient(data_dir=d)
        gc._on_report(GpsReport(fix="3d", lat=47.6, lon=-122.3, sats_used=7, hdop=1.1, speed_mps=0.0))
        gc._flush_status(force=True)
        import json

        with open(os.path.join(d, "gps_status.json")) as fh:
            status = json.load(fh)
        self.assertEqual(status["fix"], "3d")
        self.assertEqual(status["sats_used"], 7)

    def test_injected_reader_drives_a_rehome(self):
        import config
        from utilities.gps_client import GpsClient
        from utilities.gps_resolver import GpsReport

        d = tempfile.mkdtemp()
        old = config.LOCATION_FILE
        old_mode = config._location_mode_runtime
        try:
            config.LOCATION_FILE = os.path.join(d, "location.json")
            config._location_mode_runtime = "auto"
            config.set_location_home(47.6000, -122.3000, source="configured")

            # Fake reader: emit accepted far fixes across an advancing clock, then stop.
            far = GpsReport(fix="3d", lat=47.6100, lon=-122.3000, sats_used=7, hdop=1.0, speed_mps=0.0)

            def fake_reader(host, port, stop_event, on_report, on_state, connect_timeout=5.0):
                on_state("present")
                for _ in range(5):
                    if stop_event.is_set():
                        break
                    on_report(far)

            clock = {"t": 0.0}

            def fake_now():
                clock["t"] += 10.0
                return clock["t"]

            gc = GpsClient(data_dir=d, reader=fake_reader, now=fake_now)
            gc.start()
            gc._thread.join(timeout=5.0)
            gc.stop()
            self.assertAlmostEqual(config.LOCATION_HOME[0], 47.6100, places=4)
        finally:
            config.LOCATION_FILE = old
            config._location_mode_runtime = old_mode
