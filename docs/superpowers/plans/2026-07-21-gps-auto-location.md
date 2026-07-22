# GPS Auto-Location Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make FlightScnr Pi locate itself from a USB GPS receiver, re-homing the radar when the device is relocated, with a graceful fallback chain and an explicit Auto/Manual mode.

**Architecture:** `gpsd` (system daemon, hotplug via `USBAUTO`) owns the u-blox device. A `GpsClient` background thread in the display process reads gpsd's JSON socket, runs a pure `LocationResolver` state machine, publishes `gps_status.json`, and writes `location.json` on a settled re-home — which the display's existing 2 s poll applies through a new consolidated `_recenter()`. IP geolocation is a divergence detector, not a primary position source.

**Tech Stack:** Python 3.13, gpsd (apt), plain sockets + json (no `python3-gps` dependency), pygame (display), Flask (portal), `unittest` for tests.

## Global Constraints

- **No new Python packages.** `requirements.txt` is untouched. gpsd is read over its JSON socket with stdlib `socket` + `json`. Only apt-level `gpsd`/`gpsd-clients` are added.
- **GPS time is never used.** Only latitude/longitude/fix-quality. The system clock stays NTP-driven; timezone follows location through the existing `tz_lookup` path already invoked by the weather refresh.
- **Distance in meters** via `gps_resolver.haversine_m` — never `overhead.haversine` (`overhead.py:157`), which is unit-dependent from an import-time `DISTANCE_UNITS` snapshot.
- **Test import hazard:** the suite is import-order fragile (`overhead.py:23` snapshots `DISTANCE_UNITS`; `tests/test_overhead_utils.py:36` sets it at module scope). **All GPS test files MUST do their imports *inside test methods*, never at module scope** — matching the existing `tests/test_position_smooth.py` convention. This avoids importing `overhead`/the app before `test_overhead_utils` runs.
- **Tests use `unittest`** (class-based, `sys.path.insert(0, ...)` at top for path only), run via `../flightscnr-venv/bin/python -m pytest tests/<file> -q` on the Pi (`planepi`), which has pygame in its venv. The Mac has no pygame.
- **gpsd stays bound to localhost.** Never pass `-G`.
- **Backward compatibility:** existing `location.json` readers use `lat`/`lon`; new fields (`mode`, `source`, `updated_at`) are additive.

**Working directory:** the repo is checked out on both the Mac (`/Users/davidsayed/repos/FlightScnr_Pi`, authoring) and the Pi (`~/FlightScnr_Pi`, test execution). Edit on the Mac, `scp` changed files to the Pi to run tests, or edit directly on the Pi. Commits happen in the Mac checkout.

---

### Task 1: Geo distance helper + normalized GPS report type

**Files:**
- Create: `flightscnr/utilities/gps_resolver.py`
- Test: `flightscnr/tests/test_gps_resolver.py`

**Interfaces:**
- Produces: `haversine_m(lat1, lon1, lat2, lon2) -> float` (great-circle meters); `GpsReport` dataclass with fields `fix: str` (`"none"|"2d"|"3d"`), `lat: float | None`, `lon: float | None`, `sats_used: int`, `hdop: float | None`, `speed_mps: float | None`, and `has_fix() -> bool`.

- [ ] **Step 1: Write the failing test**

