# HDMI Panel Bring-up — Findings & Architecture Analysis

Working notes from bringing FlightScnr up on the **HDMI variant** of the round
touch panel (we ordered the wrong one — HDMI + USB touch instead of the DSI/I²C
panel the code was written for). Written 2026-07-24 against branch
`feat/gps-auto-location` (VERSION `2026.7.22.4`, base commit `c99582a`).

This documents seven reported issues, their root causes, the fixes (each a
separate PR into `feat/gps-auto-location`), and — more durably — the
architecture we learned while chasing them. Companion to
[`ARCHITECTURE-NOTES.md`](./ARCHITECTURE-NOTES.md).

---

## 0. TL;DR

| # | Reported symptom | Root cause | Status |
|---|------------------|------------|--------|
| 7 | "Update available" after we fast-forwarded | Pi's `origin` = **upstream**, not our fork; feature branch never deployed | Fixed (deploy) |
| 5 | Altitude ranges may not work | Stale partial code on device (no `max_height` at all) | Fixed by deploy; verified live |
| 1 | Auto GPS "doesn't stick" | Working correctly; `source` only flips to `gps` after a >250 m re-home, so a stationary unit shows "Manual" | UX fix (PR) |
| 4 | nm presets are fractional | Range bands hardcoded in **statute miles**; nm/km derived by conversion | PR |
| 6 | Brightness does nothing | HDMI has no `/sys/class/backlight`; `apply_percent()` silently no-ops | PR (software dim) |
| 2 | Adjusting range **crashes** | `UnboundLocalError` — a shadowing local import in `_apply_display_row` | PR |
| 3 | No pinch-to-zoom | Touchscreen is **pointer-emulated**; app gets zero SDL `FINGER` events (both x11 and Wayland) | Diagnosed, unresolved |

**The umbrella cause.** Issues 7, 5, and the *severity* of 2 all trace to one
thing: the Pi was running a **stale, partially-copied snapshot** of the work,
not the committed feature branch. See §2.

---

## 1. The seven issues in detail

### #7 — "A later version is available" (deployment topology)

The portal's update check was **correct**: the Pi was on upstream `main` @
`010f1d6` (`Release 2026.7.20.6`), while our work lived on
`feat/gps-auto-location` @ `c99582a` (`2026.7.22.4`).

The fast-forward happened on the Mac, never on the Pi. And the Pi's `origin`
points at **`yashmulgaonkar/FlightScnr_Pi` (upstream)**, so `git fetch origin`
would never see our branches — they're on the fork `dsayed/FlightScnr_Pi`. We
added a `fork` remote and deployed:

```bash
git remote add fork https://github.com/dsayed/FlightScnr_Pi.git
git fetch fork feat/gps-auto-location
git checkout -B feat/gps-auto-location fork/feat/gps-auto-location
```

The updater (`utilities/updater.py`) compares the checked-out release/tag against
origin tags; nothing was wrong with it.

### #5 — Altitude ranges "may not be working"

On the stale snapshot, **max-altitude did not exist**: `config.py` hardcoded
`MAX_ALTITUDE_FT = 100000` and `settings.py` had no `max_height_ft` at all. So
testing max-altitude on the old code did nothing.

On the deployed branch it works end-to-end. Verified live: setting
`max_height_ft=8000` via the portal, the polling loop's debug line changed to
`(min=1500ft, max=8000ft)` within ~8 s, and `min` already tracked
`min_height_ft`. See §3.2 for *why* the propagation is subtle (a stale
`from config import MIN_ALTITUDE` that is masked by a second, live filter).

### #1 — Auto GPS "doesn't stick"

GPS is **working correctly** — live `gps_status.json` showed
`fix=3d, sats_used=7, hdop=0.8, device=present`. The confusion is the About
screen showing **`Source: Manual`**.

