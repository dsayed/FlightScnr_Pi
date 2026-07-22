# FlightScnr Pi — Architecture Notes

Working notes for our fork. Not upstream documentation — this is what we learned bringing the app up
on a headless Pi 4 and reading the code, written to support three planned changes: **make it smaller
and faster**, **add GPS**, and **ingest ADS-B locally from an SDR**.

- Code state: upstream `010f1d6`, `VERSION` 2026.7.20.6
- Measured on: Raspberry Pi 4 Model B Rev 1.1, 2 GB, Debian 13 Trixie, kernel 6.18.34 aarch64,
  Python 3.13.5, pygame 2.6.1 (SDL 2.32.4)
- Notes written 2026-07-20

---

## 0. Deployment as we run it

`planepi.local` / `192.168.20.64`, user `david`, key auth, passwordless sudo.

The Pi is **headless** — the round DSI panel has not arrived. It still has a full graphical stack:
`lightdm` autologs into **labwc** (Wayland), which creates a `NOOP-1` *headless output* and provides
`/tmp/.X11-unix/X0` via Xwayland. The app therefore runs unmodified on X11 exactly as it will with
the real panel.

We force the headless output to true panel geometry before the app starts:

- `/usr/local/bin/flightscnr-headless-mode.sh` — `wlr-randr --output NOOP-1 --custom-mode 720x720@60`
- `/etc/systemd/system/flightscnr.service.d/headless-720.conf` — runs it as `ExecStartPre`

Both are **no-ops once a real panel is attached** (no `NOOP` output ⇒ script exits 0). When the panel
arrives: set `DISPLAY_ROTATION=90` in `/etc/flightscnr.env` (we run `0` for the virtual display), add
the DSI overlay to `/boot/firmware/config.txt`, and optionally `rm -rf` the drop-in directory.

### Seeing the UI without a panel

The high-value trick. `grim` captures the Wayland output directly:

```bash
ssh planepi 'XDG_RUNTIME_DIR=/run/user/1000 WAYLAND_DISPLAY=wayland-0 grim /tmp/shot.png'
scp planepi:/tmp/shot.png .
```

Returns a 720×720 PNG of exactly what the panel would show. `xdotool` (with `DISPLAY=:0
XAUTHORITY=/home/david/.Xauthority`) simulates taps, so UI changes can be verified programmatically —
useful for before/after comparison during the performance work. Interactive VNC is also enabled
(`wayvnc.service` on `:5900`, TLS + PAM auth) but needs RealVNC Viewer, not macOS Screen Sharing.

**Simulating swipes:** they work, but the pointer must move in *paced* steps — `xdotool mousemove`
calls with ~20 ms sleeps between them. Jumping the pointer in one move, or with no delay, is not
recognized as a drag. Verified: swipe-left → Settings, swipe-down → Clock, swipe-right → Track all
navigate correctly this way. Swipe threshold is 46 px (`max(26, SIZE*0.065)`,
`input_handler.py:77`).

```bash
xdotool mousemove 560 360 mousedown 1
for x in 540 510 480 450 420 390 360 330 300 270 240 210 190; do xdotool mousemove $x 360; sleep 0.02; done
xdotool mouseup 1
```

**Screens auto-return to radar on a timeout** (the green ring is the countdown), so capture the
screenshot within ~1 s of the gesture or you will photograph the radar and think the swipe failed.

### Raspberry Pi Connect conflicts with the pinned resolution

`rpi-connect` screen sharing is active and signed in — browser-based interactive access via
connect.raspberrypi.com, no client install, and it drives real pointer input so **gestures work
naturally there**. But a Connect screen-sharing session **resizes the headless output** to the client
viewport and it does not return to 720×720 on disconnect (observed reverting to the 1280×673 default;
correlate with `rpi-connect[...] [vnc]: Proxy session started/ended` in the journal).

So the two tools trade off:
- **Connect** — best for interactive gesture testing; geometry is not panel-accurate.
- **grim + xdotool** — panel-accurate 720×720; best for screenshots and automated verification.

After any Connect session, re-run `sudo /usr/local/bin/flightscnr-headless-mode.sh` before trusting
screenshot geometry. Always confirm captures are actually 720×720 (`file shot.png`) — a mis-sized
capture silently invalidates any coordinate-based interaction test.

### Config precedence, briefly

