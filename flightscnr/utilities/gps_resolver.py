"""Pure GPS location-resolution logic: distance math, report type, and the
Auto-mode resolution state machine. No sockets, threads, or file I/O — everything
is injected, so this module is fully unit-testable without hardware."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Optional, Tuple


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


@dataclass
class ResolverConfig:
    min_sats: int = 4
    max_hdop: float = 5.0
    rehome_min_m: float = 250.0
    settle_s: float = 25.0
    nofix_grace_s: float = 180.0
    divergence_km: float = 25.0
    ip_retry_s: float = 60.0


@dataclass
class Decision:
    action: str = "none"  # none | rehome | bootstrap | diverge | clear_notice
    lat: float | None = None
    lon: float | None = None
    source: str | None = None  # gps | estimated


class LocationResolver:
    """Pure Auto-mode resolution. Fed reports + a monotonic `now`; returns Decisions.

    geoip_lookup() -> (lat, lon, city) | None is injected so the logic stays testable.
    """

    def __init__(self, cfg: ResolverConfig, geoip_lookup: Callable[[], Optional[Tuple[float, float, str]]]):
        self._cfg = cfg
        self._geoip = geoip_lookup
        self._cluster_anchor: tuple[float, float] | None = None
        self._cluster_since: float | None = None
        self._nofix_since: float | None = None
        self._notice_active = False
        self._last_ip_ts: float | None = None

    def _accepts(self, r: GpsReport) -> bool:
        return (
            r.has_fix()
            and r.sats_used >= self._cfg.min_sats
            and r.hdop is not None
            and r.hdop <= self._cfg.max_hdop
        )

    def observe(self, report: GpsReport, now: float, home, mode: str, has_saved_home: bool) -> Decision:
        if mode != "auto":
            # Reset time-based state so wall-clock spent in manual mode never
            # counts toward settle/grace windows once the user returns to auto.
            self._cluster_anchor = None
            self._cluster_since = None
            self._nofix_since = None
            self._last_ip_ts = None
            return Decision(action="none")

        if self._accepts(report):
            self._nofix_since = None
            self._last_ip_ts = None
            lat, lon = float(report.lat), float(report.lon)
            decision = self._observe_accepted(lat, lon, now, home)
            # A real fix means the "no fix" premise behind any divergence notice
            # is gone — clear it. A rehome supersedes the notice silently.
            if self._notice_active:
                self._notice_active = False
                if decision.action != "rehome":
                    return Decision(action="clear_notice")
            return decision

        # No usable fix.
        self._cluster_anchor = None
        self._cluster_since = None
        if self._nofix_since is None:
            self._nofix_since = now

        # Fresh unit with nothing saved: bootstrap from IP, retried at a rate limit
        # until it succeeds (a failed lookup must not permanently block bootstrap).
        if not has_saved_home:
            if self._ip_allowed(now):
                self._last_ip_ts = now
                ip = self._geoip()
                if ip is not None:
                    return Decision(action="bootstrap", lat=ip[0], lon=ip[1], source="estimated")
            return Decision(action="none")

        # Persistent no-fix past grace → IP divergence check, rate-limited and
        # re-evaluated each interval (not once per process lifetime).
        if (now - self._nofix_since) >= self._cfg.nofix_grace_s and self._ip_allowed(now):
            self._last_ip_ts = now
            ip = self._geoip()
            if ip is not None and home is not None:
                km = haversine_m(ip[0], ip[1], home[0], home[1]) / 1000.0
                if km >= self._cfg.divergence_km and not self._notice_active:
                    self._notice_active = True
                    return Decision(action="diverge")
        return Decision(action="none")

    def _ip_allowed(self, now: float) -> bool:
        return self._last_ip_ts is None or (now - self._last_ip_ts) >= self._cfg.ip_retry_s

    def _observe_accepted(self, lat: float, lon: float, now: float, home) -> Decision:
        # Near current home? nothing to do.
        if home is not None and haversine_m(lat, lon, home[0], home[1]) <= self._cfg.rehome_min_m:
            self._cluster_anchor = None
            self._cluster_since = None
            return Decision(action="none")
        # Candidate move: maintain a settle cluster.
        if (
            self._cluster_anchor is None
            or haversine_m(lat, lon, self._cluster_anchor[0], self._cluster_anchor[1]) > self._cfg.rehome_min_m
        ):
            self._cluster_anchor = (lat, lon)
            self._cluster_since = now
            return Decision(action="none")
        if self._cluster_since is not None and (now - self._cluster_since) >= self._cfg.settle_s:
            self._cluster_anchor = None
            self._cluster_since = None
            return Decision(action="rehome", lat=lat, lon=lon, source="gps")
        return Decision(action="none")
