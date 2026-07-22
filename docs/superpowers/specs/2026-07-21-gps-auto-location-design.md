# GPS Auto-Location — Design

**Date:** 2026-07-21
**Status:** Approved design, pre-implementation
**Hardware:** u-blox 7 USB GNSS receiver (VID:PID `1546:01a7`), enumerates as USB CDC-ACM at
`/dev/serial/by-id/usb-u-blox_AG_-_www.u-blox.com_u-blox_7_-_GPS_GNSS_Receiver-if00` → `/dev/ttyACM0`.
Confirmed working on `planepi.local`: valid NMEA, `ANTSTATUS=OK`, acquires a 3-satellite fix within
~1 minute near a window; a fix landed ~425 m from the manually-configured home.

## Context

FlightScnr Pi centers its radar on a home coordinate. Today that coordinate is typed by hand
(`HOME_LAT`/`HOME_LON` in `/etc/flightscnr.env`, or set via the web portal / on-device drag-to-recenter,
persisted to `location.json`). This feature makes the device locate itself, so relocating it doesn't
require editing config.

**Use case (confirmed):** *occasionally relocated.* The unit is mostly stationary but gets moved
between places (home ↔ office, trips) and should re-home itself wherever it settles. It is **not**
continuously mobile — no live tracking in a moving vehicle — so re-homing is a rare, settled event and
per-fix SD-card writes are a non-issue.

## The model: a location *mode*

Location gains an explicit mode, set in the portal:

- **Manual** — use stored manual coordinates; GPS is ignored. The manual entry field is pre-seeded with
  the last-known position, so the user adjusts from a real starting point rather than typing from zero.
- **Auto** — GPS drives home. A settled fix sets the center and re-homes on a real move. When GPS has no
  fix, the device falls back gracefully (below) and never shows a blank radar.

This replaces the current implicit "`location.json` always wins" precedence with an explicit,
user-visible choice, and gives `LOCATION_SOURCE` real meaning: `GPS` / `Manual` / `Estimated` /
`Configured` (today it is a vestigial `"portal"` string, `config.py:141`).

## Position resolution (Auto mode)

The core insight: **IP geolocation is too coarse to home on, but it is an excellent *divergence
detector*.** So last-known GPS and IP are not competing position sources — last-known is the position,
IP answers "did the device probably move since that fix?"

At any instant up to four signals exist, in precedence:

1. **Live GPS fix** — exact, where you are now. Whenever present, it is the answer.
2. **Last-known effective home** (persisted) — exact but possibly stale. Shown during any no-fix moment.
3. **IP geolocation** — coarse (ISP-level, ±tens of km), reflects the *current network*. Used as a
   divergence check, and — only for a fresh unit with nothing else — as a provisional position.
4. **Configured home** (`HOME_LAT`/`HOME_LON` env) — static backstop.

### State machine

```
BOOT → load location.json → show effective home immediately (never blank)

Fix acceptance gate (all must hold):
    mode ∈ {2D, 3D}   AND   hdop ≤ GPS_MAX_HDOP   AND   sats_used ≥ GPS_MIN_SATS
(We need only lat/lon, so a 2D fix suffices; 3D altitude is unused.)

AUTO mode, per accepted report:
    fix, distance-from-home > GPS_REHOME_MIN_METERS, and successive accepted fixes
        stay clustered (each within GPS_REHOME_MIN_METERS of the first candidate)
        for GPS_SETTLE_SECONDS  [if a fix strays outside the cluster, restart the timer]
            → RE-HOME to the latest fix: write location.json (source="gps")
              → existing poll → _recenter()
    fix, near current home
            → nothing (we are home; do not chase jitter)
    no fix, elapsed < GPS_NOFIX_GRACE_SECONDS
            → keep last-known, status "searching"
    no fix, elapsed ≥ grace
            → one IP lookup:
                 IP within GPS_DIVERGENCE_KM of last-known → keep last-known silently
                 IP ≥ GPS_DIVERGENCE_KM from last-known    → divergence notice
                                                              (keep last-known + surface)
    fresh unit — no location.json AND no configured HOME_LAT/LON:
            no fix → IP bootstrap as provisional home, source="estimated", until GPS fixes
    fresh unit — no location.json but HOME_LAT/LON is set:
            no fix → show configured home, source="configured"; IP is only the divergence check

MANUAL mode:
    GpsClient still reads gpsd and publishes status (portal shows fix info),
    but never writes location.json — the manual pin stands.
```

**On persistent divergence** (no fix past grace + IP ≥ `GPS_DIVERGENCE_KM` from last-known): keep the
last-known *exact* location and surface a notice on the About screen and portal —
"GPS has no fix; your network suggests you're near \<city>, far from \<last location>." — with one-tap
**Use estimated** / **Set manually**. Rationale: a precise-but-maybe-stale location is less confusing
on a radar than a coarse guess, and nothing moves the center without the user's say-so. The user
resolves it in seconds.

