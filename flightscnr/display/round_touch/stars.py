# SPDX-License-Identifier: CC-BY-NC-SA-4.0
# Copyright (c) 2026 Yash Mulgaonkar - https://github.com/yashmulgaonkar/FlightScnr_Pi

"""Pure helpers for the optional STARS-style radar presentation."""

from __future__ import annotations

import math
import time
from typing import Iterable

from display.round_touch import position_smooth

RADAR_STYLES = ("classic", "stars")
VECTOR_SECONDS = 60.0
VERTICAL_ARROW_FPM = 100
COAST_AFTER_S = 8.0

RectTuple = tuple[int, int, int, int]


def normalize_radar_style(value) -> str:
    raw = str(value or "").strip().lower()
    return raw if raw in RADAR_STYLES else "classic"


def _as_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return f


def track_identity(flight: dict) -> str | None:
    hex_id = str(flight.get("icao_hex") or "").strip().upper()
    if hex_id:
        return f"hex:{hex_id}"
    callsign = (
        flight.get("callsign")
        or flight.get("flight_number")
        or flight.get("registration")
        or ""
    )
    callsign = "".join(str(callsign).upper().split())
    if callsign:
        return f"cs:{callsign}"
    squawk = _normalize_squawk(flight.get("squawk"))
    if squawk:
        return f"sq:{squawk}"
    return None


def _normalize_squawk(value) -> str:
    raw = "".join(ch for ch in str(value or "").strip() if ch.isdigit())
    if not raw:
        return ""
    return raw[-4:].zfill(4)


def _clean_label(value) -> str:
    return " ".join(str(value or "").strip().upper().split())


def data_block_callsign(flight: dict) -> str:
    for key in ("flight_number", "callsign", "registration"):
        label = _clean_label(flight.get(key))
        if label:
            return label
    squawk = _normalize_squawk(flight.get("squawk"))
    if squawk:
        return f"SQ{squawk}"
    hex_id = _clean_label(flight.get("icao_hex"))
    if hex_id:
        return hex_id[-6:]
    return "----"


def altitude_hundreds(altitude) -> str:
    alt = _as_float(altitude)
    if alt is None:
        return "---"
    return f"{max(0, int(round(alt / 100.0))):03d}"


def vertical_arrow(vertical_speed) -> str:
    vs = _as_float(vertical_speed)
    if vs is None:
        return " "
    if vs >= VERTICAL_ARROW_FPM:
        return "^"
    if vs <= -VERTICAL_ARROW_FPM:
        return "v"
    return " "


def speed_tens(speed_kt) -> str:
    speed = _as_float(speed_kt)
    if speed is None or speed < 0:
        return "--"
    return f"{int(round(speed / 10.0)):02d}"


def coast_label(flight: dict, *, now: float | None = None, stale_after_s: float = COAST_AFTER_S) -> str:
    """Return CST when a numeric feed timestamp shows the track has gone stale."""
    ts = _as_float(flight.get("last_seen_ts"))
    if ts is None or ts <= 0:
        return ""
    now = time.time() if now is None else float(now)
    if now - ts > stale_after_s:
        return "CST"
    return ""


def format_data_block(flight: dict, *, now: float | None = None) -> list[str]:
    """Return compact ATC-style data-block lines for an aircraft track."""
    line2 = f"{altitude_hundreds(flight.get('altitude'))}{vertical_arrow(flight.get('vertical_speed'))} {speed_tens(flight.get('ground_speed'))}"
    lines = [data_block_callsign(flight), line2]
    coast = coast_label(flight, now=now)
    if coast:
        lines.append(coast)
    return lines


def is_on_ground(flight: dict) -> bool:
    if bool(flight.get("on_ground") or flight.get("grounded")):
        return True
    alt = _as_float(flight.get("altitude"))
    return alt is not None and alt <= 0


def has_heading(flight: dict) -> bool:
    return _as_float(flight.get("heading")) is not None


def symbol_kind(flight: dict) -> str:
    if is_on_ground(flight):
        return "ground"
    if has_heading(flight):
        return "triangle"
    return "dot"


