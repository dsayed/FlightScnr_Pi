"""Config: GPS env vars and extended location.json persistence."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestGpsConfig(unittest.TestCase):
    def test_defaults_present(self):
        import config

        self.assertEqual(config.LOCATION_MODE, "auto")
        self.assertTrue(hasattr(config, "GPS_ENABLED"))
        self.assertEqual(config.GPSD_PORT, 2947)
        self.assertEqual(config.GEOIP_URL, "https://ipwho.is/")

    def test_set_location_home_writes_source_and_mode(self):
        import config

        d = tempfile.mkdtemp()
        old = config.LOCATION_FILE
        try:
            config.LOCATION_FILE = os.path.join(d, "location.json")
            config.set_location_home(47.61, -122.33, source="gps")
            with open(config.LOCATION_FILE) as fh:
                data = json.load(fh)
            self.assertEqual(data["source"], "gps")
            self.assertIn("mode", data)
            self.assertIn("updated_at", data)
            self.assertAlmostEqual(data["lat"], 47.61)
        finally:
            config.LOCATION_FILE = old

    def test_backward_compatible_read_of_bare_lat_lon(self):
        import config

        d = tempfile.mkdtemp()
        old = config.LOCATION_FILE
        old_mtime = config._location_file_mtime
        try:
            config.LOCATION_FILE = os.path.join(d, "location.json")
            with open(config.LOCATION_FILE, "w") as fh:
                json.dump({"lat": 40.0, "lon": -75.0}, fh)  # legacy shape
            config._location_file_mtime = None
            changed = config.reload_location_override()
            self.assertTrue(changed)
            self.assertAlmostEqual(config.LOCATION_HOME[0], 40.0)
        finally:
            config.LOCATION_FILE = old
            config._location_file_mtime = old_mtime