Everything comes from env vars; `config.py` holds no user-facing defaults (`config.py:1-11`).

| Key class | Precedence (highest first) |
|---|---|
| API keys, `SHOW_AIRLINE_LOGOS`, `VESSEL_*` | `/etc/flightscnr.env` → `secrets.json` → `config.h` |
| **Effective radar center** | **`location.json`** → zone corners → `HOME_LAT`/`HOME_LON` |
| `MIN_HEIGHT`, `DISTANCE_UNITS`, scale, map style | env **only on first run**, then `round_touch_settings.json` owns them (`settings.py:89-102`) |
| Everything else | env at import; no runtime path changes it |

`location.json` outranking the env file is the one place portal state beats `/etc/flightscnr.env`
(`config.py:200-213`).

---

## 1. Process topology

`flightscnr.py:86` launches the Flask portal via `subprocess.Popen` — **a second Python interpreter**,
sharing no memory. Every shared module (`config`, `settings`, `off_hours`, `overhead`, …) is imported
twice with independent state. All cross-process coordination happens through files in
`/var/lib/flightscnr/`. This single fact explains most of the surprising behaviour in §4.

The portal is a *child* of the display process, so if the UI dies the portal dies with it.

Within the display process: pygame runs single-threaded, and `Overhead.grab_data()` spawns a fresh
**daemon thread** per cycle (`overhead.py:737`).

---

## 2. Ingestion — one cycle of `Overhead._grab()`

Entry `overhead.py:747`. HTTP order per cycle:

**FR24 live feed → ≤5 FR24 detail calls → (0-5) adsbdb → adsb.fi → FR24 find_by_callsign → FR24
details → (maybe) AirLabs.** AIS is *not* in this pipeline; it's a separate WebSocket thread merged
in at the UI layer (`app.py:190-192`).

- **STEP 1** (`overhead.py:775-1003`) — FR24 zone feed for the current bbox, altitude-gated, sorted by
  distance. Only the **5 nearest** get a details call (`MAX_FLIGHT_LOOKUP = 5`, `overhead.py:81`), but
  `_index_zone_flights_by_callsign` (`overhead.py:313`) indexes the *whole* zone for later enrichment.
- **STEP 1b** (`overhead.py:1004-1134`) — adsb.fi positions merged in. See below.
- **STEP 2** (`overhead.py:1136-1231`) — tracked flight, with a miss/ETA state machine and an optional
  AirLabs schedule fallback for not-yet-airborne flights.
- **Publication** (`overhead.py:1270-1273`) — under `_lock`, rebinds `self._data` to a new list and
  bumps `_grab_seq`. On *any* exception it publishes an empty list deliberately, so the UI never
  freezes on stale data.

Anonymous FR24 access works without an API key and still returns routes and details — better than the
README implies. That's what we're running on.

### The merge/dedupe algorithm (the part worth knowing)

Two passes. **Pass A**, incremental, in `_grab` (`overhead.py:1057-1131`): for each adsb.fi entry, find
a matching FR24 entry in strict priority order —

1. ICAO hex exact (`overhead.py:1074`)
2. Identity key — `hex:` / `reg:` / `cs:` namespaced, `aircraft_alert.py:100-118`
3. Callsign alias (IATA→ICAO expanded, `aircraft_alert.py:78-93`)
4. **Geographic proximity fallback** (`overhead.py:1082-1107`): reject > 1.2 km; accept if ≤ 0.45 km,
   *or* same aircraft type, *or* shared identity

On a match, `merge_live_fields` copies the ADS-B side over `_LIVE_FIELDS` (`overhead.py:1018-1022`):
position, altitude, heading, speeds, squawk, hex, registration, callsign, type. FR24-only fields
(origin, destination, airline, trail, times) are never touched.

**So: adsb.fi wins on kinematics, FR24 wins on route/identity metadata.**

**Pass B**, `dedupe_flights` (`aircraft_alert.py:139-224`), is a global O(n²) sweep with a `richness()`
score (`+10` route, `+5` non-adsb_fi source, `+3` airline, `+2` hex…) choosing the survivor.

Two sharp edges worth remembering:
- `merge_live_fields` skips *falsy* values only for identity-ish fields; a `0` heading or ground speed
  from adsb.fi **does** overwrite FR24's value (`aircraft_alert.py:131-138`).
