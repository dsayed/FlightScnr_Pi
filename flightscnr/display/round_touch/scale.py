"""Radar range scale bands (FlightScnr radar_scale.h).

Bands are unit-aware: each distance unit has its own set of round preset
values, so a nautical-mile user sees clean nm rings (2, 3, 5, 8, 10, 20, 30 nm)
rather than fractional conversions of statute-mile bands. All three preset
tuples are the SAME length, so the persisted ``scale_index`` (0..N-1) stays
valid across a unit switch — only the *physical* size of the index changes.

``set_units()`` is the single seam that swaps the active band table. Callers
that switch units must also invalidate any scale-index-keyed caches (map tiles,
rainviewer), because index i now maps to a different physical distance.
"""

STATUTE_MILE_KM = 1.609344
NM_KM = 1.852
LABEL_TO_COVERAGE = 4.0 / 3.0

# Round preset values per unit. MUST all be the same length: the persisted
# scale_index is an index into whichever tuple is active.
PRESETS = {
    "mi": (2, 3, 5, 8, 10, 20, 30),
    "nm": (2, 3, 5, 8, 10, 20, 30),
    "km": (2, 5, 8, 15, 20, 30, 50),
}
NUM_BANDS = len(PRESETS["mi"])
assert all(len(v) == NUM_BANDS for v in PRESETS.values()), "preset tuples must match length"

# Backward-compatible alias (statute-mile presets were the original source).
PRESET_STATUTE_MILES = PRESETS["mi"]

NM_PER_KM = 1.0 / 1.852

_UNIT_KM = {"mi": STATUTE_MILE_KM, "nm": NM_KM, "km": 1.0}


def _norm_units(units) -> str:
    u = (units or "km").lower()
    return u if u in PRESETS else "km"


def _bands_for(units: str) -> list:
    units = _norm_units(units)
    factor = _UNIT_KM[units]
    bands = []
    for value in PRESETS[units]:
        label_km = value * factor
        bands.append({"label_km": label_km, "coverage_km": label_km * LABEL_TO_COVERAGE})
    return bands


_active_index = 1
_active_units = "km"
# Rebuilt whenever set_units() runs; consumers read scale.SCALE_BANDS fresh.
SCALE_BANDS = _bands_for(_active_units)


def set_units(units: str) -> None:
    """Swap the active band table. Idempotent; safe to call every reload."""
    global _active_units, SCALE_BANDS
    _active_units = _norm_units(units)
    SCALE_BANDS = _bands_for(_active_units)


def active_units() -> str:
    return _active_units


def active_band():
    return SCALE_BANDS[_active_index]


def active_index():
    return _active_index


def cycle_next():
    """Advance to the next range band, wrapping to the smallest."""
    global _active_index
    _active_index = (_active_index + 1) % NUM_BANDS


def select(index: int):
    global _active_index
    _active_index = max(0, min(index, NUM_BANDS - 1))


def search_radius_nm(index: int | None = None) -> float:
    """Nautical-mile fetch radius for rim targets (coverage scaled to visible edge)."""
    if index is None:
        idx = active_index()
    else:
        idx = max(0, min(int(index), NUM_BANDS - 1))
    band = SCALE_BANDS[idx]
    try:
        from display.round_touch import theme

        screen_r = theme.VISIBLE_RADIUS - theme.BEYOND_RING_MARGIN
        fetch_km = band["coverage_km"] * (screen_r / theme.GRID_OUTER_RADIUS)
    except ImportError:
        fetch_km = band["coverage_km"]
    return fetch_km / 1.852


def format_scale_tag(label_km: float, units: str = "km") -> str:
    units = _norm_units(units)
    if units == "mi":
        miles = label_km / STATUTE_MILE_KM
        if abs(miles - round(miles)) < 0.05:
            return f"{int(round(miles))}mi"
        return f"{miles:.1f}mi"
    if units == "nm":
        nm = label_km * NM_PER_KM
        if abs(nm - round(nm)) < 0.05:
            return f"{int(round(nm))}nm"
        return f"{nm:.1f}nm"
    if label_km >= 10:
        return f"{int(round(label_km))}km"
    return f"{label_km:.1f}km"


def format_active_tag(units: str = "km") -> str:
    return format_scale_tag(active_band()["label_km"], units)


def format_band_tag(index: int, units: str = "km") -> str:
    idx = max(0, min(int(index), NUM_BANDS - 1))
    return format_scale_tag(_bands_for(units)[idx]["label_km"], units)


def value_to_km(value: float, units: str = "mi") -> float:
    units = _norm_units(units)
    return value * _UNIT_KM[units]


def index_for_value(value: float, units: str = "mi") -> int:
    """Snap to the nearest scale band for a distance in the given units."""
    units = _norm_units(units)
    target_km = value_to_km(float(value), units)
    bands = _bands_for(units)
    best_idx = 0
    best_diff = float("inf")
    for i, band in enumerate(bands):
        diff = abs(band["label_km"] - target_km)
        if diff < best_diff:
            best_diff = diff
            best_idx = i
    return best_idx


def display_value_for_index(index: int, units: str = "mi") -> float:
    """Numeric range for portal display in the given units."""
    idx = max(0, min(int(index), NUM_BANDS - 1))
    units = _norm_units(units)
    label_km = _bands_for(units)[idx]["label_km"]
    return label_km / _UNIT_KM[units]


def format_display_value(index: int, units: str = "mi") -> str:
    """Format range for the portal text box."""
    value = display_value_for_index(index, units)
    units = _norm_units(units)
    if units == "km" and value >= 10:
        return str(int(round(value)))
    if abs(value - round(value)) < 0.05:
        return str(int(round(value)))
    return f"{value:.1f}"


def presets_for(units: str = "mi") -> tuple:
    """Round preset values shown in the portal for the given units."""
    return PRESETS[_norm_units(units)]


def preset_labels(units: str = "mi") -> str:
    return ", ".join(str(v) for v in presets_for(units))


def preset_labels_mi() -> str:
    return preset_labels("mi")


def index_for_radius_nm(radius_nm: float) -> int:
    """Scale band index that fits the configured search radius (active units)."""
    radius_km = radius_nm * 1.852
    best = NUM_BANDS - 1
    for i, band in enumerate(SCALE_BANDS):
        if band["coverage_km"] >= radius_km:
            best = i
            break
    return best
