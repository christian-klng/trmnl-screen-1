# trmnl-screen-1

A small BYOS ("bring your own server") backend for a **TRMNL OG** e-ink display.
It aggregates several calendars over CalDAV and renders them as a
Monday-to-Sunday week grid: 800×480, 1 bit, no browser involved.

![week layout](docs/preview.png)

## How it works

The display polls one endpoint on a timer and draws whatever image it is
pointed at:

```
device wakes  ──▶  GET /api/display  ──▶  { image_url, filename, refresh_rate }
                                            │
                   GET /image/<token>/<hash>.png  ──▶  800×480 1-bit PNG
                                            │
                   sleeps for refresh_rate seconds
```

Meanwhile the server refreshes calendar data on its own schedule:

```
:00 and :30  ──▶  CalDAV fetch  ──▶  expand recurrences  ──▶  layout  ──▶  render
```

`filename` is the device's cache key, so it is set to a hash of the image
content. When nothing in the week changed, the device recognises the unchanged
name, skips the download and does not redraw the panel at all — which saves both
battery and e-ink wear.

## Design notes

**No headless browser.** Terminus and `byos_node_lite` render HTML through
Chrome. A week grid is pure geometry, so Pillow draws it directly: a much
smaller image, ~50 ms per render, and crisper type — anti-aliased browser text
has to be thresholded down to 1 bit, which is exactly what makes small type look
muddy on e-ink. Here it is drawn unantialiased from the start.

**The grid is tight, on purpose.** Seven columns across 800 px leaves 109 px per
day, roughly 17 characters. Two parallel events halve that. Long titles are
wrapped, and words too long for a column are broken rather than ellipsised
mid-title — an ellipsis in the middle would suggest the title ends there while
the next line carries on.

**Adaptive time window.** Rather than a fixed 07:00–21:00, the grid zooms to the
hours actually used that week, with a minimum span of 8 h so a quiet week is not
stretched absurdly and a maximum of 15 h so one 04:00 flight cannot ruin the
resolution of every other day. Events outside the window are shown as `↑n` / `↓n`
edge markers instead of widening it. A week with no events at all falls back to
08:00–16:00.

**Never a blank screen.** If a calendar cannot be fetched, its last known good
copy is used and its name is marked in the header. A calendar server hiccup
showing an empty week would be the worst possible failure.

**The panel is wiped on a schedule.** A 1-bit image is drawn with a partial,
flicker-free update, and a panel that only ever gets those slowly washes out.
The firmware does have a guard — a full black/white refresh every eight partial
ones — but its BMP branch resets that counter on every draw, so for a screen
served as BMP the guard can never fire. This server drove its display that way
for its first weeks, and the contrast visibly faded.

Two things fix it. The screen is served as PNG, which is the branch where the
firmware's own counter works — and which is about 2 KB against the BMP's fixed
48 062 bytes.
And rather than rely on that, every eighth redraw and the first redraw of each
day set `maximum_compatibility` in the poll response, which is the firmware's
oddly named switch for "wipe the panel properly this time". `FULL_REFRESH_EVERY`
tunes the cadence; set it to 1 for a day to scrub a panel that has already
faded. Only a redraw counts — when the image is unchanged the device keeps its
cached copy and never touches the panel, so a wipe requested then would be
spent on nothing.

**The waveform profile is ours to set, and this panel needs `a`.**
`TEMPERATURE_PROFILE` (`default`, `a`, `b`) rides along on every poll — the same
knob as *Device settings → Firmware* on trmnl.com, which a BYOS device cannot
reach. It has to be sent every time, because the firmware overwrites its stored
value from each response, and it takes effect on the *next* redraw, since the
profile is stored only after the current image has been drawn.

It is not cosmetic. Under `default` the panel type is `EP75_800x480`, and
`png_to_epd()` writes the second memory plane — the controller's copy of the
*old* image — only when the type differs from exactly that:

```c
if (bbep.getPanelType() != EP75_800x480) { bbep.startWrite(PLANE_1); ... }
```

