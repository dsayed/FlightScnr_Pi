import unittest

from display.round_touch import scale


class TestScaleSnapping(unittest.TestCase):
    def test_index_for_value_mi(self):
        self.assertEqual(scale.index_for_value(30, "mi"), 6)
        self.assertEqual(scale.index_for_value(4, "mi"), 1)
        self.assertEqual(scale.index_for_value(7, "mi"), 3)

    def test_format_display_value_mi(self):
        self.assertEqual(scale.format_display_value(1, "mi"), "3")

    def test_index_for_value_km(self):
        self.assertEqual(scale.index_for_value(48, "km"), 6)


class TestUnitAwareBands(unittest.TestCase):
    def setUp(self):
        # Tests mutate module state; restore afterwards.
        self._saved_units = scale.active_units()
        self._saved_index = scale.active_index()

    def tearDown(self):
        scale.set_units(self._saved_units)
        scale.select(self._saved_index)

    def test_all_preset_tuples_same_length(self):
        lengths = {len(v) for v in scale.PRESETS.values()}
        self.assertEqual(len(lengths), 1)

    def test_nm_bands_are_round_nautical_miles(self):
        # Every nm band index must display back as its round nm preset.
        for i, preset in enumerate(scale.PRESETS["nm"]):
            self.assertEqual(scale.format_display_value(i, "nm"), str(preset))

    def test_km_bands_are_round_km(self):
        for i, preset in enumerate(scale.PRESETS["km"]):
            self.assertEqual(scale.format_display_value(i, "km"), str(preset))

    def test_set_units_swaps_active_band_physical_size(self):
        scale.select(4)  # index 4
        scale.set_units("nm")
        nm_km = scale.active_band()["label_km"]
        scale.set_units("mi")
        mi_km = scale.active_band()["label_km"]
        # index preserved, but a nm band is physically larger than the mi band
        # at the same index (1 nm = 1.852 km > 1 mi = 1.609 km).
        self.assertGreater(nm_km, mi_km)

    def test_index_preserved_across_unit_switch(self):
        scale.select(5)
        scale.set_units("nm")
        self.assertEqual(scale.active_index(), 5)
        scale.set_units("km")
        self.assertEqual(scale.active_index(), 5)

    def test_index_for_value_nm_round_trip(self):
        # A round nm value snaps to the band that displays as that value.
        for preset in scale.PRESETS["nm"]:
            idx = scale.index_for_value(preset, "nm")
            self.assertEqual(scale.format_display_value(idx, "nm"), str(preset))

    def test_format_active_tag_nm_is_round(self):
        scale.set_units("nm")
        scale.select(4)  # 10 nm
        self.assertEqual(scale.format_active_tag("nm"), "10nm")

    def test_search_radius_scales_with_unit(self):
        scale.select(4)
        scale.set_units("nm")
        r_nm = scale.search_radius_nm()
        scale.set_units("mi")
        r_mi = scale.search_radius_nm()
        # Same index, nm physically larger, so fetch radius (nm) is larger.
        self.assertGreater(r_nm, r_mi)

    def test_presets_for_units(self):
        self.assertEqual(scale.presets_for("nm"), (2, 3, 5, 8, 10, 20, 30))
        self.assertEqual(scale.presets_for("km"), (2, 5, 8, 15, 20, 30, 50))


if __name__ == "__main__":
    unittest.main()