```python
# flightscnr/tests/test_gps_resolver.py
"""Unit tests for the pure GPS location-resolution logic (no gpsd, no threads)."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestHaversineMeters(unittest.TestCase):
    def test_zero_distance(self):
        from utilities.gps_resolver import haversine_m

        self.assertAlmostEqual(haversine_m(47.6, -122.3, 47.6, -122.3), 0.0, places=3)

    def test_known_short_distance(self):
        from utilities.gps_resolver import haversine_m

        # ~0.001 deg latitude ≈ 111.2 m
        d = haversine_m(47.6000, -122.3000, 47.6010, -122.3000)
        self.assertTrue(110.0 < d < 113.0, f"expected ~111 m, got {d}")


class TestGpsReport(unittest.TestCase):
    def test_no_fix_report(self):
        from utilities.gps_resolver import GpsReport

        r = GpsReport(fix="none", lat=None, lon=None, sats_used=0, hdop=None, speed_mps=None)
        self.assertFalse(r.has_fix())

    def test_3d_fix_report(self):
        from utilities.gps_resolver import GpsReport

        r = GpsReport(fix="3d", lat=47.6, lon=-122.3, sats_used=7, hdop=1.2, speed_mps=0.1)
        self.assertTrue(r.has_fix())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `../flightscnr-venv/bin/python -m pytest tests/test_gps_resolver.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'utilities.gps_resolver'`

- [ ] **Step 3: Write minimal implementation**

```python
# flightscnr/utilities/gps_resolver.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `../flightscnr-venv/bin/python -m pytest tests/test_gps_resolver.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add flightscnr/utilities/gps_resolver.py flightscnr/tests/test_gps_resolver.py
git commit -m "feat(gps): geo distance helper and GpsReport type"
```

---

### Task 2: gpsd JSON report parsing

**Files:**
- Create: `flightscnr/utilities/gpsd_reader.py`
- Test: `flightscnr/tests/test_gpsd_reader.py`

**Interfaces:**
- Consumes: `GpsReport` from `utilities.gps_resolver`.
- Produces: `apply_gpsd_object(obj: dict, current: GpsReport) -> GpsReport` — returns a NEW `GpsReport` with fields updated from one gpsd JSON object. `TPV` sets `fix`/`lat`/`lon`/`speed_mps`; `SKY` sets `hdop`/`sats_used`; other classes return `current` unchanged. gpsd `mode` maps 0/1→`"none"`, 2→`"2d"`, 3→`"3d"`.

- [ ] **Step 1: Write the failing test**

```python
# flightscnr/tests/test_gpsd_reader.py
"""Unit tests for parsing gpsd JSON reports (no socket)."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestApplyGpsdObject(unittest.TestCase):
    def _empty(self):
        from utilities.gps_resolver import GpsReport

        return GpsReport()

    def test_tpv_3d_sets_position_and_fix(self):
        from utilities.gpsd_reader import apply_gpsd_object

        obj = {"class": "TPV", "mode": 3, "lat": 47.5899, "lon": -122.1186, "speed": 0.08}
        r = apply_gpsd_object(obj, self._empty())
        self.assertEqual(r.fix, "3d")
        self.assertAlmostEqual(r.lat, 47.5899)
        self.assertAlmostEqual(r.lon, -122.1186)
        self.assertAlmostEqual(r.speed_mps, 0.08)

    def test_tpv_mode_1_is_no_fix(self):
        from utilities.gpsd_reader import apply_gpsd_object

        r = apply_gpsd_object({"class": "TPV", "mode": 1}, self._empty())
        self.assertEqual(r.fix, "none")

    def test_sky_sets_hdop_and_used_satellites(self):
        from utilities.gpsd_reader import apply_gpsd_object

        obj = {
            "class": "SKY",
            "hdop": 2.36,
            "satellites": [
                {"PRN": 4, "used": True},
                {"PRN": 7, "used": True},
                {"PRN": 9, "used": False},
            ],
        }
        r = apply_gpsd_object(obj, self._empty())
        self.assertAlmostEqual(r.hdop, 2.36)
        self.assertEqual(r.sats_used, 2)

    def test_unrelated_class_returns_current_unchanged(self):
        from utilities.gpsd_reader import apply_gpsd_object

        cur = self._empty()
        r = apply_gpsd_object({"class": "VERSION", "release": "3.25"}, cur)
        self.assertEqual(r.fix, "none")

    def test_tpv_does_not_clobber_prior_sky_fields(self):
        from utilities.gpsd_reader import apply_gpsd_object

        after_sky = apply_gpsd_object(
            {"class": "SKY", "hdop": 1.5, "satellites": [{"PRN": 1, "used": True}]},
            self._empty(),
        )
        after_tpv = apply_gpsd_object(
            {"class": "TPV", "mode": 3, "lat": 47.6, "lon": -122.3}, after_sky
        )
        self.assertAlmostEqual(after_tpv.hdop, 1.5)
        self.assertEqual(after_tpv.sats_used, 1)
        self.assertEqual(after_tpv.fix, "3d")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `../flightscnr-venv/bin/python -m pytest tests/test_gpsd_reader.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'utilities.gpsd_reader'`

- [ ] **Step 3: Write minimal implementation**

```python
# flightscnr/utilities/gpsd_reader.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `../flightscnr-venv/bin/python -m pytest tests/test_gpsd_reader.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add flightscnr/utilities/gpsd_reader.py flightscnr/tests/test_gpsd_reader.py
git commit -m "feat(gps): parse gpsd TPV/SKY JSON into GpsReport"
```

---

### Task 3: gpsd socket stream with reconnect

**Files:**
- Modify: `flightscnr/utilities/gpsd_reader.py`
- Test: `flightscnr/tests/test_gpsd_reader.py` (add a class)

**Interfaces:**
- Produces: `stream_reports(host, port, stop_event, on_report, on_device_state, connect_timeout=5.0)` — connects to gpsd, sends the WATCH command, and calls `on_report(GpsReport)` for each update and `on_device_state(str)` with `"present"`/`"no_daemon"` on connect/disconnect. Loops with backoff until `stop_event.is_set()`. Uses only stdlib `socket`.

- [ ] **Step 1: Write the failing test** (fake in-process gpsd server)

```python
# add to flightscnr/tests/test_gpsd_reader.py

class TestStreamReports(unittest.TestCase):
    def test_reads_reports_from_a_fake_gpsd(self):
        import socket
        import threading
        import time

        from utilities.gpsd_reader import stream_reports

        # A minimal fake gpsd: accept one client, expect the WATCH line, emit
        # a SKY then a TPV, then close.
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        def serve():
            conn, _ = srv.accept()
            conn.recv(1024)  # consume ?WATCH
            conn.sendall(b'{"class":"SKY","hdop":1.5,"satellites":[{"PRN":1,"used":true}]}\n')
            conn.sendall(b'{"class":"TPV","mode":3,"lat":47.6,"lon":-122.3}\n')
            time.sleep(0.2)
            conn.close()

        threading.Thread(target=serve, daemon=True).start()

        reports = []
        states = []
        stop = threading.Event()

        def on_report(r):
            reports.append(r)
            if r.has_fix():
                stop.set()  # got what we need

        def on_state(s):
            states.append(s)

        stream_reports("127.0.0.1", port, stop, on_report, on_state, connect_timeout=2.0)

        self.assertIn("present", states)
        self.assertTrue(any(r.fix == "3d" for r in reports))
        got = [r for r in reports if r.fix == "3d"][-1]
        self.assertAlmostEqual(got.lat, 47.6)
        self.assertEqual(got.sats_used, 1)  # SKY applied before TPV
        srv.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `../flightscnr-venv/bin/python -m pytest tests/test_gpsd_reader.py::TestStreamReports -q`
Expected: FAIL — `ImportError: cannot import name 'stream_reports'`

- [ ] **Step 3: Write minimal implementation** (append to `gpsd_reader.py`)

```python
import socket
import threading  # noqa: F401  (documented dependency for callers)
import time

_WATCH = b'?WATCH={"enable":true,"json":true}\n'


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
        time.sleep(backoff)
        backoff = min(backoff * 2, 30.0)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `../flightscnr-venv/bin/python -m pytest tests/test_gpsd_reader.py -q`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add flightscnr/utilities/gpsd_reader.py flightscnr/tests/test_gpsd_reader.py
git commit -m "feat(gps): stream gpsd reports over a socket with reconnect"
```

---

### Task 4: The Auto-mode resolution state machine

**Files:**
- Modify: `flightscnr/utilities/gps_resolver.py`
- Test: `flightscnr/tests/test_gps_resolver.py` (add classes)

**Interfaces:**
- Consumes: `GpsReport`, `haversine_m`.
- Produces:
  - `ResolverConfig` dataclass: `min_sats:int`, `max_hdop:float`, `rehome_min_m:float`, `settle_s:float`, `nofix_grace_s:float`, `divergence_km:float`.
  - `Decision` dataclass: `action:str` (`"none"|"rehome"|"bootstrap"|"diverge"|"clear_notice"`), `lat:float|None`, `lon:float|None`, `source:str|None` (`"gps"|"estimated"`).
  - `LocationResolver(cfg, geoip_lookup)` where `geoip_lookup: Callable[[], tuple[float,float,str] | None]`. Method `observe(report, now, home, mode, has_saved_home) -> Decision`. `home` is `(lat, lon)` current effective home or `None`. Only acts in `mode == "auto"`.

- [ ] **Step 1: Write the failing test**

```python
# add to flightscnr/tests/test_gps_resolver.py

def _cfg():
    from utilities.gps_resolver import ResolverConfig

    return ResolverConfig(
        min_sats=4, max_hdop=5.0, rehome_min_m=250.0,
        settle_s=25.0, nofix_grace_s=180.0, divergence_km=25.0,
    )


def _fix(lat, lon, sats=7, hdop=1.2):
    from utilities.gps_resolver import GpsReport

    return GpsReport(fix="3d", lat=lat, lon=lon, sats_used=sats, hdop=hdop, speed_mps=0.0)


class TestResolverRehome(unittest.TestCase):
    def test_settled_move_beyond_threshold_rehomes(self):
        from utilities.gps_resolver import LocationResolver

        res = LocationResolver(_cfg(), geoip_lookup=lambda: None)
        home = (47.6000, -122.3000)
        far = (47.6100, -122.3000)  # ~1.1 km away
        # first sighting starts the settle timer, no action yet
        d0 = res.observe(_fix(*far), now=0.0, home=home, mode="auto", has_saved_home=True)
        self.assertEqual(d0.action, "none")
        # still settling before settle_s elapses
        d1 = res.observe(_fix(*far), now=10.0, home=home, mode="auto", has_saved_home=True)
        self.assertEqual(d1.action, "none")
        # after settle window, commit re-home
        d2 = res.observe(_fix(*far), now=30.0, home=home, mode="auto", has_saved_home=True)
        self.assertEqual(d2.action, "rehome")
        self.assertAlmostEqual(d2.lat, far[0])
        self.assertEqual(d2.source, "gps")

    def test_small_move_does_not_rehome(self):
        from utilities.gps_resolver import LocationResolver

        res = LocationResolver(_cfg(), geoip_lookup=lambda: None)
        home = (47.6000, -122.3000)
        near = (47.6001, -122.3000)  # ~11 m
        for t in (0.0, 10.0, 30.0, 60.0):
            d = res.observe(_fix(*near), now=t, home=home, mode="auto", has_saved_home=True)
            self.assertEqual(d.action, "none")

    def test_stray_during_settle_restarts_timer(self):
        from utilities.gps_resolver import LocationResolver

        res = LocationResolver(_cfg(), geoip_lookup=lambda: None)
        home = (47.6000, -122.3000)
        far_a = (47.6100, -122.3000)
        far_b = (47.6300, -122.3000)  # >250 m from far_a, resets cluster
        res.observe(_fix(*far_a), now=0.0, home=home, mode="auto", has_saved_home=True)
        res.observe(_fix(*far_b), now=10.0, home=home, mode="auto", has_saved_home=True)
        # only 20 s on the far_b cluster — not settled yet
        d = res.observe(_fix(*far_b), now=30.0, home=home, mode="auto", has_saved_home=True)
        self.assertEqual(d.action, "none")


class TestResolverRejectsBadFixes(unittest.TestCase):
    def test_high_hdop_fix_ignored(self):
        from utilities.gps_resolver import LocationResolver

        res = LocationResolver(_cfg(), geoip_lookup=lambda: None)
        home = (47.6000, -122.3000)
        bad = _fix(47.6100, -122.3000, hdop=9.9)
        for t in (0.0, 30.0):
            d = res.observe(bad, now=t, home=home, mode="auto", has_saved_home=True)
            self.assertEqual(d.action, "none")

    def test_too_few_sats_ignored(self):
        from utilities.gps_resolver import LocationResolver

        res = LocationResolver(_cfg(), geoip_lookup=lambda: None)
        home = (47.6000, -122.3000)
        bad = _fix(47.6100, -122.3000, sats=2)
        d = res.observe(bad, now=30.0, home=home, mode="auto", has_saved_home=True)
        self.assertEqual(d.action, "none")


class TestResolverNoFix(unittest.TestCase):
    def test_nofix_within_grace_keeps_quiet(self):
        from utilities.gps_resolver import GpsReport, LocationResolver

        res = LocationResolver(_cfg(), geoip_lookup=lambda: (99.0, 99.0, "Nowhere"))
        home = (47.6000, -122.3000)
        d = res.observe(GpsReport(fix="none"), now=60.0, home=home, mode="auto", has_saved_home=True)
        self.assertEqual(d.action, "none")

    def test_persistent_nofix_far_ip_raises_divergence(self):
        from utilities.gps_resolver import GpsReport, LocationResolver

        # IP far (>25 km) from home → diverge
        res = LocationResolver(_cfg(), geoip_lookup=lambda: (40.0, -75.0, "Philadelphia"))
        home = (47.6000, -122.3000)
        res.observe(GpsReport(fix="none"), now=0.0, home=home, mode="auto", has_saved_home=True)
        d = res.observe(GpsReport(fix="none"), now=200.0, home=home, mode="auto", has_saved_home=True)
        self.assertEqual(d.action, "diverge")
        self.assertEqual(d.source, None)  # notice only; no location change

    def test_persistent_nofix_near_ip_stays_quiet(self):
        from utilities.gps_resolver import GpsReport, LocationResolver

        res = LocationResolver(_cfg(), geoip_lookup=lambda: (47.61, -122.31, "Seattle"))
        home = (47.6000, -122.3000)
        res.observe(GpsReport(fix="none"), now=0.0, home=home, mode="auto", has_saved_home=True)
        d = res.observe(GpsReport(fix="none"), now=200.0, home=home, mode="auto", has_saved_home=True)
        self.assertEqual(d.action, "none")

    def test_fresh_unit_no_home_bootstraps_from_ip(self):
        from utilities.gps_resolver import GpsReport, LocationResolver

        res = LocationResolver(_cfg(), geoip_lookup=lambda: (47.61, -122.31, "Seattle"))
        d = res.observe(GpsReport(fix="none"), now=0.0, home=None, mode="auto", has_saved_home=False)
        self.assertEqual(d.action, "bootstrap")
        self.assertEqual(d.source, "estimated")
        self.assertAlmostEqual(d.lat, 47.61)


class TestResolverManualMode(unittest.TestCase):
    def test_manual_mode_never_acts(self):
        from utilities.gps_resolver import LocationResolver

        res = LocationResolver(_cfg(), geoip_lookup=lambda: (40.0, -75.0, "Philadelphia"))
        home = (47.6000, -122.3000)
        far = (47.6100, -122.3000)
        d = res.observe(_fix(*far), now=30.0, home=home, mode="manual", has_saved_home=True)
        self.assertEqual(d.action, "none")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `../flightscnr-venv/bin/python -m pytest tests/test_gps_resolver.py -q`
Expected: FAIL — `ImportError: cannot import name 'LocationResolver'`

- [ ] **Step 3: Write minimal implementation** (append to `gps_resolver.py`)

```python
from typing import Callable, Optional, Tuple


@dataclass
class ResolverConfig:
    min_sats: int = 4
    max_hdop: float = 5.0
    rehome_min_m: float = 250.0
    settle_s: float = 25.0
    nofix_grace_s: float = 180.0
    divergence_km: float = 25.0


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
        self._did_ip_check = False

    def _accepts(self, r: GpsReport) -> bool:
        return (
            r.has_fix()
            and r.sats_used >= self._cfg.min_sats
            and r.hdop is not None
            and r.hdop <= self._cfg.max_hdop
        )

    def observe(self, report: GpsReport, now: float, home, mode: str, has_saved_home: bool) -> Decision:
        if mode != "auto":
            return Decision(action="none")

        if self._accepts(report):
            self._nofix_since = None
            self._did_ip_check = False
            lat, lon = float(report.lat), float(report.lon)
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
                if self._notice_active:
                    self._notice_active = False
                return Decision(action="rehome", lat=lat, lon=lon, source="gps")
            return Decision(action="none")

        # No usable fix.
        self._cluster_anchor = None
        self._cluster_since = None
        if self._nofix_since is None:
            self._nofix_since = now

        # Fresh unit with nothing saved: bootstrap from IP immediately.
        if not has_saved_home:
            ip = self._geoip() if not self._did_ip_check else None
            self._did_ip_check = True
            if ip is not None:
                return Decision(action="bootstrap", lat=ip[0], lon=ip[1], source="estimated")
            return Decision(action="none")

        # Persistent no-fix past grace → one IP divergence check.
        if (now - self._nofix_since) >= self._cfg.nofix_grace_s and not self._did_ip_check:
            self._did_ip_check = True
            ip = self._geoip()
            if ip is not None and home is not None:
                km = haversine_m(ip[0], ip[1], home[0], home[1]) / 1000.0
                if km >= self._cfg.divergence_km:
                    self._notice_active = True
                    return Decision(action="diverge")
        return Decision(action="none")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `../flightscnr-venv/bin/python -m pytest tests/test_gps_resolver.py -q`
Expected: PASS (all resolver tests green)

- [ ] **Step 5: Commit**

```bash
git add flightscnr/utilities/gps_resolver.py flightscnr/tests/test_gps_resolver.py
git commit -m "feat(gps): Auto-mode location resolution state machine"
```

---

### Task 5: IP geolocation lookup

**Files:**
- Create: `flightscnr/utilities/geoip.py`
- Test: `flightscnr/tests/test_geoip.py`

**Interfaces:**
- Produces: `lookup(url: str, timeout=(3.05, 5)) -> tuple[float, float, str] | None` — GET `url`, parse `latitude`/`longitude`/`city` (ipwho.is schema), return `(lat, lon, city)` or `None` on any failure. Uses `requests` (already a dependency).

- [ ] **Step 1: Write the failing test**

```python
# flightscnr/tests/test_geoip.py
"""Unit tests for IP geolocation (requests mocked, no network)."""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestGeoipLookup(unittest.TestCase):
    def test_parses_ipwhois_response(self):
        from utilities import geoip

        payload = {"success": True, "latitude": 47.61, "longitude": -122.33, "city": "Seattle"}
        fake = mock.Mock()
        fake.raise_for_status = mock.Mock()
        fake.json = mock.Mock(return_value=payload)
        with mock.patch("utilities.geoip.requests.get", return_value=fake):
            result = geoip.lookup("https://ipwho.is/")
        self.assertEqual(result, (47.61, -122.33, "Seattle"))

    def test_network_error_returns_none(self):
        import requests

        from utilities import geoip

        with mock.patch("utilities.geoip.requests.get", side_effect=requests.RequestException("boom")):
            self.assertIsNone(geoip.lookup("https://ipwho.is/"))

    def test_missing_fields_returns_none(self):
        from utilities import geoip

        fake = mock.Mock()
        fake.raise_for_status = mock.Mock()
        fake.json = mock.Mock(return_value={"success": False})
        with mock.patch("utilities.geoip.requests.get", return_value=fake):
            self.assertIsNone(geoip.lookup("https://ipwho.is/"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `../flightscnr-venv/bin/python -m pytest tests/test_geoip.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'utilities.geoip'`

- [ ] **Step 3: Write minimal implementation**

```python
# flightscnr/utilities/geoip.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `../flightscnr-venv/bin/python -m pytest tests/test_geoip.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add flightscnr/utilities/geoip.py flightscnr/tests/test_geoip.py
git commit -m "feat(gps): coarse IP geolocation lookup"
```

---

### Task 6: Config — env vars, mode, and extended location.json

**Files:**
- Modify: `flightscnr/config.py` (env block near `:299`; location helpers `:131-213`)
- Test: `flightscnr/tests/test_gps_config.py`

**Interfaces:**
- Produces (module-level constants): `LOCATION_MODE`, `GPS_ENABLED`, `GPSD_HOST`, `GPSD_PORT`, `GPS_MIN_SATS`, `GPS_MAX_HDOP`, `GPS_REHOME_MIN_METERS`, `GPS_SETTLE_SECONDS`, `GPS_NOFIX_GRACE_SECONDS`, `GPS_DIVERGENCE_KM`, `GEOIP_ENABLED`, `GEOIP_URL`.
- Produces (functions): `location_mode() -> str`; `set_location_mode(mode: str)`; extends `set_location_home(lat, lon, source="portal")` and `_save_location_file(lat, lon, source)` to persist `{mode, lat, lon, source, updated_at}`; `location_source() -> str`; `has_saved_location() -> bool`.

- [ ] **Step 1: Write the failing test**

```python
# flightscnr/tests/test_gps_config.py
"""Config: GPS env vars and extended location.json persistence."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestGpsConfig(unittest.TestCase):
    def test_defaults_present(self):
        import config

        self.assertEqual(config.LOCATION_MODE, "auto")
        self.assertTrue(hasattr(config, "GPS_ENABLED"))
        self.assertEqual(config.GPSD_PORT, 2947)
        self.assertEqual(config.GEOIP_URL, "https://ipwho.is/")

    def test_set_location_home_writes_source_and_mode(self):
        import config

        d = tempfile.mkdtemp()
        old = config.LOCATION_FILE
        try:
            config.LOCATION_FILE = os.path.join(d, "location.json")
            config.set_location_home(47.61, -122.33, source="gps")
            with open(config.LOCATION_FILE) as fh:
                data = json.load(fh)
            self.assertEqual(data["source"], "gps")
            self.assertIn("mode", data)
            self.assertIn("updated_at", data)
            self.assertAlmostEqual(data["lat"], 47.61)
        finally:
            config.LOCATION_FILE = old

    def test_backward_compatible_read_of_bare_lat_lon(self):
        import config

        d = tempfile.mkdtemp()
        old = config.LOCATION_FILE
        old_mtime = config._location_file_mtime
        try:
            config.LOCATION_FILE = os.path.join(d, "location.json")
            with open(config.LOCATION_FILE, "w") as fh:
                json.dump({"lat": 40.0, "lon": -75.0}, fh)  # legacy shape
            config._location_file_mtime = None
            changed = config.reload_location_override()
            self.assertTrue(changed)
            self.assertAlmostEqual(config.LOCATION_HOME[0], 40.0)
        finally:
            config.LOCATION_FILE = old
            config._location_file_mtime = old_mtime
