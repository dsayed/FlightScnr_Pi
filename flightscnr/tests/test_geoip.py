"""Unit tests for IP geolocation (requests mocked, no network)."""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestGeoipLookup(unittest.TestCase):
    def test_parses_ipwhois_response(self):
        from utilities import geoip

        payload = {"success": True, "latitude": 47.61, "longitude": -122.33, "city": "Seattle"}
        fake = mock.Mock()
        fake.raise_for_status = mock.Mock()
        fake.json = mock.Mock(return_value=payload)
        with mock.patch("utilities.geoip.requests.get", return_value=fake):
            result = geoip.lookup("https://ipwho.is/")
        self.assertEqual(result, (47.61, -122.33, "Seattle"))

    def test_network_error_returns_none(self):
        import requests

        from utilities import geoip

        with mock.patch("utilities.geoip.requests.get", side_effect=requests.RequestException("boom")):
            self.assertIsNone(geoip.lookup("https://ipwho.is/"))

    def test_missing_fields_returns_none(self):
        from utilities import geoip

        fake = mock.Mock()
        fake.raise_for_status = mock.Mock()
        fake.json = mock.Mock(return_value={"success": False})
        with mock.patch("utilities.geoip.requests.get", return_value=fake):
            self.assertIsNone(geoip.lookup("https://ipwho.is/"))
