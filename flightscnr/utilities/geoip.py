"""Coarse IP geolocation — used only as a divergence detector / fresh-unit bootstrap
when GPS has no fix. Never on the per-fix path. Default service: ipwho.is (no key, HTTPS)."""

from __future__ import annotations

import logging

import requests

logger = logging.getLogger(__name__)


def lookup(url: str, timeout=(3.05, 5)):
    """Return (lat, lon, city) from an IP-geolocation service, or None on any failure."""
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        logger.info("geoip lookup failed: %s", exc)
        return None
    lat = data.get("latitude")
    lon = data.get("longitude")
    if lat is None or lon is None:
        logger.info("geoip response missing coordinates: %s", data.get("message") or data)
        return None
    try:
        return (float(lat), float(lon), str(data.get("city") or ""))
    except (TypeError, ValueError):
        return None