```

- [ ] **Step 2: Run test to verify it fails**

Run: `../flightscnr-venv/bin/python -m pytest tests/test_gps_config.py -q`
Expected: FAIL — `AttributeError: module 'config' has no attribute 'LOCATION_MODE'`

- [ ] **Step 3: Write minimal implementation**

Add to the env block of `config.py` (after `SPEED_UNITS`, near `:333`):

```python
# --- GPS / auto-location ---
LOCATION_MODE = os.environ.get("LOCATION_MODE", "auto").strip().lower()
GPS_ENABLED = _bool(os.environ.get("GPS_ENABLED", "True"))
GPSD_HOST = os.environ.get("GPSD_HOST", "127.0.0.1")
GPSD_PORT = int(os.environ.get("GPSD_PORT", "2947"))
GPS_MIN_SATS = int(os.environ.get("GPS_MIN_SATS", "4"))
GPS_MAX_HDOP = float(os.environ.get("GPS_MAX_HDOP", "5.0"))
GPS_REHOME_MIN_METERS = float(os.environ.get("GPS_REHOME_MIN_METERS", "250"))
GPS_SETTLE_SECONDS = float(os.environ.get("GPS_SETTLE_SECONDS", "25"))
GPS_NOFIX_GRACE_SECONDS = float(os.environ.get("GPS_NOFIX_GRACE_SECONDS", "180"))
GPS_DIVERGENCE_KM = float(os.environ.get("GPS_DIVERGENCE_KM", "25"))
GEOIP_ENABLED = _bool(os.environ.get("GEOIP_ENABLED", "True"))
GEOIP_URL = os.environ.get("GEOIP_URL", "https://ipwho.is/")

