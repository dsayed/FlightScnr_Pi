"""Background GPS orchestrator: gpsd socket → resolver → location.json + gps_status.json.

Mirrors utilities/ais_client.py (a daemon feed thread). The heavy decision logic lives
in the pure gps_resolver; this class is the I/O shell: read reports, run the resolver on
an advancing clock, apply decisions, and publish status. Injectable reader/now for tests.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time

import config
from utilities import geoip
from utilities.gps_resolver import (
    Decision,
    GpsReport,
    LocationResolver,
    ResolverConfig,
)
from utilities.gpsd_reader import stream_reports

logger = logging.getLogger(__name__)

_STATUS_MIN_INTERVAL_S = 3.0


class GpsClient:
    def __init__(self, data_dir: str | None = None, reader=None, now=None):
        self._data_dir = data_dir or os.environ.get("FLIGHTSCNR_DATA_DIR", "/var/lib/flightscnr")
        self._status_path = os.path.join(self._data_dir, "gps_status.json")
        self._reader = reader or stream_reports
        self._now = now or time.monotonic
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._report = GpsReport()
        self._device = "no_daemon"
        self._divergence = {"active": False, "ip_city": None, "ip_lat": None, "ip_lon": None, "km": None}
        self._last_status_ts = 0.0
        cfg = ResolverConfig(
            min_sats=config.GPS_MIN_SATS,
            max_hdop=config.GPS_MAX_HDOP,
            rehome_min_m=config.GPS_REHOME_MIN_METERS,
            settle_s=config.GPS_SETTLE_SECONDS,
            nofix_grace_s=config.GPS_NOFIX_GRACE_SECONDS,
            divergence_km=config.GPS_DIVERGENCE_KM,
        )
        self._resolver = LocationResolver(cfg, geoip_lookup=self._geoip)

    # --- lifecycle -----------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._thread_main, name="gpsclient", daemon=True)
        self._thread.start()
        logger.info("GPS client thread started (gpsd %s:%s)", config.GPSD_HOST, config.GPSD_PORT)

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)

    def _thread_main(self) -> None:
        try:
            self._reader(
                config.GPSD_HOST,
                config.GPSD_PORT,
                self._stop,
                self._on_report,
                self._on_device_state,
            )
        except Exception:
            logger.exception("GPS reader thread crashed")

    # --- callbacks from the reader ------------------------------------------
    def _on_device_state(self, state: str) -> None:
        with self._lock:
            self._device = "present" if state == "present" else state
        self._tick(None)

    def _on_report(self, report: GpsReport) -> None:
        with self._lock:
            self._report = report
        self._tick(report)

    def _tick(self, report: GpsReport | None) -> None:
        now = self._now()
        home = None
        if config.location_configured():
            home = (float(config.LOCATION_HOME[0]), float(config.LOCATION_HOME[1]))
        decision = self._resolver.observe(
            report if report is not None else self._report,
            now=now,
            home=home,
            mode=config.location_mode(),
            has_saved_home=config.has_saved_location() or config.location_configured(),
        )
        if decision.action in ("rehome", "bootstrap", "diverge", "clear_notice"):
            self.apply_decision(decision)
        self._flush_status()

    # --- decision application ------------------------------------------------
    def apply_decision(self, decision: Decision) -> None:
        if decision.action in ("rehome", "bootstrap") and decision.lat is not None:
            config.set_location_home(decision.lat, decision.lon, source=decision.source or "gps")
            with self._lock:
                self._divergence = {"active": False, "ip_city": None, "ip_lat": None, "ip_lon": None, "km": None}
            logger.info("GPS %s → %.5f, %.5f", decision.action, decision.lat, decision.lon)
        elif decision.action == "diverge":
            logger.info("GPS divergence notice raised")
        elif decision.action == "clear_notice":
            with self._lock:
                self._divergence = {"active": False, "ip_city": None, "ip_lat": None, "ip_lon": None, "km": None}
            logger.info("GPS divergence notice cleared")
        self._flush_status(force=True)

    def _geoip(self):
        if not config.GEOIP_ENABLED:
            return None
        result = geoip.lookup(config.GEOIP_URL)
        if result is not None:
            with self._lock:
                home = config.LOCATION_HOME
                from utilities.gps_resolver import haversine_m

                km = haversine_m(result[0], result[1], home[0], home[1]) / 1000.0
                self._divergence = {
                    "active": km >= config.GPS_DIVERGENCE_KM,
                    "ip_city": result[2],
                    "ip_lat": result[0],
                    "ip_lon": result[1],
                    "km": round(km, 1),
                }
        return result

    # --- status publishing ---------------------------------------------------
    def status(self) -> dict:
        with self._lock:
            r = self._report
            return {
                "fix": r.fix,
                "sats_used": r.sats_used,
                "hdop": r.hdop,
                "lat": r.lat,
                "lon": r.lon,
                "speed_mps": r.speed_mps,
                "device": self._device,
                "source": config.location_source(),
                "mode": config.location_mode(),
                "divergence": dict(self._divergence),
            }

    def _flush_status(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and (now - self._last_status_ts) < _STATUS_MIN_INTERVAL_S:
            return
        self._last_status_ts = now
        data = self.status()
        try:
            os.makedirs(self._data_dir, exist_ok=True)
            tmp = self._status_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            os.replace(tmp, self._status_path)
        except OSError:
            logger.debug("could not write gps_status.json", exc_info=True)


_client: GpsClient | None = None
_client_lock = threading.Lock()


def get_client() -> GpsClient:
    global _client
    with _client_lock:
        if _client is None:
            _client = GpsClient()
    return _client


def start_gps() -> None:
    """Start the GPS client if enabled. Mirrors ais_client.sync_ais_client()."""
    if not config.GPS_ENABLED:
        logger.info("GPS disabled (GPS_ENABLED=False)")
        return
    get_client().start()
