"""Unit tests for STARS-style radar helper logic."""

from __future__ import annotations

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestStarsFormatting(unittest.TestCase):
    def test_data_block_formats_callsign_altitude_climb_and_speed(self):
        from display.round_touch import stars

        lines = stars.format_data_block(
            {
                "callsign": " ual123 ",
                "altitude": 12300,
                "vertical_speed": 450,
                "ground_speed": 457,
            },
            now=1_000.0,
        )

        self.assertEqual(lines, ["UAL123", "123^ 46"])

    def test_data_block_uses_fallbacks_and_coast(self):
        from display.round_touch import stars

        lines = stars.format_data_block(
            {
                "icao_hex": "ABC123",
                "altitude": None,
                "vertical_speed": -300,
                "ground_speed": None,
                "last_seen_ts": 100.0,
            },
            now=120.0,
        )

        self.assertEqual(lines, ["ABC123", "---v --", "CST"])

    def test_symbol_kind_distinguishes_ground_heading_and_dot(self):
        from display.round_touch import stars

        self.assertEqual(stars.symbol_kind({"altitude": 0, "heading": 270}), "ground")
        self.assertEqual(stars.symbol_kind({"altitude": 1200, "heading": 0}), "triangle")
        self.assertEqual(stars.symbol_kind({"altitude": 1200}), "dot")


class TestStarsVector(unittest.TestCase):
    def test_project_vector_uses_one_minute_ground_track(self):
        from display.round_touch import stars

        lat, lon = stars.project_vector_lat_lon(
            {
                "plane_latitude": 37.0,
                "plane_longitude": -122.0,
                "altitude": 1200,
                "heading": 90.0,
                "ground_speed": 600.0,
            }
        )

        self.assertAlmostEqual(lat, 37.0, places=5)
        expected_dlon = (600.0 * 1.852 / 60.0) / (111.320 * math.cos(math.radians(37.0)))
        self.assertAlmostEqual(lon + 122.0, expected_dlon, places=4)

    def test_project_vector_omits_ground_tracks(self):
        from display.round_touch import stars

        self.assertIsNone(
            stars.project_vector_lat_lon(
                {
                    "plane_latitude": 37.0,
                    "plane_longitude": -122.0,
                    "altitude": 0,
                    "heading": 90.0,
                    "ground_speed": 20.0,
                }
            )
        )


class TestStarsPlacement(unittest.TestCase):
    def test_placement_uses_first_clear_cardinal_position(self):
        from display.round_touch import stars

        symbol = (100, 100)
        size = (30, 20)
        bounds = (0, 0, 200, 200)
        occupied = [(118, 62, 30, 20)]

        self.assertEqual(
            stars.place_data_block(symbol, size, occupied, bounds, gap=18, pad=2),
            (52, 62, 30, 20),
        )

    def test_leader_endpoint_clamps_to_block_edge(self):
        from display.round_touch import stars

        self.assertEqual(stars.leader_endpoint((100, 100), (120, 62, 30, 20)), (120, 82))


class TestStarsHistory(unittest.TestCase):
    def test_history_samples_positions_and_caps_trail(self):
        from display.round_touch import stars

        history = stars.TrackHistory(max_points=3, min_distance_px=2)
        self.assertEqual(history.update("hex:ABC", (0, 0)), [])
        self.assertEqual(history.update("hex:ABC", (1, 0)), [])
        self.assertEqual(history.update("hex:ABC", (2, 0)), [(0, 0)])
        history.update("hex:ABC", (4, 0))
        history.update("hex:ABC", (6, 0))
        self.assertEqual(history.update("hex:ABC", (8, 0)), [(2, 0), (4, 0), (6, 0)])


if __name__ == "__main__":
    unittest.main()