_location_mode_runtime = LOCATION_MODE
_location_source_runtime = "configured"
```

Replace `_save_location_file` (`config.py:150-160`) and `set_location_home` (`config.py:163-171`), and add helpers:

```python
def _save_location_file(lat: float, lon: float, source: str = "portal"):
    import datetime

    os.makedirs(os.path.dirname(LOCATION_FILE), exist_ok=True)
    payload = {
        "mode": _location_mode_runtime,
        "lat": lat,
        "lon": lon,
        "source": source,
        "updated_at": datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
    }
    tmp_path = LOCATION_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    os.replace(tmp_path, LOCATION_FILE)
    try:
        os.chmod(LOCATION_FILE, 0o666)
    except OSError:
        pass


def set_location_home(lat: float, lon: float, source: str = "portal"):
    """Persist and apply radar center coordinates with a source tag."""
    global _location_file_mtime, _location_source_runtime
    _location_source_runtime = source
    _save_location_file(lat, lon, source)
    _apply_home(lat, lon, source)
    try:
        _location_file_mtime = os.path.getmtime(LOCATION_FILE)
    except OSError:
        _location_file_mtime = None


def location_mode() -> str:
    return _location_mode_runtime


def set_location_mode(mode: str):
    """Persist the Auto/Manual mode into location.json (keeps current coords)."""
    global _location_mode_runtime
    mode = (mode or "auto").strip().lower()
    if mode not in ("auto", "manual"):
        mode = "auto"
    _location_mode_runtime = mode
    if location_configured():
        _save_location_file(LOCATION_HOME[0], LOCATION_HOME[1], _location_source_runtime)


def location_source() -> str:
    return _location_source_runtime


def has_saved_location() -> bool:
    return os.path.isfile(LOCATION_FILE)
```

Update `_apply_home` (`config.py:131`) to record the source and mode from a loaded file. In `reload_location_override` (`config.py:186-195`) and `_bootstrap_location_override` (`config.py:204-208`), after loading `data`, also read the optional fields:

```python
        # inside reload_location_override, after lat/lon parsed:
        global _location_mode_runtime, _location_source_runtime
        _location_mode_runtime = str(data.get("mode", _location_mode_runtime)).strip().lower() or "auto"
        _location_source_runtime = str(data.get("source", "portal"))
```

(Apply the same two lines in `_bootstrap_location_override`. `_apply_home`'s existing `source` parameter is passed `_location_source_runtime` there.)

- [ ] **Step 4: Run test to verify it passes**

Run: `../flightscnr-venv/bin/python -m pytest tests/test_gps_config.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Run the full suite to confirm no regression**

Run: `../flightscnr-venv/bin/python -m pytest tests -q --ignore=tests/test_gesture_handler.py`
Expected: `4 failed, 210 passed` — the SAME 4 pre-existing failures as baseline (test_get_airport_coords_iata, _case_insensitive, test_route_display_one_line, test_refresh_invalidates_on_date_change), plus our new passing tests. If the count of failures increased, a GPS test imported `overhead` at module scope — fix the import.

- [ ] **Step 6: Commit**

```bash
git add flightscnr/config.py flightscnr/tests/test_gps_config.py
git commit -m "feat(gps): config env vars, location mode, extended location.json"
```

---

### Task 7: GpsClient orchestrator thread

**Files:**
- Create: `flightscnr/utilities/gps_client.py`
- Test: `flightscnr/tests/test_gps_client.py`

**Interfaces:**
- Consumes: `gpsd_reader.stream_reports`, `gps_resolver.{LocationResolver,ResolverConfig,GpsReport,Decision}`, `geoip.lookup`, `config`.
- Produces: `GpsClient` with `start()`, `stop()`, `status() -> dict` (the `gps_status.json` shape), and `apply_decision(decision)` (writes `location.json` via `config.set_location_home` on `rehome`/`bootstrap`). Constructor accepts injectable `reader=` (defaults to real `stream_reports`) and `now=` (defaults to `time.monotonic`) for testing. `get_client() -> GpsClient` singleton, plus `start_gps()` convenience mirroring `sync_ais_client()`.
- Writes `gps_status.json` in `FLIGHTSCNR_DATA_DIR` (rate-limited).

