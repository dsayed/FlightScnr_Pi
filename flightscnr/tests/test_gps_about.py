"""About-screen GPS line rendering (pure)."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestGpsAboutLines(unittest.TestCase):
    def test_none_status_yields_no_lines(self):
        from display.round_touch.gps_about import status_lines

        self.assertEqual(status_lines(None), [])

    def test_3d_fix_lines(self):
        from display.round_touch.gps_about import status_lines

        lines = status_lines({
            "fix": "3d", "sats_used": 7, "hdop": 1.2, "device": "present",
            "source": "gps", "mode": "auto",
            "divergence": {"active": False},
        })
        self.assertTrue(any("Source: GPS" in ln for ln in lines))
        self.assertTrue(any("GPS: 3D" in ln and "7 sats" in ln for ln in lines))

    def test_divergence_notice_line(self):
        from display.round_touch.gps_about import status_lines

        lines = status_lines({
            "fix": "none", "sats_used": 0, "hdop": None, "device": "present",
            "source": "manual", "mode": "auto",
            "divergence": {"active": True, "ip_city": "Philadelphia", "km": 3800.0},
        })
        self.assertTrue(any("Philadelphia" in ln for ln in lines))

    def test_no_device_line(self):
        from display.round_touch.gps_about import status_lines

        lines = status_lines({
            "fix": "none", "sats_used": 0, "hdop": None, "device": "absent",
            "source": "configured", "mode": "auto", "divergence": {"active": False},
        })
        self.assertTrue(any("no device" in ln.lower() for ln in lines))