- There's no timestamp anywhere in the merge — **last writer wins**. This matters directly for SDR
  (§6).

### Entry shape

FR24 entries carry ~30 keys; adsb.fi entries carry **15** and have `origin`/`destination` as `""`.
An ADS-B-only entry that never gets feed-enriched has **no `distance`, no `direction`, no `trail`, no
time fields** — downstream code must use `.get()` defensively.

### Concurrency hazards

- `grab_data()` has **no internal guard** against overlapping cycles. The only protection is a
  check-then-act TOCTOU on `overhead.processing` at `app.py:1363`, and four call sites can race it.
- `peek_data()` shallow-copies the *list*, not the dicts (`overhead.py:1489`). The UI can read a dict
  mid-mutation — e.g. latitude updated, longitude not — for a one-frame position glitch.
- Tracked-flight state is read/written entirely outside `_lock` (`overhead.py:1139-1187`); overlapping
  cycles can double-increment the miss counter and trigger a premature auto-wipe.
- The worker thread reads *UI state* (`scale.search_radius_nm(settings.scale_index())` at
  `overhead.py:786`), so a pinch mid-cycle can desync the FR24 bbox from the adsb.fi radius.

### Import-time value snapshots (a recurring smell, two known instances)

`overhead.py` binds config values **by value** at import, so later changes to `config.*` never reach
it. Two confirmed instances:

- **`DISTANCE_UNITS`** (`overhead.py:23-28`, used by `haversine` at `overhead.py:174`). This makes the
  *test suite itself* import-order dependent: `tests/test_overhead_utils.py:36` does
  `os.environ.setdefault("DISTANCE_UNITS", "imperial")` at module scope before importing overhead, so
  any test module that sorts earlier alphabetically and transitively imports `config` first will bind
  `overhead.DISTANCE_UNITS` to the `"metric"` default and make `test_known_distance_new_york_los_angeles`
  fail (expects ~2451 miles, gets ~3974 km). Verified empirically: adding a `test_navigation_*.py`
  file was enough to break it. **Adding any new test module whose name sorts before `test_overhead_utils`
  and which imports the display stack will break that test.**
- **`MIN_ALTITUDE`** — see below.

Fixing either properly means reading `config.X` at call time rather than importing the name.

### Known bug: `MIN_ALTITUDE` is frozen at boot

`overhead.py:45` does `from config import MIN_ALTITUDE` — binding an **int by value**. When the user
changes min height, `settings._sync_config_min_height()` updates `config.MIN_ALTITUDE`, but overhead's
copy never rebinds. The fetch-time filters (`overhead.py:790`, `overhead.py:1016`) keep the boot-time
floor for the life of the process. Contrast `LOCATION_HOME`, which is a *mutable list* and therefore
does propagate — see §5.

---

## 3. Render loop and measured performance

`RoundTouchDisplay.run()` at `app.py:1388`. **There is no dirty-rect model** — every draw repaints the
full 720×720 surface, applies the bezel, and presents.

Frame pacing (`app.py:1533-1569`) is per-screen. On radar, `frame_ms = SWEEP_FRAME_MS (16)` when the
sweep line is on, else `50` — i.e. **62 fps with the sweep, 20 fps without**. The loop itself is a
busy-poll with `sleep(0.001)` (`app.py:1577`), so the unconditional tail ticks at `app.py:1571-1574`
run *hundreds* of times per second, far above the frame rate.

### Measured profile — py-spy, 30 s, 100 Hz, radar screen, ~25 aircraft

Sustained **77% of one core** and **266 MB RSS** (314 MB including the Flask child).

| Self time | Location | What |
|---|---|---|
| **21.4%** | `screens/radar.py:326` | `font.render()` per tag line, per aircraft, **per frame** |
| **11.9%** | `draw.py:270` | `apply_round_bezel` — full-screen SRCALPHA blit |
| **10.0%** | `app.py:275` | `_present` — blit + `flip()` |
| 7.4% | `aircraft_type_icons.py:240,242` | `transform.rotate()` per aircraft per frame |
| 4.5% | `app.py:1577` | the busy-poll sleep |
| 3.3% | `radar.py:328,330` | tag blits |
| **~12% combined** | `off_hours.py:92`, `overhead.py:471`, `genericpath` `getmtime`/`exists`, `json` | **file I/O + JSON parsing in the render loop** |
| 0.9% | `draw.py:173` | `draw_sweep_line` |