**Threshold defaults** (all env-tunable): `GPS_REHOME_MIN_METERS=250` (past normal fix jitter, catches a
real relocation), `GPS_DIVERGENCE_KM=25` (comfortably past IP coarseness so ISP offset never
false-alarms), `GPS_NOFIX_GRACE_SECONDS=180`, `GPS_SETTLE_SECONDS=25`, `GPS_MAX_HDOP=5.0`,
`GPS_MIN_SATS=4`.

**Distance is computed in meters directly**, with a small units-independent haversine helper inside the
GPS module — **not** `overhead.haversine` (`overhead.py:157`), which returns miles-or-km depending on the
import-time `DISTANCE_UNITS` snapshot and would make re-home thresholds silently unit-dependent.

## Architecture

Fits the existing two-process model (display process + Flask portal subprocess, all cross-process state
via files under `FLIGHTSCNR_DATA_DIR`).

```
u-blox 7  ──NMEA/USB──►  gpsd (system daemon, localhost:2947, -n, USBAUTO)
                              │  JSON: TPV (lat/lon/mode/speed) + SKY (hdop/sats)
                              ▼
                         GpsClient  ── background thread in the DISPLAY process
                              │        (mirrors utilities/ais_client.py: a feed thread,
                              │         no pygame calls)
              ┌───────────────┼────────────────────────────┐
              ▼               ▼                             ▼
     gps_status.json   location.json (on settled       (About screen reads
     (portal reads)     re-home / IP bootstrap)          GpsClient in-memory)
                              │
                              ▼
             existing 2 s poll  _maybe_reload_location (app.py:1333)
                              ▼
                    _recenter(lat, lon, source)  ← NEW consolidated path
```

**gpsd** manages the device (hotplug via udev, re-enumeration, fix parsing). We do **not** add the
`python3-gps` Python library — `GpsClient` reads gpsd's plain JSON protocol over a socket
(`?WATCH={"enable":true,"json":true}`, newline-delimited JSON, keys `class` ∈ {TPV, SKY, DEVICES}),
consistent with the app already hand-parsing adsb.fi/FR24 JSON. `requirements.txt` is untouched; only
the apt-level daemon is added.

