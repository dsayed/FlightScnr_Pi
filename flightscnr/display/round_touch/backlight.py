"""Apply display brightness on Raspberry Pi (backlight sysfs)."""

import logging
import os

logger = logging.getLogger("flightscnr.display")

_last_pct: int | None = None


def _backlight_paths() -> list[str]:
    base = "/sys/class/backlight"
    if not os.path.isdir(base):
        return []
    paths = []
    for name in sorted(os.listdir(base)):
        bpath = os.path.join(base, name, "brightness")
        maxpath = os.path.join(base, name, "max_brightness")
        if os.path.isfile(bpath) and os.path.isfile(maxpath):
            paths.append(bpath)
    return paths


def software_dim_alpha(percent: int) -> int:
    """Black-overlay alpha (0..255) that emulates a brightness percent.

    Compositing black at alpha a scales content luminance by (1 - a/255), so
    alpha = (100 - pct)/100 * 255 makes luminance track brightness_pct linearly.
    pct>=100 -> 0 (no overlay); pct==0 -> 255 (full black, the HDMI "off").
    """
    pct = max(0, min(100, int(percent)))
    return int(round((100 - pct) / 100 * 255))


def has_hardware() -> bool:
    """True if a real backlight sysfs device exists (DSI panels).

    HDMI monitors expose no backlight control, so callers fall back to a
    software dim overlay. Cheap enough to call each frame; sysfs devices do not
    appear at runtime.
    """
    return bool(_backlight_paths())


def apply_percent(percent: int) -> bool:
    global _last_pct
    pct = max(0, min(100, int(percent)))
    if _last_pct == pct:
        return True
    ok = False
    for bpath in _backlight_paths():
        try:
            maxpath = os.path.join(os.path.dirname(bpath), "max_brightness")
            with open(maxpath, encoding="utf-8") as f:
                max_val = int(f.read().strip())
            value = max(1, int(round(max_val * pct / 100)))
            with open(bpath, "w", encoding="utf-8") as f:
                f.write(str(value))
            ok = True
        except OSError as exc:
            logger.debug("Backlight write failed %s: %s", bpath, exc)
    if ok:
        _last_pct = pct
    return ok