`source` is the *provenance of the stored home coordinates*, not "is GPS live".
The Auto resolver (`utilities/gps_resolver.py`) only flips `source` to `gps`
after it **re-homes** — i.e. the fix moves `> GPS_REHOME_MIN_METERS` (default
**250 m**) from the current home and then *settles* for `GPS_SETTLE_SECONDS`.
Our fix sits ~3.5 m from the manually-set home, far under 250 m, so it never
re-homes and `source` stays `manual` forever. The `mode` field (`auto`) was
never shown, so there was no on-screen signal that GPS was active.

**Fix (PR):** make the mode explicit and say what GPS is doing —
`Mode: Auto · GPS locked` (live fix agreeing with a manual home),
`Mode: Auto · GPS set location` (actually re-homed), `Mode: Auto · acquiring
fix`, `Mode: Manual`.

### #4 — nm range presets are fractional

`display/round_touch/scale.py` defined a **single** band table in statute miles:

```python
SCALE_BANDS = [_band(m) for m in (2, 3, 5, 8, 10, 20, 30)]   # miles
```

Nautical-mile and kilometre values were derived by conversion, so nm showed
`1.7, 2.6, 4.3, 7.0, 8.7, 17.4, 26.1` and the portal hint literally said "mi".

**Fix (PR):** unit-aware bands — each unit has its own round preset tuple, all
the same length so the persisted `scale_index` stays valid across a unit switch;
only the *physical* size of the index changes (`10 mi` → `10 nm` on toggle). See
§3.3 for the design and its cache-invalidation requirement.

### #6 — Brightness does nothing (the HDMI theory was right)

`/sys/class/backlight/` is **empty** on this Pi — an HDMI monitor exposes no
backlight control. `display/round_touch/backlight.py::_backlight_paths()`
returns `[]`, so `apply_percent()` silently returns `False`. This also silently
broke off-hours dim and display-off.

**Fix (PR):** a software-dim overlay. When there is no hardware backlight,
`_present()` composites a translucent-black surface over the display. Compositing
black at `alpha = (100-pct)/100*255` scales content luminance by `pct/100`, so
brightness-% maps linearly to luminance; `pct=0` → full black (the HDMI
equivalent of "off"). Gated on `backlight.has_hardware()` — **DSI panels are
untouched**. See §3.4.

### #2 — Adjusting the range crashes the app

Reproduced on-device (see §4 for the technique). Tapping the **Range** row on the
Display settings page:

```
ERROR: Display loop crashed
UnboundLocalError: cannot access local variable 'rainviewer_overlay'
  where it is not associated with a value
  File ".../display/round_touch/app.py", line 560, in _apply_display_row
```

The `precipitation` branch of `_apply_display_row` did a **local**
`from display.round_touch import rainviewer_overlay`. In Python a name assigned
anywhere in a function is local for the *entire* function, so the earlier `range`
branch — which reads the module-level `rainviewer_overlay` — hit an unbound
local and threw. The exception propagated out of the display loop; systemd
(`Restart=always`) bounced the process, so it read as "the app crashes".

`rainviewer_overlay` is already imported at module scope (`app.py:28`); the local
re-import was pure redundancy.

**Fix (PR):** delete the redundant local import. Note this bug was **not** on the
stale snapshot's version of `app.py` — it's real on the feature branch, which is
why reproducing on the *deployed* code mattered.

### #3 — No pinch-to-zoom (diagnosed, unresolved)

The panel is genuinely multitouch: evdev shows `ABS_MT_SLOT` /
`ABS_MT_POSITION_X/Y` / `ABS_MT_TRACKING_ID`, and `libinput list-devices`
reports `Capabilities: touch`. `pinch_handler.py` is a complete, **frozen**
implementation driven by SDL `FINGER` events.

But with `TOUCH_DEBUG=1` and **real two-finger input on the panel**, the app
received **zero** `FINGERDOWN`/`FINGERMOTION` events — every touch, including a
genuine pinch, arrived as `MOUSEBUTTONDOWN`/`MOUSEMOTION`. Two fingers down
produced two mouse-downs collapsed onto a single pointer.

