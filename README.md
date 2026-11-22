# trmnl-screen-1

A small BYOS ("bring your own server") backend for a **TRMNL OG** e-ink display
on a kitchen table. It shows the weather for one day large and the day after
it small: 800×480, 1 bit, no browser involved.

![weather screen](docs/preview.png)

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

Meanwhile the server refreshes the forecast on its own schedule:

```
:00 and :30  ──▶  Open-Meteo  ──▶  pick the two days  ──▶  render
```

`filename` is the device's cache key, so it is set to a hash of the image
content. When the forecast did not change, the device recognises the unchanged
name, skips the download and does not redraw the panel at all — which saves both
battery and e-ink wear.

## What the screen shows

On the left, large, the day that matters: its weekday in 108 px type, the date,
an icon, the day's high, the felt high, rain chance and amount, wind and gusts,
and a short description. Under a rule, four parts of the day — *Früh* (6–10),
*Mittag* (10–14), *Nachmittag* (14–18), *Abend* (18–22) — each with an icon,
the temperature and, smaller, the felt temperature. On the right, the day after
it: weekday, date, icon, high and low, description, rain and wind.

Until 20:00 that is today and tomorrow. From 20:00 on it is tomorrow and the
day after: in the evening, tomorrow's weather is what counts at the kitchen
table. `DAY_SWITCH_HOUR` moves the switch.

## Design notes

**No headless browser.** Terminus and `byos_node_lite` render HTML through
Chrome. This screen is a handful of text lines and icons, so Pillow draws it
directly: a much smaller image, a render in milliseconds, and crisper type —
anti-aliased browser text has to be thresholded down to 1 bit, which is exactly
what makes type look muddy on e-ink. Here it is drawn unantialiased from the
start, and the icons are drawn from circles and lines rather than taken from an
icon font, so they come out clean at every size.

**Weekdays, never "today".** The image drawn at 20:00 stays on the panel through
the night while the device sleeps. A label saying "tomorrow" would be wrong from
midnight on; "Freitag" stays right. So the screen names days only by weekday and
date.

**No clock on the screen.** A "last updated 14:30" stamp would change the image
on every refresh, and every changed image is a redraw of the panel. Without it
the image changes only when the forecast does. Temperatures are whole degrees
for the same reason, besides being what anyone reads off a kitchen display.

**The device wakes for the switch.** The server renders the next day at 20:00
on the dot. With a 15-minute poll interval the display would pick that up as
late as 20:15, so in the last stretch before the switch the device is told to
sleep only until 20:01.

**Parts of the day take their worst hour.** One rainy hour out of four decides
whether to take an umbrella, so a part shows the most severe weather of its
hours rather than the most common. *Früh* shows its coldest hour rather than an
average, because it stands in for the day's low, which the large block does not
repeat. A part that is mostly after dark gets a moon instead of a sun.

**Never a blank screen.** The last forecast that parsed is kept on the volume.
If Open-Meteo cannot be reached, that copy is drawn instead, and once it is more
than three hours old a black bar in the corner says how old. A single failed
fetch earns no mark, because the bar would only make the image flip back and
forth. A response that is not a usable forecast counts as a failed fetch and
never overwrites the good copy.

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

## Weather data