The display is cut from power between polls, so by the next wake that plane
holds noise. A full refresh drives every pixel along its old→new transition, so
under `default` it drives them from noise: the screen comes back pale, at worst
unreadable, and the partial updates that follow claw it back over the next few
hours. The signature is unmistakable once you have seen it — the screen looks
worst right after a wipe and best just before the next one. Under `a` (panel
type `EP75_800x480_GEN2`) the firmware writes the inverted image into that
plane, every pixel gets a defined full transition, and the wipe comes out
crisp. `b` buys nothing here: for a purely 1-bit screen on the OG it maps to the
same panel type as `default`.

**Firmware updates come from here too.** Set `FIRMWARE_TARGET` (say `1.8.16`)
and the next poll tells the device to update itself: it downloads TRMNL's
release image into its spare app partition, verifies it and reboots into it.
WiFi and the server URL are kept, which a USB flash would erase. The firmware
compares no versions — told to update, it would reflash the same image on every
poll — so the offer is made only while the device reports an older version,
never as a downgrade, and at most once per `FIRMWARE_RETRY_HOURS`.

## Calendar sources

Two kinds, and they can be combined:

- **CalDAV** (`CALDAV_URL`, `CALDAV_USERNAME`, `CALDAV_PASSWORD`) — Nextcloud or
  any CalDAV server, using an app password.
- **Direct ICS/webcal URLs** (`ICS_URLS`) — fetched on our own schedule.

The second exists to route around a slow intermediary. A Google calendar
subscribed inside Nextcloud is only as fresh as Nextcloud's subscription job,
which defaults to once a day and needs shell access to change. Pointing at
Google's secret iCal address directly puts it back on our own half-hourly cycle.
`webcal://` URLs are rewritten to `https://` automatically.

A response that is not iCalendar — a login or error page, say — is treated as a
failure rather than as an empty calendar, so a broken URL cannot silently erase
those events from the screen.

## Configuration

All settings come from environment variables; see [`.env.example`](.env.example).
At least one of the CalDAV trio or `ICS_URLS` must be set.

| Variable | Default | Meaning |
|---|---|---|
| `CALDAV_CALENDARS` | *(all)* | Comma-separated display names, up to 5 |
| `ICS_URLS` | *(none)* | `Name=URL`, comma separated |
| `DATA_DIR` | `/data` | Must be persistent (see below) |
| `URL_PREFIX` | *(none)* | Mount the API under a secret path |
| `ALLOWED_MACS` | *(any)* | MAC allowlist for pairing |
| `LOCK_AFTER_FIRST_PAIRING` | `true` | Refuse a second device |
| `PAIRING_SECRET` | *(none)* | Header/query secret; the firmware cannot send one |
| `DISPLAY_REFRESH_SECONDS` | `900` | Device sleep between polls |
| `FETCH_AT_MINUTES` | `0,30` | Minutes of the hour to refresh calendars on |
| `QUIET_START_HOUR` / `QUIET_END_HOUR` | `23` / `5` | Overnight sleep |
| `WEEK_SWITCH_WEEKDAY` / `WEEK_SWITCH_HOUR` | `6` / `12` | Sunday noon flips to next week |
| `GRID_MIN_HOURS` / `GRID_MAX_HOURS` | `8` / `15` | Adaptive window bounds |

## Nextcloud setup

Google calendars are subscribed inside Nextcloud, so this server only ever
speaks CalDAV. Two Nextcloud settings decide whether that actually works:

```bash
# Subscribed calendars refresh very rarely by default.
php occ config:app:set dav calendarSubscriptionRefreshRate --value "PT15M"
```

**Background jobs must be set to system cron**, not AJAX
(*Administration → Basic settings*). With AJAX the refresh job only runs while
somebody has the web UI open, so subscribed calendars effectively never update.

