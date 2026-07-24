"""Pure helper: turn a gps_status.json dict into About-screen text lines."""

from __future__ import annotations

_SOURCE_LABEL = {"gps": "GPS", "manual": "Manual", "estimated": "Estimated", "configured": "Configured", "portal": "Manual"}
_FIX_LABEL = {"3d": "3D", "2d": "2D", "none": "no fix"}


def status_lines(status) -> list[str]:
    if not status:
        return []
    mode = str(status.get("mode", "")).lower()
    source = str(status.get("source", ""))
    device = status.get("device", "present")
    fix = status.get("fix", "none")
    has_fix = fix in ("2d", "3d")

    # Line 1: make the SELECTED mode explicit, and in Auto say what GPS is doing.
    # A live receiver can simply agree with a manually-set home — that never
    # flips `source` to "gps", which used to render as "Source: Manual" and read
    # as "GPS isn't being used" even with a perfect fix.
    lines: list[str] = []
    if mode == "auto":
        if device in ("absent", "no_daemon"):
            lines.append("Mode: Auto")
        elif source == "gps":
            lines.append("Mode: Auto · GPS set location")
        elif has_fix:
            lines.append("Mode: Auto · GPS locked")
        else:
            lines.append("Mode: Auto · acquiring fix")
    elif mode == "manual":
        lines.append("Mode: Manual")
    else:
        lines.append(f"Source: {_SOURCE_LABEL.get(source, source or '?')}")

    # Line 2: receiver / device detail.
    if device == "absent":
        lines.append("GPS: no device")
    elif device == "no_daemon":
        lines.append("GPS: no daemon")
    else:
        fix_label = _FIX_LABEL.get(fix, "?")
        if has_fix:
            hdop = status.get("hdop")
            hdop_s = f", HDOP {hdop:.1f}" if isinstance(hdop, (int, float)) else ""
            lines.append(f"GPS: {fix_label}, {status.get('sats_used', 0)} sats{hdop_s}")
        else:
            lines.append(f"GPS: {fix_label}")

    div = status.get("divergence") or {}
    if div.get("active"):
        city = div.get("ip_city") or "your network area"
        lines.append(f"GPS no fix — network suggests {city}")
    return lines
