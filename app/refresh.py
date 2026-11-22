"""Fetch, pick the days, render — the whole pipeline in one place."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from PIL import Image

from .config import Settings
from .day import shown_days
from .render import render_message, render_weather, to_device_bytes
from .store import Store
from .weather import fetch_forecast, parse_forecast

log = logging.getLogger(__name__)

# How old the forecast may get before the screen says so. A single failed
# fetch is not worth a mark: it would only flip the image back and forth, and
# every flip is a redraw of the panel.
STALE_AFTER = timedelta(hours=3)

Fetcher = Callable[[Settings], dict]


@dataclass
class ScreenState:
    digest: str | None = None
    generated_at: datetime | None = None
    forecast_fetched_at: datetime | None = None
    shown_days: list[str] = field(default_factory=list)
    last_error: str | None = None


def stale_note(fetched_at: datetime, now: datetime) -> str | None:
    """The banner for an old forecast, or None while it is fresh enough."""
    if now - fetched_at < STALE_AFTER:
        return None
    local = fetched_at.astimezone(now.tzinfo)
    if local.date() == now.date():
        return f"Daten von {local:%H:%M} · offline"
    return f"Daten vom {local:%d.%m. %H:%M} · offline"


async def refresh_screen(
    settings: Settings,
    store: Store,
    state: ScreenState,
    fetch: Fetcher = fetch_forecast,
) -> ScreenState:
    tz = settings.tz
    now = datetime.now(tz)

    try:
        raw = await asyncio.to_thread(fetch, settings)
        days = parse_forecast(raw)
        store.save_forecast(raw, now)
        fetched_at = now
        state.last_error = None
    except Exception as exc:  # noqa: BLE001 - the weather service being down must not blank the screen
        log.warning("Forecast fetch failed: %s", exc)
        state.last_error = str(exc) or type(exc).__name__
        cached = store.load_forecast()
        if cached is None:
            image = render_message(
                "Wetter nicht erreichbar",
                ["Der Wetterdienst antwortet gerade nicht.", "Die Anzeige versucht es weiter."],
            )
            return _store_image(store, state, image, now)
        raw, fetched_at = cached
        days = parse_forecast(raw)
        log.warning("Using the forecast from %s", fetched_at.isoformat())

    main_day, next_day = shown_days(now, tz, settings.day_switch_hour)
    state.forecast_fetched_at = fetched_at
    state.shown_days = [main_day.isoformat(), next_day.isoformat()]

    if main_day not in days or next_day not in days:
        image = render_message(
            "Keine aktuellen Wetterdaten",
            [f"Die letzte Vorhersage ist vom {fetched_at.astimezone(tz):%d.%m.%Y}."],
        )
        return _store_image(store, state, image, now)

    image = render_weather(days[main_day], days[next_day], stale_note(fetched_at, now))
    return _store_image(store, state, image, now)


def _store_image(store: Store, state: ScreenState, image: Image.Image, now: datetime) -> ScreenState:
    payload = to_device_bytes(image, store.image_ext)
    digest = hashlib.sha1(payload).hexdigest()[:16]

    # An unchanged digest means the device keeps its cached image and the
    # e-ink panel is not redrawn at all.
    if digest != store.current_digest():
        store.save_image(digest, payload)
        log.info("Rendered new screen %s", digest)
    else:
        log.info("Screen unchanged (%s), device will use its cache", digest)

    state.digest = digest
    state.generated_at = now
    return state