- [ ] **Step 1: Write the failing test** (fake reader injected — no gpsd, no threads run to completion)

```python
# flightscnr/tests/test_gps_client.py
"""GpsClient orchestration with an injected fake reader (no gpsd, no real socket)."""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestGpsClientDecisions(unittest.TestCase):
    def test_rehome_decision_writes_location_file(self):
        import config
        from utilities.gps_client import GpsClient
        from utilities.gps_resolver import Decision

        d = tempfile.mkdtemp()
        old = config.LOCATION_FILE
        try:
            config.LOCATION_FILE = os.path.join(d, "location.json")
            gc = GpsClient(data_dir=d)
            gc.apply_decision(Decision(action="rehome", lat=47.61, lon=-122.33, source="gps"))
            self.assertTrue(os.path.isfile(config.LOCATION_FILE))
            self.assertAlmostEqual(config.LOCATION_HOME[0], 47.61)
            self.assertEqual(config.location_source(), "gps")
        finally:
            config.LOCATION_FILE = old

    def test_status_file_written(self):
        from utilities.gps_client import GpsClient
        from utilities.gps_resolver import GpsReport

        d = tempfile.mkdtemp()
        gc = GpsClient(data_dir=d)
        gc._on_report(GpsReport(fix="3d", lat=47.6, lon=-122.3, sats_used=7, hdop=1.1, speed_mps=0.0))
        gc._flush_status(force=True)
        import json

        with open(os.path.join(d, "gps_status.json")) as fh:
            status = json.load(fh)
        self.assertEqual(status["fix"], "3d")
        self.assertEqual(status["sats_used"], 7)

    def test_injected_reader_drives_a_rehome(self):
        import config
        from utilities.gps_client import GpsClient
        from utilities.gps_resolver import GpsReport

        d = tempfile.mkdtemp()
        old = config.LOCATION_FILE
        old_mode = config._location_mode_runtime
        try:
            config.LOCATION_FILE = os.path.join(d, "location.json")
            config._location_mode_runtime = "auto"
            config.set_location_home(47.6000, -122.3000, source="configured")

            # Fake reader: emit accepted far fixes across an advancing clock, then stop.
            far = GpsReport(fix="3d", lat=47.6100, lon=-122.3000, sats_used=7, hdop=1.0, speed_mps=0.0)

            def fake_reader(host, port, stop_event, on_report, on_state, connect_timeout=5.0):
                on_state("present")
                for _ in range(5):
                    if stop_event.is_set():
                        break
                    on_report(far)

            clock = {"t": 0.0}

            def fake_now():
                clock["t"] += 10.0
                return clock["t"]

            gc = GpsClient(data_dir=d, reader=fake_reader, now=fake_now)
            gc.start()
            gc._thread.join(timeout=5.0)
            gc.stop()
            self.assertAlmostEqual(config.LOCATION_HOME[0], 47.6100, places=4)
        finally:
            config.LOCATION_FILE = old
            config._location_mode_runtime = old_mode
```

- [ ] **Step 2: Run test to verify it fails**

Run: `../flightscnr-venv/bin/python -m pytest tests/test_gps_client.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'utilities.gps_client'`

- [ ] **Step 3: Write minimal implementation**

```python
# flightscnr/utilities/gps_client.py
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


def get_client() -> GpsClient:
    global _client
    if _client is None:
        _client = GpsClient()
    return _client


def start_gps() -> None:
    """Start the GPS client if enabled. Mirrors ais_client.sync_ais_client()."""
    if not config.GPS_ENABLED:
        logger.info("GPS disabled (GPS_ENABLED=False)")
        return
    get_client().start()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `../flightscnr-venv/bin/python -m pytest tests/test_gps_client.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add flightscnr/utilities/gps_client.py flightscnr/tests/test_gps_client.py
git commit -m "feat(gps): GpsClient orchestrator thread and status publishing"
```

---

### Task 8: Consolidate re-home invalidation into `_recenter()`

**Files:**
- Modify: `flightscnr/display/round_touch/app.py` (add `_recenter`; rewrite `_save_map_pan:540`, `_maybe_reload_location:1333`)
- Test: `flightscnr/tests/test_recenter.py`

**Interfaces:**
- Produces: `RoundTouchDisplay._recenter(self, lat, lon, source)` which performs ALL display-side invalidation: `map_bg.invalidate()` + `prewarm_all_scales()`, `rainviewer_overlay.invalidate()` + `request_overlay()`, `sync_ais_client()`, `self._position_smoother.reset()`, weather/tz refresh on a daemon thread, and `self.overhead.grab_data()`. Both existing callers route through it.

- [ ] **Step 1: Write the failing test** (bare object, stubbed module functions)

```python
# flightscnr/tests/test_recenter.py
"""_recenter consolidates all invalidation. Verify each subsystem is invoked."""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestRecenter(unittest.TestCase):
    def test_recenter_invalidates_all_subsystems(self):
        from display.round_touch.app import RoundTouchDisplay

        disp = object.__new__(RoundTouchDisplay)

        smoother = mock.Mock()
        overhead = mock.Mock()
        disp._position_smoother = smoother
        disp.overhead = overhead
        disp._weather_redraw_pending = False

        with mock.patch("display.round_touch.app.map_bg") as m_map, \
             mock.patch("display.round_touch.app.rainviewer_overlay") as m_rv, \
             mock.patch("display.round_touch.app.sync_ais_client") as m_ais:
            disp._recenter(47.61, -122.33, source="gps")

        m_map.invalidate.assert_called_once()
        m_map.prewarm_all_scales.assert_called_once()
        m_rv.invalidate.assert_called_once()
        m_rv.request_overlay.assert_called_once()
        m_ais.assert_called_once()
        smoother.reset.assert_called_once()
        overhead.grab_data.assert_called_once()
```

Note: this requires `sync_ais_client` to be importable at module scope in `app.py`. Add `from utilities.ais_client import sync_ais_client` to the imports at the top of `app.py` (it is currently imported lazily inside methods at `app.py:100,1324`).

- [ ] **Step 2: Run test to verify it fails**

Run (on Pi): `../flightscnr-venv/bin/python -m pytest tests/test_recenter.py -q`
Expected: FAIL — `AttributeError: 'RoundTouchDisplay' object has no attribute '_recenter'`

- [ ] **Step 3: Write minimal implementation**

At the top of `app.py`, with the other `from utilities...` / `from display.round_touch...` imports, add:

```python
from utilities.ais_client import sync_ais_client
```

Add the method to `RoundTouchDisplay` (near `_maybe_reload_location`):

```python
    def _recenter(self, lat, lon, source="portal"):
        """Single source of truth for re-homing side effects (fixes the old
        divergent invalidation between touch-pan, the location poll, and GPS)."""
        map_bg.invalidate()
        map_bg.prewarm_all_scales()
        rainviewer_overlay.invalidate()
        rainviewer_overlay.request_overlay()
        try:
            sync_ais_client()
        except Exception:
            logger.debug("AIS resubscribe after recenter skipped", exc_info=True)
        self._position_smoother.reset()

        def _after_recenter():
            try:
                from display.round_touch import weather_data

                weather_data.after_radar_center_changed(float(lat), float(lon))
            except Exception:
                logger.exception("Weather/timezone refresh after recenter failed")
            else:
                self._weather_redraw_pending = True

        Thread(target=_after_recenter, daemon=True).start()
        self.overhead.grab_data()
```

Rewrite `_maybe_reload_location` (`app.py:1333`) body after the `reload_location_override()` guard:

```python
    def _maybe_reload_location(self):
        try:
            from config import LOCATION_HOME, reload_location_override, location_source

            if not reload_location_override():
                return
            lat, lon = float(LOCATION_HOME[0]), float(LOCATION_HOME[1])
            self._recenter(lat, lon, source=location_source())
            self._safe_draw()
        except ImportError:
            pass