This held under **both** SDL video drivers:
- `SDL_VIDEODRIVER=x11` (through Xwayland, the documented default): mouse only.
- `SDL_VIDEODRIVER=wayland` (native labwc): rendering + single-finger tap/swipe
  work perfectly, but pinch still produced mouse-only events.

So the touchscreen is **pointer-emulated through the whole stack** — labwc /
wlroots is presenting the seat to the client as `wl_pointer`, not `wl_touch`.
The frozen pinch handler is starved of the events it needs; this is a
compositor/driver input problem, not app code. Left unresolved (reverted to the
x11 baseline). Investigation avenues in §5.

---

## 2. The stale-deploy hazard (root cause behind #7, #5, #2-severity)

The single most important lesson. The Pi deploys by **checking out git branches
in place**, and before this session it held a hand-copied mix: the new GPS
*files* were present (untracked), but modified tracked files (`settings.py`,
`config.py`, `app.py`, `web/app.py`) were **older revisions** or the pristine
upstream version.

The trap is that the mix was *internally consistent* — the portal answered
`/radar/json` with HTTP 200, no `AttributeError` — so nothing announced that the
device was running a different program than the one under test. `git status`
showing untracked `gps_resolver.py` next to an **unmodified** `settings.py` was
the only tell.

Consequences we saw:
- #7: version genuinely behind.
- #5: max-altitude code simply absent.
- #2: we nearly concluded "no crash" from reading the *stale* `app.py`, which
  did not have the bug. The crash only reproduces on the deployed feature branch.

**Recommendation:** deploy by `git fetch fork <branch> && git reset --hard
fork/<branch>` (never file copies), and after deploy run the test suite
on-device (`flightscnr-venv/bin/python -m pytest tests`). Config lives in
`/etc/flightscnr.env` and runtime state in `/var/lib/flightscnr/*.json`, neither
touched by checkout, so a hard reset is safe for settings/keys.

---

## 3. Architecture analysis

### 3.1 Two-process model, and why pygame crashes in one of them

`flightscnr.py` forks two processes:

- **Display process** — owns the X11/SDL display (has `/dev/dri` + framebuffer
  fds), runs the pygame render loop *and* the aircraft polling (`overhead.py`).
- **Portal process** — `web/app.py`, the Flask settings portal. **No pygame
  display.**

This split explains a class of log noise: any code path in the *portal* process
that calls a pygame surface op (e.g. `map_bg.request_background()` →
`pygame.image.load(buf).convert()`) raises
`pygame.error: cannot convert without pygame.display initialized`. It's caught
and logged (`Radar map background fetch failed`), non-fatal, but it means **the
portal must never do rendering work** — only write settings/state that the
display process picks up. When we needed to invalidate tile caches on a unit
change (§3.3), we deliberately did *not* trigger a re-fetch from the portal
process for this reason; the display's draw loop re-requests on its next frame.

### 3.2 Cross-process settings sync (and a masked staleness bug)

Settings are a JSON file (`/var/lib/flightscnr/round_touch_settings.json`) plus
an in-memory `_state`. The **portal** writes the file; the **display** process
calls `settings.reload()` periodically and on a `.reload` sentinel file. `reload`
re-derives a few `config` module attributes from the saved settings, e.g.
`config.MIN_ALTITUDE = min_height_ft()` and `config.MAX_ALTITUDE_FT =
max_height_ft()`.

There is a latent **`from x import y` staleness** bug hiding here, worth
understanding because it *looks* broken but isn't:

- `overhead.py` does `from config import MIN_ALTITUDE` at module load — a
  **copy** of the value, bound once.
- `settings._sync_config_min_height()` does `config.MIN_ALTITUDE = h` — it
  mutates the **config module attribute**, not `overhead`'s copy.
- So `overhead.MIN_ALTITUDE` is frozen at its import-time value.