**gpsd config** (`/etc/default/gpsd`, written by `install-pi.sh`):
- `DEVICES="/dev/serial/by-id/usb-u-blox_AG_-_www.u-blox.com_u-blox_7_-_GPS_GNSS_Receiver-if00"`
  (stable path; won't shift when other USB devices appear)
- `GPSD_OPTIONS="-n"` (poll the device without waiting for a client to connect)
- `USBAUTO="true"` (udev hotplug — re-adds the receiver on replug, no restart)
- Stays bound to localhost. Never `-G`.

`install-pi.sh` adds `gpsd gpsd-clients` to its apt list, writes `/etc/default/gpsd`, and enables
`gpsd.socket`.

### The ride-along refactor: `_recenter(lat, lon, source)`

Three call sites re-home today and each invalidates a *different* subset — a latent bug GPS would make
a fourth of:

- `_save_map_pan` (`app.py:540`, touch pan) — invalidates map_bg + rainviewer, resets smoother, refreshes
  weather/tz, regrabs. **Misses AIS resubscribe.**
- `_maybe_reload_location` (`app.py:1333`, the 2 s poll) — invalidates map_bg, resets smoother, refreshes
  weather/tz, regrabs. **Misses rainviewer AND AIS.**
- Portal `/location/set` (`web/app.py`) — runs in the portal process, so it only warms the shared on-disk
  cache, not the display's in-memory surfaces.

Consolidate all display-side invalidation into one `_recenter()` method in `app.py`: `map_bg.invalidate`
+ `prewarm_all_scales`, `rainviewer_overlay.invalidate` + `request_overlay`, AIS resubscribe
(`sync_ais_client`), `position_smoother.reset`, the weather/tz refresh thread, and `overhead.grab_data`.
Route touch-pan, the location poll, and GPS through it. This fixes the existing rainviewer/AIS gaps for
free and gives GPS a single correct entry point. Scoped to code we are already touching — not a broad
refactor.

## Time and timezone

- **The system clock (UTC) comes from NTP over the internet.** This Pi 4 has no battery-backed RTC, so
  NTP is already the time source. GPS time is **never** used — which avoids the u-blox 7 week-rollover
  bug class entirely (and this unit's time was correct anyway).
- **The timezone follows GPS location automatically.** The app already auto-sets the system timezone
  from the radar center (`tz_lookup` → `maybe_apply_auto_timezone`, run inside the weather/tz refresh).
  Because that refresh is part of `_recenter`, a GPS re-home across a timezone boundary updates the
  clock's local offset with no extra work. NTP owns the instant; GPS-driven location owns the offset.

## Configuration (env, in `config.py` + `.env.example`)

| Var | Default | Meaning |
|---|---|---|
| `LOCATION_MODE` | `auto` | `auto` \| `manual` |
| `GPS_ENABLED` | `true` | master switch for the GpsClient thread |
| `GPSD_HOST` | `127.0.0.1` | gpsd socket host |
| `GPSD_PORT` | `2947` | gpsd socket port |
| `GPS_MIN_SATS` | `4` | fix-acceptance floor |
| `GPS_MAX_HDOP` | `5.0` | fix-acceptance ceiling |
| `GPS_REHOME_MIN_METERS` | `250` | move this far to trigger re-home |
| `GPS_SETTLE_SECONDS` | `25` | new position must hold this long |
| `GPS_NOFIX_GRACE_SECONDS` | `180` | no-fix tolerated before divergence check |
| `GPS_DIVERGENCE_KM` | `25` | IP-vs-last-known gap that raises the notice |
| `GEOIP_ENABLED` | `true` | allow the IP divergence/bootstrap lookup |
| `GEOIP_URL` | `https://ipwho.is/` | no-key HTTPS geolocation; called only on no-fix/fresh-unit |

## Data schemas

**`location.json`** (extended; backward-compatible — existing reader `config.py:174` still finds
`lat`/`lon`):
```json
{ "mode": "auto", "lat": 47.5899, "lon": -122.1186, "source": "gps", "updated_at": "2026-07-21T02:44:42Z" }
```

**`gps_status.json`** (new; written by GpsClient, rate-limited; read by the portal):
```json
{ "fix": "3d", "sats_used": 6, "hdop": 2.36, "lat": 47.5899, "lon": -122.1186,
  "speed_mps": 0.08, "device": "present",
  "divergence": { "active": false, "ip_city": null, "ip_lat": null, "ip_lon": null, "km": null },
  "updated_at": "2026-07-21T02:44:42Z" }
```
`fix` ∈ `3d|2d|none`; `device` ∈ `present|absent|no_daemon`.

## Surfaces

**Portal** (`web/app.py` + `templates/index.html`, Location section):
- Mode toggle Auto / Manual.
- In Manual: a lat/lon field pre-seeded with last-known.
- Status block: fix (3D/2D/none/no-device), satellites used, HDOP, current effective source.
- On divergence: the notice with **Use estimated** / **Set manually** actions.
- `GET /location/json` → `{mode, effective:{lat,lon,source}, gps:{...}, divergence:{...}}`.
- `POST /location/set` → handles mode changes, manual coords, and the two resolution actions.
  - "Use estimated" writes `location.json` with the IP coords, `source="estimated"`.
  - "Set manually" switches `mode="manual"` with the provided (or last-known) coordinate.

**On-device** (`screens/info.py`, the About screen, swipe-up): it already shows lat/lon
(`info.py:469-470`). Add a source line, a GPS fix-status line, and the divergence notice. No new gesture.

## Testing

Mirrors the project's "test the logic, not pygame" pattern (pure `utilities/` logic is well covered;
the pygame layer is not).

- **Unit — resolution state machine:** the decision logic is a pure class, separate from socket I/O.
  Feed synthetic TPV/SKY sequences; assert re-home / keep / diverge / bootstrap across the thresholds.
  This is the bulk of coverage and needs no hardware.
- **Unit — gpsd JSON parsing:** captured TPV/SKY report lines → parsed fix/hdop/sats.
- **Unit — distance & settling:** table tests around `GPS_REHOME_MIN_METERS`, `GPS_SETTLE_SECONDS`,
  `GPS_DIVERGENCE_KM`.
- **Fake gpsd:** a tiny socket server emitting canned JSON for an end-to-end-ish test with no receiver.
- **On-device:** real fix → re-home; unplug → status "no device" → replug → auto-recover (the hotplug
  path); manual mode ignores GPS. Verified via the `grim` screenshot loop + portal.

> **Test-suite hazard (pre-existing):** the suite is import-order fragile — `overhead.py` snapshots
> `DISTANCE_UNITS` at import (`overhead.py:23`) and `tests/test_overhead_utils.py:36` sets that env var
> at module scope, so a new test module that sorts alphabetically before it and imports the display
> stack will break `test_known_distance_new_york_los_angeles`. New GPS test files must either sort after
> `test_overhead_utils` or avoid importing the config/overhead chain at module scope. (See
> `docs/ARCHITECTURE-NOTES.md` §"Import-time value snapshots".)

## Failure modes

| Condition | Behavior |
|---|---|
| gpsd down / not installed | GpsClient reconnects with backoff; `device="no_daemon"`; Auto keeps last-known / IP |
| Receiver unplugged | gpsd emits device-gone; `device="absent"`; keep last-known; auto-recover on replug |
| IP lookup fails / offline | skip divergence detection; silently keep last-known (safe) |
| gpsd socket stalls | socket timeout on a daemon thread; shutdown never blocks |
| Partial / garbage JSON | skip the line |
| GPS time wrong (rollover) | irrelevant — GPS time is never used |

## Out of scope

- Continuous live tracking for a moving vehicle (use case C). The settle/hysteresis model is built for
  occasional relocation; live tracking would need different cadence and is a separate effort.
- On-device Auto/Manual toggle — the portal is sufficient; a touch toggle is deferred (YAGNI).
- Fixing the unrelated pre-existing bugs catalogued in `ARCHITECTURE-NOTES.md` (portal-reverts-settings,
  `MIN_ALTITUDE` snapshot), except the location-invalidation consolidation, which this work needs.
