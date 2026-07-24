"""Regression test for the on-device range-row crash.

_apply_display_row had a local `from display.round_touch import rainviewer_overlay`
in the precipitation branch, which made `rainviewer_overlay` a function-local
name for the WHOLE method. The earlier "range" branch then hit an
UnboundLocalError when tapped. This test drives the range branch and asserts it
does not raise.
"""

import unittest
from unittest.mock import patch


class TestRangeActionNoCrash(unittest.TestCase):
    def test_range_row_tap_does_not_raise(self):
        from display.round_touch import app as app_module

        inst = app_module.RoundTouchDisplay.__new__(app_module.RoundTouchDisplay)  # skip heavy __init__

        with patch.object(app_module.info, "display_action_at", return_value="range"), \
             patch.object(app_module.settings, "cycle_scale"), \
             patch.object(app_module.settings, "scale_index", return_value=2), \
             patch.object(app_module.scale, "select"), \
             patch.object(app_module.map_bg, "request_background"), \
             patch.object(app_module.rainviewer_overlay, "request_overlay") as m_overlay:
            # Before the fix this raises UnboundLocalError on rainviewer_overlay.
            inst._apply_display_row(1, 6)

        m_overlay.assert_called_once()

    def test_precipitation_row_still_works(self):
        # The branch that owned the shadowing import must keep functioning.
        from display.round_touch import app as app_module

        inst = app_module.RoundTouchDisplay.__new__(app_module.RoundTouchDisplay)

        with patch.object(app_module.info, "display_action_at", return_value="precipitation"), \
             patch.object(app_module.settings, "toggle_show_precipitation"), \
             patch.object(app_module.rainviewer_overlay, "invalidate") as m_inv, \
             patch.object(app_module.rainviewer_overlay, "request_overlay") as m_req:
            inst._apply_display_row(1, 5)

        m_inv.assert_called_once()
        m_req.assert_called_once()


if __name__ == "__main__":
    unittest.main()