**The sweep animation is not the bottleneck** — it is 0.85% directly. But it sets the 16 ms frame
budget, so it *multiplies* everything else by ~3× versus the no-sweep path, and forces the 10×
busy-poll branch. It's an amplifier, not a cost.

Three findings deserve emphasis:

1. **Text rasterization is the single largest cost.** `_draw_aircraft_tag` (`radar.py:290-330`) calls
   `font.render()` three times per aircraft with no cache — ~75 rasterizations per frame, ~4,600/s.
   Tag text changes at data-poll rate (0.5 Hz), so a `(text, color, font)` cache would hit >99%.
2. **~12% of CPU is filesystem I/O in the draw path.** `off_hours._load()` (`off_hours.py:87-89`)
   opens and parses JSON on *every* call and is reached twice per loop iteration.
   `load_tracked_callsign()` (`overhead.py:468`) opens and parses `tracked_flight.json` **once per
   aircraft per frame** via `_is_tracked` (`radar.py:349`) — ~1,550 file reads/second.
   `alert_prefs.reload()` stats the file per aircraft too. None of this needs to be faster than 1 Hz.
3. **The bezel blends a full 518k-pixel SRCALPHA surface** when only the ~23% outside the circle is
   non-transparent.

### Memory

- **~26 MB**: all 7 map scales prewarmed eagerly into RAM at startup (`app.py:156`,
  `map_bg.py:604-630`); each is a 970² RGBA surface (~3.8 MB) and nothing evicts inactive scales.
- **Suspected leak**: `rainviewer_overlay._surfaces` is keyed by `(lat, lon, scale, frame_time)` and
  `_prune_old_cache` deletes **only the on-disk PNGs** (`rainviewer_overlay.py:247-269`). A new
  `frame_time` appears every ~10 min at ~2 MB each ⇒ roughly **12 MB/hour of uptime**. This is the
  leading hypothesis for RSS being 266 MB rather than ~100 MB. *Unverified — needs an uptime RSS
  trend to confirm.*
- Aircraft icon source PNGs are **1024×1024** (18 MB on disk) and scaled to ~63 px at runtime, causing
  4 MB transient decodes plus a pure-Python per-pixel `_colorize` loop
  (`aircraft_type_icons.py:180-189`).

### Optimization order (impact ÷ risk), grounded in the profile above

**Tier 1 — measured, low risk, no behaviour change**
1. Cache rendered tag text, keyed `(text, color, font)` with bounded LRU — targets the 21.4%.
2. mtime-guard `off_hours._load()`; hoist `load_tracked_callsign()` out of the per-aircraft loop;
   time-guard `alert_prefs.reload()` — targets the ~12% I/O.
3. Rate-limit the tail ticks at `app.py:1571-1574` to ~2 Hz. Also delete the duplicated `_safe_draw()`
   at `app.py:1519-1520` (a free whole extra frame).
4. Cache rotated icons by 5°-quantized heading — targets the 7.4%.
5. Blit only the four bezel corners — targets ~75% of the 11.9%.

**Tier 2 — bigger, riskier**
6. `SWEEP_FRAME_MS` 16 → 33 (30 fps). One line; halves *everything*. The 16-step fade trail
   (`draw.py:145`) exists precisely to make lower frame rates look smooth. Purely perceptual risk —
   judge on real hardware.
7. Skip the `rotation.present` full copy when rotation is 0 (`rotation.py:44-50`).
8. Dirty-rect the sweep against a cached static frame. Largest available win, highest risk —
   interacts with bezel, alert pulsing, timeout ring, and the rotation path.

Tier 1 alone plausibly addresses over half the measured CPU without touching rendering semantics.
Do it before anything structural, and re-profile between steps.

---

## 4. Two-process state sharing — where it bites

All coordination is via files in `/var/lib/flightscnr/`. Sync mechanisms are **inconsistent**:

| Mechanism | Files |
|---|---|
| mtime polling | `location.json` (display polls at 2 s), `alert_prefs.json` |
| value-snapshot polling | `round_touch_settings.json` (display polls at 0.5 s) |
| read-through, every access | `weather_prefs.json`, `off_hours_prefs.json`, secrets toggles |
| sentinel file | `round_touch_settings.reload` |
| **nothing at all** | `close.txt`, `farthest.txt`, `flight_counter.json`, `tracked_flight.json` |

