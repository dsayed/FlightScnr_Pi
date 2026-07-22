"""gpsd JSON-socket client: parse TPV/SKY reports and stream normalized GpsReports.

We speak gpsd's line protocol directly over a socket (no python3-gps dependency):
connect, send `?WATCH={"enable":true,"json":true}`, read newline-delimited JSON.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import socket
import threading  # noqa: F401  (documented dependency for callers)

from utilities.gps_resolver import GpsReport

logger = logging.getLogger(__name__)

_MODE_TO_FIX = {0: "none", 1: "none", 2: "2d", 3: "3d"}

_WATCH = b'?WATCH={"enable":true,"json":true}\n'


def apply_gpsd_object(obj: dict, current: GpsReport) -> GpsReport:
    """Fold one gpsd JSON object into a new GpsReport (TPV=position, SKY=quality)."""
    cls = obj.get("class")
    if cls == "TPV":
        fix = _MODE_TO_FIX.get(int(obj.get("mode", 0)), "none")
        updated = dataclasses.replace(current, fix=fix)
        # Only trust position when there is a fix; on no-fix, keep prior coords.
        if fix != "none" and "lat" in obj and "lon" in obj:
            updated.lat = float(obj["lat"])
            updated.lon = float(obj["lon"])
        if "speed" in obj:
            try:
                updated.speed_mps = float(obj["speed"])
            except (TypeError, ValueError):
                updated.speed_mps = None
        return updated
    if cls == "SKY":
        hdop = obj.get("hdop")
        try:
            hdop = float(hdop) if hdop is not None else current.hdop
        except (TypeError, ValueError):
            hdop = current.hdop
        # gpsd interleaves full SKY (per-satellite array) with terse DOP-only SKY
        # that carries just uSat. Prefer uSat; fall back to counting used sats;
        # keep the prior count when a message has neither (never reset to 0).
        if "uSat" in obj:
            try:
                used = int(obj["uSat"])
            except (TypeError, ValueError):
                used = current.sats_used
        elif obj.get("satellites"):
            used = sum(1 for s in obj["satellites"] if s.get("used"))
        else:
            used = current.sats_used
        return dataclasses.replace(current, hdop=hdop, sats_used=used)
    return current


def stream_reports(host, port, stop_event, on_report, on_device_state, connect_timeout=5.0):
    """Connect to gpsd and stream GpsReports until stop_event is set.

    Reconnects with backoff. on_device_state receives "present" on a live socket
    and "no_daemon" when gpsd can't be reached.
    """
    backoff = 1.0
    while not stop_event.is_set():
        sock = None
        try:
            sock = socket.create_connection((host, port), timeout=connect_timeout)
            sock.settimeout(1.0)
            sock.sendall(_WATCH)
            on_device_state("present")
            backoff = 1.0
            current = GpsReport()
            buf = b""
            while not stop_event.is_set():
                try:
                    chunk = sock.recv(4096)
                except socket.timeout:
                    continue
                if not chunk:
                    break  # gpsd closed
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line.decode("utf-8", "replace"))
                    except ValueError:
                        continue
                    current = apply_gpsd_object(obj, current)
                    on_report(current)
        except OSError as exc:
            logger.debug("gpsd connect/read failed: %s", exc)
            on_device_state("no_daemon")
        finally:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
        if stop_event.is_set():
            break
        stop_event.wait(backoff)
        backoff = min(backoff * 2, 30.0)
