# flightscnr/tests/test_gps_portal.py
"""Portal location endpoints: mode switch, status, resolution actions."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestLocationPortal(unittest.TestCase):
    def _client(self, data_dir):
        import config

        config.LOCATION_FILE = os.path.join(data_dir, "location.json")
        from web import app as webapp

        webapp.app.config.update(TESTING=True)
        return webapp.app.test_client(), config

    def test_location_json_reports_mode(self):
        d = tempfile.mkdtemp()
        client, config = self._client(d)
        config._location_mode_runtime = "auto"
        config.set_location_home(47.6, -122.3, source="gps")
        resp = client.get("/location/json")
        body = resp.get_json()
        self.assertEqual(body["mode"], "auto")
        self.assertEqual(body["source"], "gps")

    def test_post_mode_switch_to_manual(self):
        d = tempfile.mkdtemp()
        client, config = self._client(d)
        config.set_location_home(47.6, -122.3, source="gps")
        resp = client.post("/location/set", json={"mode": "manual"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(config.location_mode(), "manual")

    def test_post_manual_location_sets_source_manual(self):
        d = tempfile.mkdtemp()
        client, config = self._client(d)
        resp = client.post("/location/set", json={"location": "47.65, -122.35"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(config.location_source(), "manual")
        self.assertEqual(config.location_mode(), "manual")