It works anyway because the actual filter in `adsb_client._to_entry()` calls
`config.passes_altitude_filter(alt_ft)`, which reads `config.MIN_ALTITUDE` /
`config.MAX_ALTITUDE_FT` **freshly** — and those *are* the attributes `reload`
updates. The stale copy passed as a function argument is dead-lettered by a
second, live gate. Fragile but functional; a future cleanup should read
`config.MIN_ALTITUDE` at the call site rather than importing the name.

**Takeaway:** in this codebase, prefer `import config; config.X` over
`from config import X` for anything mutated at runtime.

### 3.3 Unit-aware range bands (the #4 design)

All consumers reference bands **by index** (`scale.SCALE_BANDS[i]`,
`scale.active_index()`), and only `scale_index` is persisted — never a physical
distance. That constraint is what makes per-unit bands tractable: keep all three
unit tuples the same length, and index `i` stays valid across a unit switch;
only its *physical* meaning changes.

`scale.set_units()` is the single seam that swaps the active table. The one
non-obvious requirement: **tile caches keyed by `scale_index` alone
(`map_bg`, `rainviewer_overlay`) must be invalidated on a unit change**, because
index `i` now maps to a different physical km. That invalidation must happen in
the **display** process (via `reload()` / the on-device unit toggle), not the
portal — see §3.1.

### 3.4 Brightness abstraction (DSI vs HDMI)

`backlight.py` writes `/sys/class/backlight/*/brightness`. This is a clean
hardware abstraction that simply has **no backend on HDMI**. The software-dim
fix adds a second backend at the compositing layer (`_present()`), selected by
`has_hardware()`. Two nice properties fall out:

- `_present()` is the single chokepoint every screen flows through, so dim is
  uniform with no per-screen work.
- Off-hours dim / display-off route through the same `_apply_brightness` →
  `_brightness_pct`, so they start working on HDMI for free.

Note `self.surface` (the logical 720×720) is left pristine; the overlay is
applied to `self._display` *after* `rotation.present`, so it can't accumulate
across frames.

### 3.5 GPS resolution (issue #1 semantics)

`utilities/gps_resolver.py` is a **pure** state machine — no sockets/threads/IO,
everything injected — which makes it fully unit-testable. Key thresholds
(`ResolverConfig`): `min_sats=4`, `max_hdop=5.0`, `rehome_min_m=250`,
`settle_s=25`, plus a no-fix→IP-divergence path.