Atomic (tmp + `os.replace`): `location.json`, `round_touch_settings.json`, `alert_prefs.json`,
`weather_prefs.json`, `off_hours_prefs.json`, `secrets.json`.
**Non-atomic** (plain truncate+write, torn reads possible): `close.txt`, `farthest.txt`,
`flight_counter.json`, `tracked_flight.json`.

### The significant defect: the portal never re-reads settings

`web/app.py` **never calls `settings.reload()`**. The portal's `settings._state` is whatever it loaded
when the Flask process started.

- `GET /display/json` and `GET /radar/json` serve **boot-time values**, ignoring every on-device change.
- Worse, `POST /radar` / `POST /display` call `settings.set_*`, each of which writes the *entire*
  stale dict back to disk. **Changing one field in the portal silently reverts every setting the user
  changed on the touchscreen since the portal started.** Last-writer-wins over a whole document.

The reverse direction is mostly fine because the display polls at 0.5 s — except while a slider drag
holds `_disk_synced == False` (`settings.py:258`), during which portal writes are dropped from memory
and later overwritten.

Also: API keys are frozen in the display process at import (`config.py:94-97`), so key changes need a
restart — the portal says so. And `/system/*` plus `/updates/apply` are **unauthenticated** on the LAN
(`web/app.py:685`, no auth or CSRF anywhere), meaning anyone on the network can reboot the Pi or
trigger an update.

---

## 5. The location seam (GPS)

**`LOCATION_HOME` is a mutable list, mutated in place** by `_apply_home()` (`config.py:136-137`), and
`ZONE_HOME` uses `.clear()`/`.update()` rather than reassignment. So every
`from config import LOCATION_HOME` alias sees updates for free — including
`overhead.LOCATION_DEFAULT` (`overhead.py:55`), `geo.py:6`, `screens/info.py:12`, `map_generator.py:4`.
Someone designed for a moving radar center.

There is already a cross-process reload path: `reload_location_override()` (`config.py:174`) polls
`location.json`'s mtime. The display calls it every 2 s (`app.py:1338`); the portal calls it on three
endpoints. Writers are the portal (`web/app.py:215`) and the display's touch-pan (`app.py:552`).

*(Correcting an earlier assumption of ours: the display **does** poll for location changes. What's
actually broken is the invalidation, below.)*

### What a GPS feed must invalidate

The invalidation logic is **duplicated in three places that disagree** — `app.py:553-570` (touch pan),
`app.py:1339-1355` (location poll), `web/app.py:216-232` (portal).

| Downstream state | Reacts automatically? | Needs |
|---|---|---|
| Radar search zone, distances, screen projection | **Yes** (alias) | — |
| Map tile background | No — cached by `(lat, lon, scale, style)` | `map_bg.invalidate()` + `prewarm_all_scales()` |
| **RainViewer overlay** | No | `rainviewer_overlay.invalidate()` — **missing from the location-poll path** (`app.py:1333-1355`) |
| **AIS subscription box** | No — pinned at configure time | `sync_ais_client()` — **also missing from the poll path** |
| Weather cache (30 min TTL, not location-keyed) | No | `weather_data.after_radar_center_changed()` |
| Timezone | Explicitly reset; issues a **system-wide** `timedatectl` write | `tz_lookup.invalidate_cache()` |
| Persisted `close.txt` / `farthest.txt` distances | No — computed against the old home | nothing does this today |

**Design implication.** `set_location_home()` (`config.py:163-171`) is the natural insertion point — it
persists atomically and self-suppresses its own mtime re-trigger — but it performs **no side-effect
invalidation at all**. Before GPS multiplies the number of callers, consolidate invalidation behind
one hook (e.g. the `changed == True` branch of `_apply_home`, `config.py:147`). That removes a whole
class of bug rather than adding a fourth divergent copy.

Two more GPS-specific notes: `_save_location_file` does a tmp-write + `os.replace` + `chmod` **per
update** — at GPS cadence that's a rename and chmod per fix on an SD card, so rate-limit or keep GPS
updates in memory. And `_apply_home` hardcodes `LOCATION_SOURCE = "portal"`; GPS wants its own
sentinel or the UI will claim the position was set via the web portal.

---

## 6. Local SDR ingestion

