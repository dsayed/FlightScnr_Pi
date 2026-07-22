"""Pure helper: turn a gps_status.json dict into About-screen text lines."""

from __future__ import annotations

_SOURCE_LABEL = {"gps": "GPS", "manual": "Manual", "estimated": "Estimated", "configured": "Configured", "portal": "Manual"}
_FIX_LABEL = {"3d": "3D", "2d": "2D", "none": "no fix"}


def status_lines(status) -> list[str]:
    if not status:
        return []
    lines = [f"Source: {_SOURCE_LABEL.get(status.get('source', ''), status.get('source', '?'))}"]
    device = status.get("device", "present")
    if device == "absent":
        lines.append("GPS: no device")
    elif device == "no_daemon":
        lines.append("GPS: no daemon")
    else:
        fix = _FIX_LABEL.get(status.get("fix", "none"), "?")
        if status.get("fix") in ("2d", "3d"):
            hdop = status.get("hdop")
            hdop_s = f", HDOP {hdop:.1f}" if isinstance(hdop, (int, float)) else ""
            lines.append(f"GPS: {fix}, {status.get('sats_used', 0)} sats{hdop_s}")
        else:
            lines.append(f"GPS: {fix}")
    div = status.get("divergence") or {}
    if div.get("active"):
        city = div.get("ip_city") or "your network area"
        lines.append(f"GPS no fix — network suggests {city}")
    return lines