[Open-Meteo](https://open-meteo.com) — free for non-commercial use, no API key,
and for Germany its default model blend is DWD's ICON. One request a refresh
returns three days of daily and hourly values in local time. Open-Meteo
recomputes its forecast about once an hour, so refreshing on `:00` and `:30`
picks up every new run without hammering a free service.

The location defaults to Treptower Park in Berlin. Set `LATITUDE` and
`LONGITUDE` for anywhere else.

## Configuration

All settings come from environment variables; see [`.env.example`](.env.example).
Nothing is required: without any variable the server shows Treptower Park.

| Variable | Default | Meaning |
|---|---|---|
| `LATITUDE` / `LONGITUDE` | `52.4886` / `13.4697` | Where the forecast is for |
| `DAY_SWITCH_HOUR` | `20` | From this hour the next day is shown; `24` never switches early |
| `DATA_DIR` | `/data` | Must be persistent (see below) |
| `URL_PREFIX` | *(none)* | Mount the API under a secret path |
| `ALLOWED_MACS` | *(any)* | MAC allowlist for pairing |
| `LOCK_AFTER_FIRST_PAIRING` | `true` | Refuse a second device |
| `PAIRING_SECRET` | *(none)* | Header/query secret; the firmware cannot send one |
| `DISPLAY_REFRESH_SECONDS` | `900` | Device sleep between polls |
| `FETCH_AT_MINUTES` | `0,30` | Minutes of the hour to refresh the forecast on |
| `QUIET_START_HOUR` / `QUIET_END_HOUR` | `23` / `5` | Overnight sleep |
| `FULL_REFRESH_EVERY` | `8` | Every Nth redraw wipes the panel |
| `TEMPERATURE_PROFILE` | `default` | Waveform profile; this panel needs `a` |
| `FIRMWARE_TARGET` | *(none)* | Firmware version to update the device to |

Variables from the calendar version of this server (`CALDAV_*`, `ICS_URLS`,
`WEEK_SWITCH_*`, `GRID_*`) are ignored and can be deleted.

## Running locally

```bash
uv venv --python 3.12 && uv pip install -r requirements.txt -r <(echo pytest httpx ruff)
cp .env.example .env          # then edit it
DATA_DIR=./data .venv/bin/uvicorn app.main:create_app --factory --reload
```

Render the screen from sample data without a server or a device:

```bash
.venv/bin/python scripts/preview_sample.py preview.png
```

Tests:

```bash
.venv/bin/python -m pytest
```

## Deploying to Coolify

1. Create an application from this repository with the **Dockerfile** build
   pack, branch `main`, and *Ports Exposes* set to `8000`. Turn on automatic
   deployment on push if you like.
2. **Add persistent storage** mounted at `/data`, which is where `DATA_DIR`
   points by default. A container's filesystem is replaced on every deploy —
   without a volume the device's `api_key` is lost and the display silently
   unpairs.
3. Give the application a domain, e.g. `https://trmnl.example.org`, with a DNS
   record pointing at the server. Coolify's proxy obtains the certificate,
   which needs port 80 reachable. An own domain also means the display never
   has to be re-pointed when the server moves.
4. Set the environment variables from `.env.example`: at least a
   `URL_PREFIX` and `PUBLIC_BASE_URL` set to that domain, plus the location if
   it is not Treptower Park.
5. The healthcheck is the `HEALTHCHECK` in the `Dockerfile`, against `/healthz`.
   Leave Coolify's own healthcheck switched off: it runs `curl` or `wget`
   inside the container, and the slim image has neither, so every deploy
   would be marked unhealthy.

The server has to keep running between polls — the forecast is fetched on the
half hour, so a host that puts idle services to sleep would answer the
display's poll with a cold start, which the firmware sees as a failed poll.

Coolify does not back up application volumes. `state.db` is small and can be
rebuilt by pairing again, but copying it off now and then saves the image
token and the last-known-good forecast.

## Pairing the display

In the captive portal: *Advanced → Custom Server → Yes*, then the base URL with
no trailing slash. The firmware appends `/api/...` itself. It calls `/api/setup`
once, stores the returned `api_key`, and uses it for every later `/api/display`.

**The device accepts a URL and nothing else** — no custom headers, no extra
fields. That shapes what pairing protection can look like, because `/api/setup`
is otherwise open to anyone who finds the host:

1. **`URL_PREFIX`** — mount everything under a secret path and hand the device
   `https://trmnl.example.org/s/<long-random-string>`. The URL becomes the
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
| `POST /preview/<token>/refresh` | Force a forecast refresh |
| `GET /healthz` | Liveness only, outside `URL_PREFIX` for the platform |
| `GET /status` | Days shown, forecast age, last error, device telemetry |

`/image` and `/preview` are guarded by an unguessable token generated on first
start, and `/status` sits behind `URL_PREFIX` because it exposes the location
and device telemetry. Retrieve it from the
`kv` table in `state.db`, or read it from the `image_url` that `/api/setup`
returns.

## Licence

The vendored Barlow Condensed fonts in `fonts/` are covered by the SIL Open
Font License, [included here](fonts/LICENSE.barlow).