**The parser already exists.** `_to_entry()` (`adsb_client.py:60-116`) consumes exactly the
readsb/tar1090 schema — `hex`, `flight`, `t`, `ownOp`, `lat`, `lon`, `alt_baro`/`alt_geom`, `gs`,
`track`, `baro_rate`, `squawk`, `dbFlags`. `dump1090-fa`/`readsb` serve that same shape at
`http://localhost:8080/data/aircraft.json`. The only schema difference: readsb nests under `aircraft`,
adsb.fi under `ac`.

`fetch_aircraft_entries()` has **exactly one caller** (`overhead.py:1011`), so the blast radius is tiny.

**Recommended shape:**

1. `fetch_local_entries(url, lat, lon, radius_nm, min_altitude)` in `adsb_client.py`, reusing
   `_to_entry`. Needs its own cache slot — the module global `_CACHE` (`adsb_client.py:10`) is a
   single shared dict — with ~1 s TTL, and a **local distance filter**, since a local receiver serves
   everything it hears rather than a server-side radius.
2. `LOCAL_SDR_ENABLED` / `LOCAL_SDR_URL` in `config.py` beside `ADSB_ENABLED`, plus the fallback block
   at `overhead.py:49-63` so tests without config still import.
3. Feed local entries into the **existing** `adsb_entries` list before the merge loop
   (`overhead.py:1012-1017`), pre-deduped. The merge machinery at `overhead.py:1057-1134` then needs
   **no changes at all** — it already matches any dict with hex/callsign/position.

**Two traps:**

- **Freshness.** `merge_live_fields` has no timestamp concept — last writer wins. A local receiver is
  sub-second fresh while FR24's feed can be 90 s stale, so local entries must be ordered last, or
  gated on a `seen_ts` (readsb provides `seen_pos`).
- **`richness()` scoring.** `aircraft_alert.py:148` awards `+5` to any `data_source != "adsb_fi"` — a
  `"local_sdr"` string would inherit that bonus for free and wrongly outrank FR24 records. Make it an
  explicit set membership test.

Because local positions are *more* accurate than FR24's, the 0.45/1.2 km proximity thresholds
(`overhead.py:1101-1103`) may actually be too loose rather than too tight.

---

## 7. Test suite

`225 tests: 207 pass, 4 fail, 18 in an isolated module.` Run from `flightscnr/`:
`../flightscnr-venv/bin/python -m pytest tests -q` (needs `pip install pytest`).

**The suite cannot be run as a whole without `--ignore`.** `tests/test_gesture_handler.py:47-50`
replaces `sys.modules["display"]` and `sys.modules["display.round_touch"]` with bare
`types.ModuleType` stubs **at import time** and never restores them. Bare module objects have no
`__path__`, so every later import of a `display.round_touch.*` submodule fails with "unknown
location". Damage is alphabetical: `test_scale`, `test_vessel_declutter`, and `test_weather_forecast`
all sort after "g". Each passes in isolation.

The correct pattern is already used elsewhere in the same suite —
`monkeypatch.setitem(sys.modules, ...)` inside a fixture (`test_weather_forecast.py:78`).

Four genuine pre-existing failures, all tests lagging behind code:
- `test_get_airport_coords_iata` / `_case_insensitive` — code now returns an extra `'name'` key
- `test_route_display_one_line` — route formatting now returns 2 lines, test expects 1
- `test_refresh_invalidates_on_date_change` — same `sys.modules` stubbing issue on `utilities.temperature`

---

## 8. Baseline for comparison

Recorded 2026-07-20, radar screen, ~25 aircraft, sweep on, 720×720, adsb.fi + anonymous FR24:

| Metric | Value |
|---|---|
| CPU | 77% of one core (~19% of 4 cores) |
| RSS (display) | 266 MB |
| RSS (incl. Flask child) | 314 MB |
| Pipeline cycle | 169–728 ms (2 s interval) |
| SoC temp | 50.6 °C idle → 60.3 °C loaded, `throttled=0x0` |
| Repo on disk | 155 MB clone (`logo.zip` is 31 MB of it), ~2× after logo extraction |

Raw profile: `py-spy record -f raw` output saved outside the repo during bring-up; regenerate with
`sudo ./flightscnr-venv/bin/py-spy record -o /tmp/prof.folded -f raw --pid $(pgrep -f flightscnr.py) --duration 30 --rate 100 --nonblocking --threads`