```

Rewrite the tail of `_save_map_pan` (`app.py:552-572`) to delegate:

```python
        set_location_home(lat, lon, source="manual")
        self._panning_map = False
        self._pan_offset = (0, 0)
        self._pan_drag_start = None
        self._recenter(lat, lon, source="manual")
        self._refresh_flights()
```

(Remove the now-duplicated `map_bg`/`rainviewer_overlay`/`_after_recenter`/`grab_data` lines that `_recenter` now owns.)

- [ ] **Step 4: Run test to verify it passes**

Run (on Pi): `../flightscnr-venv/bin/python -m pytest tests/test_recenter.py -q`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add flightscnr/display/round_touch/app.py flightscnr/tests/test_recenter.py
git commit -m "refactor(display): consolidate re-home invalidation into _recenter (fixes rainviewer/AIS gaps)"
```

---

### Task 9: Start GpsClient in the display process

**Files:**
- Modify: `flightscnr/display/round_touch/app.py` (`__init__`, near `:97-104`)

**Interfaces:**
- Consumes: `gps_client.start_gps`.
- No new produced interface; GpsClient writes `location.json`, and the existing `_maybe_reload_location` poll (now → `_recenter`) applies re-homes. This wiring is verified on-device in Task 14 (starting a thread is not unit-tested, matching the AIS pattern which is also not unit-tested).

- [ ] **Step 1: Add the startup call**

In `RoundTouchDisplay.__init__`, right after the AIS startup block (`app.py:99-104`), add:

```python
        try:
            from utilities.gps_client import start_gps

            start_gps()
        except Exception:
            logger.debug("GPS client startup skipped", exc_info=True)
```

- [ ] **Step 2: Verify the app still imports and starts (on Pi)**

Run:
```bash
ssh planepi 'cd ~/FlightScnr_Pi/flightscnr && ../flightscnr-venv/bin/python -c "import ast; ast.parse(open(\"display/round_touch/app.py\").read()); print(\"parse OK\")"'
```
Expected: `parse OK`

Then restart the service and confirm the thread starts:
```bash
ssh planepi 'sudo systemctl restart flightscnr && sleep 12 && sudo journalctl -u flightscnr -b --no-pager | grep -iE "GPS client thread started|GPS disabled" | tail -2'
```
Expected: a line `GPS client thread started (gpsd 127.0.0.1:2947)` (it will log connect failures until Task 13 installs gpsd — that is fine here).

- [ ] **Step 3: Commit**

```bash
git add flightscnr/display/round_touch/app.py
git commit -m "feat(gps): start GpsClient in the display process"
```

---

### Task 10: About-screen GPS status lines

**Files:**
- Create: `flightscnr/display/round_touch/gps_about.py` (pure line-builder)
- Modify: `flightscnr/display/round_touch/screens/info.py` (`PAGE_MAIN` lines, `:465-476`)
- Test: `flightscnr/tests/test_gps_about.py`

**Interfaces:**
- Produces: `gps_about.status_lines(status: dict | None) -> list[str]` — turns a `gps_status.json` dict into human lines: a `Source:` line, a `GPS:` fix line, and — when `divergence.active` — a divergence notice line. Returns `[]` when `status` is None.

- [ ] **Step 1: Write the failing test**

```python
# flightscnr/tests/test_gps_about.py
"""About-screen GPS line rendering (pure)."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestGpsAboutLines(unittest.TestCase):
    def test_none_status_yields_no_lines(self):
        from display.round_touch.gps_about import status_lines

        self.assertEqual(status_lines(None), [])

    def test_3d_fix_lines(self):
        from display.round_touch.gps_about import status_lines

        lines = status_lines({
            "fix": "3d", "sats_used": 7, "hdop": 1.2, "device": "present",
            "source": "gps", "mode": "auto",
            "divergence": {"active": False},
        })
        self.assertTrue(any("Source: GPS" in ln for ln in lines))
        self.assertTrue(any("GPS: 3D" in ln and "7 sats" in ln for ln in lines))

    def test_divergence_notice_line(self):
        from display.round_touch.gps_about import status_lines

        lines = status_lines({
            "fix": "none", "sats_used": 0, "hdop": None, "device": "present",
            "source": "manual", "mode": "auto",
            "divergence": {"active": True, "ip_city": "Philadelphia", "km": 3800.0},
        })
        self.assertTrue(any("Philadelphia" in ln for ln in lines))

    def test_no_device_line(self):
        from display.round_touch.gps_about import status_lines

        lines = status_lines({
            "fix": "none", "sats_used": 0, "hdop": None, "device": "absent",
            "source": "configured", "mode": "auto", "divergence": {"active": False},
        })
        self.assertTrue(any("no device" in ln.lower() for ln in lines))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `../flightscnr-venv/bin/python -m pytest tests/test_gps_about.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'display.round_touch.gps_about'`

- [ ] **Step 3: Write minimal implementation**

```python
# flightscnr/display/round_touch/gps_about.py
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
```

Wire into `info.py` `PAGE_MAIN` (`:465-476`). After the `Lon:` line, splice the GPS lines:

```python
        gps_lines = []
        try:
            import json
            from utilities.gps_client import get_client

            gps_lines = _gps_about_lines(get_client().status())
        except Exception:
            gps_lines = []
        lines = [
            f"IP: {_local_ip()}",
            f"Host: {_hostname()}.local",
            *sys_lines,
            f"Lat: {LOCATION_HOME[0]:.5f}",
            f"Lon: {LOCATION_HOME[1]:.5f}",
            *gps_lines,
            f"Min height: {settings.min_height_ft()} ft",
            f"Web: {web_portal_url(_hostname())}",
            _route_api_line("FR24", FR24_API_KEY),
            _route_api_line("AirLabs", AIRLABS_API_KEY),
            _route_api_line("AIS", AISSTREAM_API_KEY),
        ]
