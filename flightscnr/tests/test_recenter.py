# flightscnr/tests/test_recenter.py
"""_recenter consolidates all invalidation. Verify each subsystem is invoked."""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestRecenter(unittest.TestCase):
    def test_recenter_invalidates_all_subsystems(self):
        from display.round_touch.app import RoundTouchDisplay

        disp = object.__new__(RoundTouchDisplay)

        smoother = mock.Mock()
        overhead = mock.Mock()
        disp._position_smoother = smoother
        disp.overhead = overhead
        disp._weather_redraw_pending = False

        with mock.patch("display.round_touch.app.map_bg") as m_map, \
             mock.patch("display.round_touch.app.rainviewer_overlay") as m_rv, \
             mock.patch("display.round_touch.app.sync_ais_client") as m_ais:
            disp._recenter(47.61, -122.33, source="gps")

        m_map.invalidate.assert_called_once()
        m_map.prewarm_all_scales.assert_called_once()
        m_rv.invalidate.assert_called_once()
        m_rv.request_overlay.assert_called_once()
        m_ais.assert_called_once()
        smoother.reset.assert_called_once()
        overhead.grab_data.assert_called_once()
