"""gpsd JSON-socket client: parse TPV/SKY reports and stream normalized GpsReports.

We speak gpsd's line protocol directly over a socket (no python3-gps dependency):
connect, send `?WATCH={"enable":true,"json":true}`, read newline-delimited JSON.
"""

from __future__ import annotations

import dataclasses
import json
import logging

from utilities.gps_resolver import GpsReport

logger = logging.getLogger(__name__)

_MODE_TO_FIX = {0: "none", 1: "none", 2: "2d", 3: "3d"}


def apply_gpsd_object(obj: dict, current: GpsReport) -> GpsReport:
    """Fold one gpsd JSON object into a new GpsReport (TPV=position, SKY=quality)."""
    cls = obj.get("class")
    if cls == "TPV":
        updated = dataclasses.replace(
            current,
            fix=_MODE_TO_FIX.get(int(obj.get("mode", 0)), "none"),
        )
        if "lat" in obj and "lon" in obj:
            updated.lat = float(obj["lat"])
            updated.lon = float(obj["lon"])
        if updated.fix == "none":
            # Position invalid without a fix; keep last coords for display but flag none.
            pass
        if "speed" in obj:
            try:
                updated.speed_mps = float(obj["speed"])
            except (TypeError, ValueError):
                updated.speed_mps = None
        return updated
    if cls == "SKY":
        sats = obj.get("satellites") or []
        used = sum(1 for s in sats if s.get("used"))
        hdop = obj.get("hdop")
        try:
            hdop = float(hdop) if hdop is not None else current.hdop
        except (TypeError, ValueError):
            hdop = current.hdop
        return dataclasses.replace(current, hdop=hdop, sats_used=used)
    return current