def project_vector_lat_lon(flight: dict, *, seconds: float = VECTOR_SECONDS) -> tuple[float, float] | None:
    if is_on_ground(flight):
        return None
    lat = _as_float(flight.get("plane_latitude"))
    lon = _as_float(flight.get("plane_longitude"))
    heading = _as_float(flight.get("heading"))
    speed = _as_float(flight.get("ground_speed"))
    if lat is None or lon is None or heading is None or speed is None or speed <= 0:
        return None
    return position_smooth.offset_lat_lon(lat, lon, heading, speed, seconds)


def rect_overlaps(a: RectTuple, b: RectTuple, *, pad: int = 0) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return not (
        ax + aw + pad <= bx
        or bx + bw + pad <= ax
        or ay + ah + pad <= by
        or by + bh + pad <= ay
    )


def _rect_within(rect: RectTuple, bounds: RectTuple) -> bool:
    x, y, w, h = rect
    bx, by, bw, bh = bounds
    return x >= bx and y >= by and x + w <= bx + bw and y + h <= by + bh


def _candidate_rect(
    symbol_xy: tuple[int, int],
    block_size: tuple[int, int],
    direction: tuple[int, int],
    gap: int,
) -> RectTuple:
    sx, sy = symbol_xy
    w, h = block_size
    dx, dy = direction
    if dx > 0:
        x = sx + gap
    elif dx < 0:
        x = sx - gap - w
    else:
        x = sx - w // 2
    if dy > 0:
        y = sy + gap
    elif dy < 0:
        y = sy - gap - h
    else:
        y = sy - h // 2
    return int(x), int(y), int(w), int(h)


def _clamp_rect(rect: RectTuple, bounds: RectTuple) -> RectTuple:
    x, y, w, h = rect
    bx, by, bw, bh = bounds
    return (
        max(bx, min(x, bx + bw - w)),
        max(by, min(y, by + bh - h)),
        w,
        h,
    )


def place_data_block(
    symbol_xy: tuple[int, int],
    block_size: tuple[int, int],
    occupied: Iterable[RectTuple],
    bounds: RectTuple,
    *,
    gap: int = 18,
    pad: int = 4,
) -> RectTuple:
    """Nearest-first label placement: NE/NW/SE/SW/E/W/N/S around the symbol."""
    occupied = list(occupied)
    for direction in ((1, -1), (-1, -1), (1, 1), (-1, 1), (1, 0), (-1, 0), (0, -1), (0, 1)):
        rect = _candidate_rect(symbol_xy, block_size, direction, gap)
        if not _rect_within(rect, bounds):
            continue
        if any(rect_overlaps(rect, other, pad=pad) for other in occupied):
            continue
        return rect
    return _clamp_rect(_candidate_rect(symbol_xy, block_size, (1, -1), gap), bounds)


def leader_endpoint(symbol_xy: tuple[int, int], rect: RectTuple) -> tuple[int, int]:
    sx, sy = symbol_xy
    x, y, w, h = rect
    return (
        max(x, min(sx, x + w)),
        max(y, min(sy, y + h)),
    )


class TrackHistory:
    """Small sampled trail of recent drawn screen positions per aircraft."""

    def __init__(self, *, max_points: int = 5, min_distance_px: float = 4.0) -> None:
        self.max_points = max(1, int(max_points))
        self.min_distance_px = float(min_distance_px)
        self._points: dict[str, list[tuple[int, int]]] = {}

    def reset(self) -> None:
        self._points.clear()

    def prune(self, active_identities: set[str]) -> None:
        for identity in list(self._points):
            if identity not in active_identities:
                del self._points[identity]

    def update(self, identity: str | None, point: tuple[int, int]) -> list[tuple[int, int]]:
        if identity is None:
            return []
        x, y = int(point[0]), int(point[1])
        points = self._points.setdefault(identity, [])
        if not points:
            points.append((x, y))
            return []
        lx, ly = points[-1]
        if math.hypot(x - lx, y - ly) >= self.min_distance_px:
            points.append((x, y))
            del points[: max(0, len(points) - (self.max_points + 1))]
        return list(points[:-1])[-self.max_points :]
