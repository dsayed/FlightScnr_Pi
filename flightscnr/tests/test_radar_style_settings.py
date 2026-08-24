"""Unit tests for persisted radar display style settings."""

from __future__ import annotations

import importlib
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _fresh_settings_module(data_dir: str, initial: dict | None = None):
    os.makedirs(data_dir, exist_ok=True)
    settings_path = os.path.join(data_dir, "round_touch_settings.json")
    if initial is not None:
        with open(settings_path, "w", encoding="utf-8") as fh:
            json.dump(initial, fh)
    sys.modules.pop("display.round_touch.settings", None)
    return importlib.import_module("display.round_touch.settings")


class TestRadarStyleSettings(unittest.TestCase):
    def setUp(self):
        self._old_data_dir = os.environ.get("FLIGHTSCNR_DATA_DIR")
        self._tmp = tempfile.mkdtemp()
        os.environ["FLIGHTSCNR_DATA_DIR"] = self._tmp

    def tearDown(self):
        sys.modules.pop("display.round_touch.settings", None)
        if self._old_data_dir is None:
            os.environ.pop("FLIGHTSCNR_DATA_DIR", None)
        else:
            os.environ["FLIGHTSCNR_DATA_DIR"] = self._old_data_dir
        shutil.rmtree(self._tmp)

    def test_default_preserves_classic_radar(self):
        settings = _fresh_settings_module(self._tmp)

        self.assertEqual(settings.radar_style(), "classic")
        with open(settings.SETTINGS_PATH, encoding="utf-8") as fh:
            persisted = json.load(fh)
        self.assertEqual(persisted["radar_style"], "classic")

    def test_migrates_invalid_style_to_classic(self):
        settings = _fresh_settings_module(self._tmp, {"radar_style": "roundel"})

        self.assertEqual(settings.radar_style(), "classic")
        with open(settings.SETTINGS_PATH, encoding="utf-8") as fh:
            persisted = json.load(fh)
        self.assertEqual(persisted["radar_style"], "classic")

    def test_set_and_cycle_radar_style(self):
        settings = _fresh_settings_module(self._tmp)

        self.assertEqual(settings.set_radar_style("stars"), "stars")
        self.assertEqual(settings.radar_style(), "stars")
        self.assertEqual(settings.cycle_radar_style(), "classic")


if __name__ == "__main__":
    unittest.main()
