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
        # source "gps" in auto mode = GPS actively set the location.
        self.assertTrue(any("GPS set location" in ln for ln in lines))
        self.assertTrue(any("GPS: 3D" in ln and "7 sats" in ln for ln in lines))

    def test_auto_mode_with_fix_but_manual_home_shows_gps_locked(self):
        # The reported confusion: Auto selected, live 3D fix, but home was set
        # manually and matches — must NOT read as "Manual"; GPS is clearly live.
        from display.round_touch.gps_about import status_lines

        lines = status_lines({
            "fix": "3d", "sats_used": 7, "hdop": 1.1, "device": "present",
            "source": "manual", "mode": "auto",
            "divergence": {"active": False},
        })
        self.assertTrue(any("Auto" in ln and "GPS locked" in ln for ln in lines))
        self.assertFalse(any(ln.strip() == "Source: Manual" for ln in lines))
        self.assertTrue(any("GPS: 3D" in ln and "7 sats" in ln for ln in lines))

    def test_auto_mode_acquiring_when_no_fix(self):
        from display.round_touch.gps_about import status_lines

        lines = status_lines({
            "fix": "none", "sats_used": 0, "hdop": None, "device": "present",
            "source": "manual", "mode": "auto",
            "divergence": {"active": False},
        })
        self.assertTrue(any("Auto" in ln and "acquiring" in ln for ln in lines))

    def test_manual_mode_shows_mode_manual(self):
        from display.round_touch.gps_about import status_lines

        lines = status_lines({
            "fix": "3d", "sats_used": 6, "hdop": 1.5, "device": "present",
            "source": "manual", "mode": "manual",
            "divergence": {"active": False},
        })
        self.assertTrue(any(ln == "Mode: Manual" for ln in lines))

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


if __name__ == "__main__":
    unittest.main()