```

And add near the top of `info.py` (module imports):

```python
from display.round_touch.gps_about import status_lines as _gps_about_lines
```

- [ ] **Step 4: Run test to verify it passes**

Run: `../flightscnr-venv/bin/python -m pytest tests/test_gps_about.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add flightscnr/display/round_touch/gps_about.py flightscnr/display/round_touch/screens/info.py flightscnr/tests/test_gps_about.py
git commit -m "feat(gps): About-screen GPS status lines"
```

---

### Task 11: Portal endpoints — mode, status, resolution actions

**Files:**
- Modify: `flightscnr/web/app.py` (`/location/json:191`, `/location/set:202`)
- Test: `flightscnr/tests/test_gps_portal.py`

**Interfaces:**
- `GET /location/json` → adds `mode`, `source`, and a `gps` block (from `gps_status.json` if present) and `divergence`.
- `POST /location/set` accepts either `{"mode": "auto"|"manual"}` (switch mode), `{"location": "lat, lon"}` (manual set → `source="manual"`, and implies `mode="manual"`), or `{"action": "use_estimated"}` (write the divergence IP coords as `source="estimated"`).

- [ ] **Step 1: Write the failing test**

```python
# flightscnr/tests/test_gps_portal.py
"""Portal location endpoints: mode switch, status, resolution actions."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestLocationPortal(unittest.TestCase):
    def _client(self, data_dir):
        import config

        config.LOCATION_FILE = os.path.join(data_dir, "location.json")
        from web import app as webapp

        webapp.app.config.update(TESTING=True)
        return webapp.app.test_client(), config

    def test_location_json_reports_mode(self):
        d = tempfile.mkdtemp()
        client, config = self._client(d)
        config._location_mode_runtime = "auto"
        config.set_location_home(47.6, -122.3, source="gps")
        resp = client.get("/location/json")
        body = resp.get_json()
        self.assertEqual(body["mode"], "auto")
        self.assertEqual(body["source"], "gps")

    def test_post_mode_switch_to_manual(self):
        d = tempfile.mkdtemp()
        client, config = self._client(d)
        config.set_location_home(47.6, -122.3, source="gps")
        resp = client.post("/location/set", json={"mode": "manual"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(config.location_mode(), "manual")

    def test_post_manual_location_sets_source_manual(self):
        d = tempfile.mkdtemp()
        client, config = self._client(d)
        resp = client.post("/location/set", json={"location": "47.65, -122.35"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(config.location_source(), "manual")
        self.assertEqual(config.location_mode(), "manual")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `../flightscnr-venv/bin/python -m pytest tests/test_gps_portal.py -q`
Expected: FAIL — assertion errors (`mode`/`source` keys absent; mode not switched).

- [ ] **Step 3: Write minimal implementation**

Update imports at the top of `web/app.py` to include the new helpers:

```python
from config import (
    # ...existing...
    location_mode,
    set_location_mode,
    location_source,
)
```

Rewrite `/location/json` (`web/app.py:191-199`):

```python
@app.get("/location/json")
def location_json():
    reload_location_override()
    gps = {}
    try:
        import json as _json

        status_path = os.path.join(DATA_DIR, "gps_status.json")
        if os.path.isfile(status_path):
            with open(status_path, encoding="utf-8") as fh:
                gps = _json.load(fh)
    except Exception:
        gps = {}
    return jsonify({
        "location": format_location_home() if location_configured() else "",
        "configured": location_configured(),
        "mode": location_mode(),
        "source": location_source(),
        "gps": gps,
        "divergence": gps.get("divergence", {"active": False}),
    })
```

(`DATA_DIR` is already defined in `web/app.py`; confirm the name — it is set near the top from `FLIGHTSCNR_DATA_DIR`.)

In `/location/set` (`web/app.py:202`), handle the new payload shapes at the top of the function, before the existing coordinate parsing:

```python
@app.post("/location/set")
def location_set():
    data = request.get_json(force=True) or {}

    # Mode switch only.
    if "mode" in data and "location" not in data:
        set_location_mode(str(data.get("mode", "auto")))
        return jsonify({"message": f"Location mode: {location_mode()}", "mode": location_mode()})

    # Resolution action: adopt the coarse IP estimate.
    if data.get("action") == "use_estimated":
        try:
            import json as _json

            with open(os.path.join(DATA_DIR, "gps_status.json"), encoding="utf-8") as fh:
                div = _json.load(fh).get("divergence", {})
            set_location_home(float(div["ip_lat"]), float(div["ip_lon"]), source="estimated")
            return jsonify({"message": "Using estimated location", "location": format_location_home()})
        except Exception:
            return jsonify({"message": "No estimated location available"}), 400

    raw = data.get("location", "").strip()
    if not raw:
        return jsonify({"message": "Enter coordinates as latitude, longitude"}), 400
    try:
        lat, lon = parse_lat_lon_pair(raw)
    except ValueError as exc:
        return jsonify({"message": str(exc)}), 400
    # A manual pin sets mode=manual and source=manual.
    set_location_mode("manual")
    set_location_home(lat, lon, source="manual")
    # ...keep the existing weather/map refresh block that follows...
```

(Leave the existing weather/map-invalidation block after `set_location_home` intact.)

- [ ] **Step 4: Run test to verify it passes**

Run: `../flightscnr-venv/bin/python -m pytest tests/test_gps_portal.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add flightscnr/web/app.py flightscnr/tests/test_gps_portal.py
git commit -m "feat(gps): portal mode switch, GPS status, and use-estimated action"
```

---

### Task 12: Portal UI — Location section

**Files:**
- Modify: `flightscnr/web/templates/index.html` (Location section)

**Interfaces:** Consumes `GET /location/json` (`mode`, `source`, `gps`, `divergence`) and posts to `/location/set`. No unit test — verified in the browser during Task 14.

- [ ] **Step 1: Locate the Location section**

Run:
```bash
grep -n "location/set\|location/json\|Radar center\|id=\"location" flightscnr/web/templates/index.html | head
```
Note the surrounding markup and existing JS `fetch` pattern used by the other sections (e.g. weather).

- [ ] **Step 2: Add mode toggle + status + divergence controls**

In the Location section, add (matching the file's existing control styling):

```html
<div class="row">
  <label>Location mode</label>
  <select id="loc-mode">
    <option value="auto">Auto (GPS)</option>
    <option value="manual">Manual</option>
  </select>
</div>
<div id="gps-status" class="muted">GPS: …</div>
<div id="gps-divergence" style="display:none;">
  <p id="gps-divergence-text"></p>
  <button id="btn-use-estimated">Use estimated</button>
</div>
```

Add JS that refreshes on load and every 5 s:

```html
<script>
async function refreshLocation() {
  const r = await fetch('/location/json'); const d = await r.json();
  document.getElementById('loc-mode').value = d.mode || 'auto';
  const g = d.gps || {};
  const fix = g.fix ? g.fix.toUpperCase() : '—';
  const dev = g.device && g.device !== 'present' ? ` (${g.device})` : '';
  document.getElementById('gps-status').textContent =
    `GPS: ${fix}${dev} · sats ${g.sats_used ?? 0} · source ${d.source || '—'}`;
  const div = d.divergence || {active:false};
  const box = document.getElementById('gps-divergence');
  if (div.active) {
    box.style.display = 'block';
    document.getElementById('gps-divergence-text').textContent =
      `GPS has no fix; your network suggests ${div.ip_city || 'elsewhere'}, far from the last known location.`;
  } else { box.style.display = 'none'; }
}
document.getElementById('loc-mode').addEventListener('change', async (e) => {
  await fetch('/location/set', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({mode: e.target.value})});
  refreshLocation();
});
document.getElementById('btn-use-estimated').addEventListener('click', async () => {
  await fetch('/location/set', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({action:'use_estimated'})});
  refreshLocation();
});
refreshLocation(); setInterval(refreshLocation, 5000);
</script>
```

Pre-seed the manual coordinate input with the last-known value (`d.location`) when present — reuse whatever input id the existing "set location" control uses (found in Step 1); set its `.value` inside `refreshLocation()` only when the field is not focused, so typing isn't clobbered.

- [ ] **Step 3: Verify markup parses / page renders (on Pi)**

After Task 13 installs everything, load `http://planepi.local/` and confirm the Location section shows a mode dropdown and a live GPS status line (deferred check — done in Task 14).

- [ ] **Step 4: Commit**

```bash
git add flightscnr/web/templates/index.html
git commit -m "feat(gps): portal Location UI — mode toggle, GPS status, use-estimated"
```

---

### Task 13: Install gpsd and document env

**Files:**
- Modify: `install-pi.sh` (`install_apt_packages:60`; add a `setup_gpsd` step called from `cmd_install:458`)
- Modify: `.env.example`

**Interfaces:** system-level — gpsd installed, `/etc/default/gpsd` written, `gpsd.socket` enabled.

- [ ] **Step 1: Add gpsd to apt packages**

In `install_apt_packages` (`install-pi.sh:63-72`), add `gpsd gpsd-clients` to the `apt-get install` list.

- [ ] **Step 2: Add a `setup_gpsd` function and call it**

Add near the other setup functions:

```bash
setup_gpsd() {
    log_step "GPS daemon (gpsd)"
    local dev="/dev/serial/by-id/usb-u-blox_AG_-_www.u-blox.com_u-blox_7_-_GPS_GNSS_Receiver-if00"
    # Fall back to ttyACM0 if the by-id path is absent at install time.
    [ -e "$dev" ] || dev="/dev/ttyACM0"
    cat > /etc/default/gpsd <<EOF
# Managed by FlightScnr install-pi.sh
START_DAEMON="true"
USBAUTO="true"
DEVICES="$dev"
GPSD_OPTIONS="-n"
EOF
    systemctl enable gpsd.socket >/dev/null 2>&1 || true
    systemctl restart gpsd.socket >/dev/null 2>&1 || true
    log_ok "gpsd configured (device: $dev)"
}
```

Call it in `cmd_install` (`install-pi.sh:458`), right after `verify_python_deps || true`:

```bash
    setup_gpsd
```

- [ ] **Step 3: Document env vars in `.env.example`**

Append a GPS section:

```ini
# --- GPS / auto-location ---
# Location mode: "auto" (GPS drives the radar center) or "manual".
LOCATION_MODE=auto
# Master switch for the GPS reader thread.
GPS_ENABLED=True
# gpsd JSON socket (leave default unless gpsd runs elsewhere).
GPSD_HOST=127.0.0.1
GPSD_PORT=2947
# Fix-acceptance gates.
GPS_MIN_SATS=4
GPS_MAX_HDOP=5.0
# Re-home only after moving this far and settling this long.
GPS_REHOME_MIN_METERS=250
GPS_SETTLE_SECONDS=25
# No-fix tolerated before the IP divergence check; divergence distance.
GPS_NOFIX_GRACE_SECONDS=180
GPS_DIVERGENCE_KM=25
# Coarse IP geolocation fallback (no key, HTTPS). Used only on no-fix / fresh unit.
GEOIP_ENABLED=True
GEOIP_URL=https://ipwho.is/
```

- [ ] **Step 4: Run the installer's gpsd step on the Pi and verify**

```bash
ssh planepi 'sudo bash ~/FlightScnr_Pi/install-pi.sh install --no-start --skip-apt >/tmp/gpsd-setup.log 2>&1; sudo apt-get install -y -qq gpsd gpsd-clients >>/tmp/gpsd-setup.log 2>&1; cat /etc/default/gpsd'
```
Expected: the written `/etc/default/gpsd` contents.

```bash
ssh planepi 'systemctl is-active gpsd.socket; command -v gpspipe && timeout 5 gpspipe -w -n 5 2>/dev/null | grep -o "\"class\":\"[A-Z]*\"" | sort -u'
```
Expected: `active`, and `TPV`/`SKY`/`VERSION` class lines — proof gpsd serves the receiver.

- [ ] **Step 5: Commit**

```bash
git add install-pi.sh .env.example
git commit -m "feat(gps): install and configure gpsd; document GPS env vars"
```

---

### Task 14: On-device end-to-end verification

**Files:** none (verification only). Uses the `grim`/`xdotool` screenshot loop and the portal.

- [ ] **Step 1: Restart with everything in place**

```bash
ssh planepi 'sudo systemctl restart flightscnr && sleep 15 && sudo journalctl -u flightscnr -b --no-pager | grep -iE "GPS client thread started|GPS (rehome|bootstrap)|gpsd" | tail'
```
Expected: `GPS client thread started`, no crash.

- [ ] **Step 2: Confirm a real fix flows to gps_status.json**

```bash
ssh planepi 'sleep 60; cat /var/lib/flightscnr/gps_status.json'
```
Expected: `"fix":"3d"` (or `2d`), non-zero `sats_used`. (Receiver needs sky view — near a window.)

- [ ] **Step 3: Verify the About screen shows GPS status**

Ensure 720×720, screenshot the About screen (swipe up from radar), confirm a `Source:` and `GPS: 3D … sats` line:
```bash
ssh planepi 'sudo /usr/local/bin/flightscnr-headless-mode.sh'
# swipe up from radar (paced drag), then grim /tmp/about.png and scp back per the ARCHITECTURE-NOTES loop
```
Expected: About screen renders the GPS lines.

- [ ] **Step 4: Verify re-home**

With `LOCATION_MODE=auto` and a fix, temporarily lower the threshold to force a re-home without physically moving:
```bash
ssh planepi 'sudo sed -i "s/^GPS_REHOME_MIN_METERS=.*/GPS_REHOME_MIN_METERS=1/" /etc/flightscnr.env; sudo sed -i "s/^GPS_SETTLE_SECONDS=.*/GPS_SETTLE_SECONDS=5/" /etc/flightscnr.env; sudo systemctl restart flightscnr'
ssh planepi 'sleep 45; grep -o "\"source\": \"gps\"" /var/lib/flightscnr/location.json'
```
Expected: `location.json` `source` becomes `gps`; the radar re-centers on the true location.
Then restore the thresholds: `sudo sed -i "s/^GPS_REHOME_MIN_METERS=.*/GPS_REHOME_MIN_METERS=250/;s/^GPS_SETTLE_SECONDS=.*/GPS_SETTLE_SECONDS=25/" /etc/flightscnr.env && sudo systemctl restart flightscnr`.

- [ ] **Step 5: Verify hotplug**

```bash
# Unplug the GPS physically (or: ssh planepi 'sudo systemctl stop gpsd.socket gpsd.service')
ssh planepi 'sleep 8; cat /var/lib/flightscnr/gps_status.json'   # device: no_daemon/absent, last-known home kept
# Replug (or: ssh planepi 'sudo systemctl start gpsd.socket')
ssh planepi 'sleep 20; cat /var/lib/flightscnr/gps_status.json'  # device: present, fix resumes
```
Expected: status reflects device loss then auto-recovery; radar never blanks.

- [ ] **Step 6: Verify Manual mode ignores GPS**

Via the portal, switch mode to Manual and set a location far from the fix; confirm `location.json` `mode=manual`, `source=manual`, and that GPS does not overwrite it after `GPS_SETTLE_SECONDS`.
```bash
ssh planepi 'sleep 40; grep -E "\"mode\"|\"source\"" /var/lib/flightscnr/location.json'
```
Expected: stays `manual`.

- [ ] **Step 7: Full suite green (baseline unchanged)**

```bash
ssh planepi 'cd ~/FlightScnr_Pi/flightscnr && ../flightscnr-venv/bin/python -m pytest tests -q --ignore=tests/test_gesture_handler.py 2>&1 | tail -3'
```
Expected: only the 4 pre-existing failures; all new GPS tests pass.

- [ ] **Step 8: Update ARCHITECTURE-NOTES and commit**

Add a short "GPS auto-location (implemented)" section to `docs/ARCHITECTURE-NOTES.md` pointing at the new modules and the spec.

```bash
git add docs/ARCHITECTURE-NOTES.md
git commit -m "docs: note GPS auto-location implementation"
```

---

## Self-Review

**Spec coverage:**
- Mode Auto/Manual → Tasks 6 (config), 11 (portal), 12 (UI). ✓
- Auto resolution / precedence / divergence detector → Task 4 (state machine). ✓
- Keep-last-known + notify on divergence → Task 4 (`diverge`), 10 (About), 12 (portal notice). ✓
- Fresh-unit IP bootstrap vs configured-home → Task 4 tests `test_fresh_unit_no_home_bootstraps_from_ip`; `has_saved_home` gate in Task 7 (`has_saved_location() or location_configured()`). ✓
- gpsd + hotplug, JSON socket, no python-gps dep → Tasks 2, 3, 13. ✓
- `_recenter()` consolidation fixing rainviewer/AIS gaps → Task 8. ✓
- Time = NTP; timezone follows location → inherited via `_recenter`'s `weather_data.after_radar_center_changed` (Task 8); no GPS-time code anywhere. ✓
- Config env vars → Task 6 + `.env.example` (Task 13). ✓
- `location.json` / `gps_status.json` schemas → Tasks 6, 7. ✓
- About + portal surfaces → Tasks 10, 11, 12. ✓
- Distance in meters, not `overhead.haversine` → Task 1 `haversine_m`, enforced in resolver + client. ✓
- Failure modes (no daemon/absent/IP offline/stall/garbage) → Tasks 3 (reconnect/backoff/timeout, skip bad JSON), 5 (None on failure), 7 (device state). ✓
- Test strategy (pure logic + fake gpsd + on-device) → Tasks 1–7 unit, 3/7 fakes, 14 on-device. ✓

**Placeholder scan:** none — every step carries runnable code or an exact command.

**Type consistency:** `GpsReport`, `ResolverConfig`, `Decision`, `LocationResolver.observe(...)`, `apply_gpsd_object`, `stream_reports(host, port, stop_event, on_report, on_device_state, connect_timeout)`, `geoip.lookup(url, timeout)`, `config.set_location_home(lat, lon, source)`, `config.location_mode()/set_location_mode()/location_source()/has_saved_location()`, `GpsClient.status()/apply_decision()/start()/stop()`, `gps_about.status_lines(status)` — names/signatures are used consistently across tasks.

**Note on the import-order hazard:** every GPS test imports inside methods, so none pulls `overhead` in at module scope. Task 6 Step 5 explicitly re-checks the full-suite failure count to catch a regression early.
