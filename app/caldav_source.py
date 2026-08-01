"""Fetching raw iCalendar documents from Nextcloud (or any CalDAV server).

Recurrences are deliberately *not* expanded server-side. Fetching the raw
documents keeps the cached copy re-expandable when the displayed week moves on,
and avoids depending on how a particular server interprets expansion.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

import caldav

log = logging.getLogger(__name__)


@dataclass
class FetchResult:
    documents: dict[str, list[str]]
    failed: list[str]


def _calendar_name(calendar) -> str:
    try:
        name = calendar.get_display_name()
        if name:
            return str(name)
    except Exception:  # noqa: BLE001 - a nameless calendar must not break the run
        log.debug("Could not read display name", exc_info=True)
    return str(getattr(calendar, "name", None) or calendar.url)


def fetch_calendars(
    url: str,
    username: str,
    password: str,
    start: datetime,
    end: datetime,
    wanted: list[str] | None = None,
    timeout: int = 20,
) -> FetchResult:
    """Return raw ICS documents per calendar.

    A calendar that fails is reported in ``failed`` rather than raising, so one
    unreachable calendar cannot blank the whole display.
    """
    documents: dict[str, list[str]] = {}
    failed: list[str] = []

    client = caldav.DAVClient(url=url, username=username, password=password, timeout=timeout)
    principal = client.principal()
    calendars = principal.calendars()

    if wanted:
        wanted_lower = {w.lower() for w in wanted}
        selected = [c for c in calendars if _calendar_name(c).lower() in wanted_lower]
        missing = wanted_lower - {_calendar_name(c).lower() for c in selected}
        for name in missing:
            log.warning("Configured calendar %r not found on the server", name)
            failed.append(name)
    else:
        selected = calendars

    for calendar in selected:
        name = _calendar_name(calendar)
        try:
            results = calendar.search(start=start, end=end, event=True, expand=False)
            documents[name] = [obj.data for obj in results if obj.data]
        except Exception:  # noqa: BLE001 - degrade to last known good instead
            log.exception("Fetching calendar %r failed", name)
            failed.append(name)

    return FetchResult(documents=documents, failed=failed)