Be aware of the resulting chain for Google events: Google's ICS export cache →
Nextcloud's refresh job → this server's half-hourly fetch → the device's
15-minute poll. Calendars living natively in Nextcloud are on the display
within about 45 minutes; Google events are slower, and by how much is up to
Google's cache.

## Running locally

```bash
uv venv --python 3.12 && uv pip install -r requirements.txt -r <(echo pytest httpx ruff)
cp .env.example .env          # then edit it
DATA_DIR=./data .venv/bin/uvicorn app.main:create_app --factory --reload
```

Render a sample week without a server or a device:

```bash
.venv/bin/python scripts/preview_sample.py preview.png
```

Tests:

```bash
.venv/bin/python -m pytest
```

## Deploying to Railway

1. Push this repository and create a Railway service from it. The
   `Dockerfile` and `railway.toml` are picked up automatically.
2. **Attach a volume** and set `DATA_DIR` to its mount path (e.g. `/data`).
   Railway's filesystem is ephemeral — without a volume the device's `api_key`
   is lost on every deploy and the display silently unpairs.
   If the container ever runs as a non-root user, also set `RAILWAY_RUN_UID=0`,
   since volumes are mounted as root.
3. **Turn Serverless off** for this service. Railway considers a service idle
   after ten minutes without *outbound* traffic, and the first request to a
   slept service can return 502 — which the display would see as a failed poll.
   This is not optional: calendars are fetched on the half hour, so nothing
   else keeps the service awake between polls.
4. Set the environment variables from `.env.example`, including calendar
   sources and a `URL_PREFIX`.
5. Generate a public domain. That URL plus `URL_PREFIX` is what the display
   gets in pairing mode.

## Pairing the display

In the captive portal: *Advanced → Custom Server → Yes*, then the base URL with
no trailing slash. The firmware appends `/api/...` itself. It calls `/api/setup`
once, stores the returned `api_key`, and uses it for every later `/api/display`.

**The device accepts a URL and nothing else** — no custom headers, no extra
fields. That shapes what pairing protection can look like, because `/api/setup`
is otherwise open to anyone who finds the host:

1. **`URL_PREFIX`** — mount everything under a secret path and hand the device
   `https://your-app.up.railway.app/s/<long-random-string>`. The URL becomes the
   credential. This is the only mechanism that keeps the endpoints from being
   discoverable at all.
2. **`ALLOWED_MACS`** — only listed MAC addresses may register. The MAC is not a
   secret, but combined with 1 it is a solid second gate.
3. **`LOCK_AFTER_FIRST_PAIRING`** (on by default) — after your device has
   paired, no other can. Zero configuration, and enough on its own for a single
   display, though it does mean a stranger who reaches `/api/setup` before you
   do would take the slot. Pair first, then relax.

An already-paired device may always re-run `/api/setup`, so a factory reset does
not lock you out.

`PAIRING_SECRET` is kept for clients that *can* send an `X-Pairing-Secret`
header or `?secret=` parameter. The TRMNL firmware cannot, so do not rely on it
for the display.

## Endpoints

| Endpoint | Purpose |
|---|---|
| `GET/POST /api/setup` | Pairing; returns `api_key` and `friendly_id` |
| `GET/POST /api/display` | Per-wake poll; returns image URL and sleep time |
| `POST /api/log` | Firmware diagnostics, written to the service log |
| `GET /image/<token>/<hash>.png` | The rendered screen |
| `GET /preview/<token>.png` | The same screen as PNG, for a browser |
| `POST /preview/<token>/refresh` | Force a calendar refresh |
| `GET /healthz` | Liveness only, outside `URL_PREFIX` for the platform |
| `GET /status` | Render state, stale calendars, device telemetry |

`/image`, `/preview` and `/status` carry the contents of your calendar, so they are
guarded by an unguessable token generated on first start. Retrieve it from the
`kv` table in `state.db`, or read it from the `image_url` that `/api/setup`
returns.

## Licence

The vendored DejaVu Sans Condensed fonts in `fonts/` are covered by
[their own licence](fonts/LICENSE.dejavu).