The mental-model mismatch behind #1: **`source` describes where the home came
from, `mode` describes what the user selected.** A perfect stationary fix never
changes `source`. Surfacing `mode` (and a "GPS locked" state for "live fix that
agrees with a manual home") resolves the confusion without pretending GPS set a
location it didn't.

Also note the re-home path deliberately routes through the display poll so
`_recenter` runs (AIS + timezone recompute) — see commit `c99582a`.

### 3.6 Input stack — X11 vs Wayland, and the multitouch dead-end

The device runs **labwc (Wayland)**; the app has historically used SDL's **x11**
driver via **Xwayland** (per `ARCHITECTURE-NOTES` — "runs unmodified on X11
exactly as with the real panel"). Input flows
`libinput → labwc → {Xwayland → SDL x11 | SDL wayland} → pygame events`.

The gesture design (`gesture_handler.py`, `pinch_handler.py`,
`input_handler.py`, all marked FROZEN) is built for a **mixed** model:
single-finger tap/swipe via **MOUSE** events (synthetic pointer from touch),
two-finger pinch via **FINGER** events. `use_finger_events()` stays `False`
until a real FINGER event is seen, so a mouse-only stack keeps working.

The blocker for #3 is upstream of all that code: **no FINGER events are produced
at all**, under either SDL driver, because the compositor presents the touch as
`wl_pointer`. Single-finger interaction works precisely because it only needs the
pointer emulation; pinch needs the touch capability that never surfaces. This is
why it can't be fixed in the (frozen, correct) handler.

### 3.7 The headless-development seam

Even with the real panel attached, the `grim` (capture) + `xdotool` (input)
workflow from `ARCHITECTURE-NOTES` remains invaluable. One critical caveat
discovered here: **`xdotool` drives X11 input, so it only reaches the app under
the x11/Xwayland driver.** Under the native `wayland` SDL driver the app reads
Wayland input directly and `xdotool` no longer reaches it — you'd need a Wayland
input tool (`ydotool`/`wtype`) or real fingers. `grim` captures the Wayland
output regardless of SDL driver.

---

## 4. Reproduction & testing techniques

- **Drive the UI:** `DISPLAY=:0 XAUTHORITY=/home/david/.Xauthority xdotool …`
  (x11 driver only). Swipes must be *paced* (`mousemove` steps with ~20 ms
  sleeps) or they aren't seen as drags. Nav: swipe-left→Settings,
  swipe-down→Clock, swipe-right→Track; radar icon (~360,610) returns to radar;
  Settings has 4 pages (Info/Display/Options/Theme) via the NEXT footer button.
- **Capture:** `XDG_RUNTIME_DIR=/run/user/1000 WAYLAND_DISPLAY=wayland-0 grim
  /tmp/x.png` then `scp`.
- **Stabilise navigation:** the display auto-returns to Clock when
  `auto_idle_clock` is on **and** there's no in-range traffic. Set it `false` in
  the settings JSON + `touch …/round_touch_settings.reload` while testing;
  restore after.
- **Detect a crash:** `Restart=always` masks crashes — the display loop restarts
  silently. Watch for `systemctl is-active` briefly showing `activating`, a
  bumped `NRestarts`, and `ERROR: Display loop crashed` + traceback in the
  journal. A plain `systemctl restart` resets `NRestarts`, so compare
  before/after a single interaction.
- **Touch diagnostics:** `TOUCH_DEBUG=1` in `/etc/flightscnr.env` logs every
  FINGER vs MOUSE event (`journalctl -u flightscnr -f | grep touch`). Needs real
  fingers — `xdotool` produces a single pointer, never a second finger.

---

## 5. Open items & recommendations

1. **#3 multitouch** — the one unresolved issue. Next steps, each needing
   physical two-finger input to validate:
   - `libinput debug-events` on the device during a 2-finger touch — does
     libinput report `TOUCH_DOWN` or `POINTER`? Isolates hardware/driver from
     compositor.
   - Confirm whether labwc/wlroots advertises `wl_touch` seat capability (a
     minimal `wl_touch` client such as `wev`) — isolates compositor from SDL.
   - If the compositor sends `wl_touch` but SDL still emits mouse: SDL 2.32.4
     wayland-backend touch handling / hints.
   - If viable, the fix is likely `SDL_VIDEODRIVER=wayland` **plus** whatever
     makes the seat expose touch — and would need the repo default
     (`.env.example`, `install-pi.sh`) updated, and the `xdotool` testing path
     swapped for a Wayland input tool.
2. **`from config import X` staleness (§3.2)** — clean up `overhead.py` to read
   `config.MIN_ALTITUDE` at the call site; the current correctness relies on a
   second live filter masking a dead argument.
3. **Deploy discipline (§2)** — always `git reset --hard fork/<branch>` +
   on-device `pytest`; never file copies.
4. **DSI→real panel migration** items from `ARCHITECTURE-NOTES` still apply
   (`DISPLAY_ROTATION`, boot overlay), plus: the software-dim fix means
   brightness now works on HDMI, and DSI keeps its hardware backlight
   untouched — no action needed there when the DSI panel is used.

---

## 6. Pull requests from this session

All target `feat/gps-auto-location`; each has a test that fails on the bug and
passes on the fix, plus on-device verification.

| PR | Issue | Title |
|----|-------|-------|
| #6 | 4 | unit-aware radar range bands (round nm/km presets) |
| #7 | 6 | software brightness dim for HDMI panels |
| #8 | 2 | range-row tap crashes the display loop (UnboundLocalError) |
| #9 | 1 | surface Auto mode + GPS-locked state on the About screen |

Issues 7 and 5 needed no code change beyond deploying the branch. Issue 3 is
documented but unresolved (§1, §3.6, §5).
