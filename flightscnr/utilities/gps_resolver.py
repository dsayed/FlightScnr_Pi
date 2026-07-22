"""Pure GPS location-resolution logic: distance math, report type, and the
Auto-mode resolution state machine. No sockets, threads, or file I/O — everything
is injected, so this module is fully unit-testable without hardware."""

from __future__ import annotations

import math
from dataclasses import dataclass, field


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in METERS (units-independent; do not use overhead.haversine)."""
    r = 6371008.8  # mean Earth radius, meters
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


@dataclass
class GpsReport:
    """Normalized snapshot of the receiver state, merged from gpsd TPV + SKY."""

    fix: str = "none"           # "none" | "2d" | "3d"
    lat: float | None = None
    lon: float | None = None
    sats_used: int = 0
    hdop: float | None = None
    speed_mps: float | None = None

    def has_fix(self) -> bool:
        return self.fix in ("2d", "3d") and self.lat is not None and self.lon is not None
