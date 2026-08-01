"""Fetch, normalise, lay out and render — the whole pipeline in one place."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime

from .caldav_source import fetch_calendars
from .config import Settings
from .events import Event, clip_and_dedupe, expand_calendar
from .layout import build_layout
from .render import render_message, render_week, to_bmp_bytes
from .store import Store
from .week import current_week

log = logging.getLogger(__name__)


@dataclass
class ScreenState:
    digest: str | None = None
    generated_at: datetime | None = None
    stale_calendars: list[str] = field(default_factory=list)
    event_count: int = 0
    last_error: str | None = None


async def refresh_screen(settings: Settings, store: Store, state: ScreenState) -> ScreenState:
    tz = settings.tz
    now = datetime.now(tz)
    window = current_week(now, tz, settings.week_switch_weekday, settings.week_switch_hour)

    if not settings.caldav_configured:
        image = render_message(
            "Kein Kalender konfiguriert",
            [
                "CALDAV_URL, CALDAV_USERNAME und CALDAV_PASSWORD setzen.",
                "Danach aktualisiert sich diese Anzeige automatisch.",
            ],
        )
        return _store_image(store, state, image, now, [], 0)

    documents: dict[str, list[str]] = {}
    failed: list[str] = []

    try:
        result = await asyncio.to_thread(
            fetch_calendars,
            settings.caldav_url,
            settings.caldav_username,
            settings.caldav_password,
            window.start,
            window.end,
            settings.calendar_names or None,
        )
        documents = result.documents
        failed = result.failed
        state.last_error = None
    except Exception as exc:  # noqa: BLE001 - the server being down must not blank the screen
        log.exception("CalDAV fetch failed entirely")
        state.last_error = str(exc)
        # Nothing was fetched, so every calendar we know about falls back to
        # its cached copy.
        failed = settings.calendar_names or store.list_cached_calendars()

    for name, docs in documents.items():
        store.save_calendar(name, docs)

    stale: list[str] = []
    for name in failed:
        cached = store.load_calendar(name)
        if cached:
            documents[name] = cached[0]
            stale.append(name)
            log.warning("Using last known good data for calendar %r", name)
        else:
            stale.append(name)
            log.warning("Calendar %r failed and has no cached copy", name)

    events: list[Event] = []
    for name, docs in documents.items():
        for doc in docs:
            events.extend(expand_calendar(doc, name, window.start, window.end, tz))

    events = clip_and_dedupe(events, window.start, window.end)

    layout = build_layout(
        events,
        window,
        tz,
        min_hours=settings.grid_min_hours,
        max_hours=settings.grid_max_hours,
        stale_calendars=sorted(set(stale)),
        generated_at=now,
    )
    image = render_week(layout, now, tz)
    return _store_image(store, state, image, now, sorted(set(stale)), len(events))


def _store_image(
    store: Store,
    state: ScreenState,
    image,
    now: datetime,
    stale: list[str],
    event_count: int,
) -> ScreenState:
    payload = to_bmp_bytes(image)
    digest = hashlib.sha1(payload).hexdigest()[:16]

    # An unchanged digest means the device keeps its cached image and the
    # e-ink panel is not redrawn at all.
    if digest != store.current_digest():
        store.save_image(digest, payload)
        log.info("Rendered new screen %s (%d events)", digest, event_count)
    else:
        log.info("Screen unchanged (%s), device will use its cache", digest)

    state.digest = digest
    state.generated_at = now
    state.stale_calendars = stale
    state.event_count = event_count
    return state
