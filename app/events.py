"""Normalising iCalendar data into a flat, timezone-correct event list."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import recurring_ical_events
from icalendar import Calendar

log = logging.getLogger(__name__)

DEFAULT_DURATION = timedelta(hours=1)


@dataclass(frozen=True)
class Event:
    uid: str
    summary: str
    start: datetime  # timezone-aware, in the display timezone
    end: datetime  # exclusive
    all_day: bool
    calendar: str

    @property
    def duration(self) -> timedelta:
        return self.end - self.start


def _to_local_datetime(value: date | datetime, tz: ZoneInfo) -> datetime:
    """A ``date`` means an all-day boundary: local midnight."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            # Floating time per RFC 5545 — interpret in the display timezone.
            return value.replace(tzinfo=tz)
        return value.astimezone(tz)
    return datetime.combine(value, time.min, tzinfo=tz)


def _is_all_day(component) -> bool:
    dtstart = component.get("DTSTART")
    if dtstart is None:
        return False
    return not isinstance(dtstart.dt, datetime)


def _component_to_event(component, calendar_name: str, tz: ZoneInfo) -> Event | None:
    if str(component.get("STATUS", "")).upper() == "CANCELLED":
        return None

    dtstart = component.get("DTSTART")
    if dtstart is None:
        return None

    all_day = _is_all_day(component)
    start = _to_local_datetime(dtstart.dt, tz)

    dtend = component.get("DTEND")
    if dtend is not None:
        end = _to_local_datetime(dtend.dt, tz)
    else:
        duration = component.get("DURATION")
        if duration is not None:
            end = start + duration.dt
        elif all_day:
            end = start + timedelta(days=1)
        else:
            end = start + DEFAULT_DURATION

    # Zero-length events would render as invisible slivers.
    if end <= start:
        end = start + (timedelta(days=1) if all_day else DEFAULT_DURATION)

    summary = str(component.get("SUMMARY", "")).strip() or "(ohne Titel)"

    return Event(
        uid=str(component.get("UID", "")),
        summary=summary,
        start=start,
        end=end,
        all_day=all_day,
        calendar=calendar_name,
    )


def expand_calendar(
    ical_text: str | bytes,
    calendar_name: str,
    window_start: datetime,
    window_end: datetime,
    tz: ZoneInfo,
) -> list[Event]:
    """Parse one iCalendar document and expand recurrences into the window.

    ``recurring_ical_events`` handles RRULE, EXDATE and RECURRENCE-ID overrides,
    which is the part that is genuinely easy to get wrong by hand.
    """
    try:
        calendar = Calendar.from_ical(ical_text)
    except Exception:
        log.exception("Could not parse iCalendar data from %s", calendar_name)
        return []

    try:
        occurrences = recurring_ical_events.of(calendar, skip_bad_series=True).between(
            window_start, window_end
        )
    except Exception:
        log.exception("Could not expand recurrences for %s", calendar_name)
        return []

    events: list[Event] = []
    for component in occurrences:
        try:
            event = _component_to_event(component, calendar_name, tz)
        except Exception:
            log.exception("Skipping unreadable event in %s", calendar_name)
            continue
        if event is not None:
            events.append(event)
    return events


def clip_and_dedupe(events: list[Event], window_start: datetime, window_end: datetime) -> list[Event]:
    """Drop events outside the window and collapse duplicates.

    Deduplication matters more than it looks: with several calendars rendered
    identically, the same meeting subscribed in two of them would otherwise
    appear twice with no way for the reader to tell it is one event.
    """
    seen: set[tuple[str, datetime, bool]] = set()
    out: list[Event] = []

    for event in events:
        if event.end <= window_start or event.start >= window_end:
            continue

        key = (event.uid, event.start, event.all_day)
        if event.uid and key in seen:
            continue
        if event.uid:
            seen.add(key)

        start = max(event.start, window_start)
        end = min(event.end, window_end)
        if end <= start:
            continue

        out.append(
            event
            if (start == event.start and end == event.end)
            else Event(
                uid=event.uid,
                summary=event.summary,
                start=start,
                end=end,
                all_day=event.all_day,
                calendar=event.calendar,
            )
        )

    out.sort(key=lambda e: (e.start, e.end, e.summary))
    return out


def split_by_day(event: Event, tz: ZoneInfo) -> list[tuple[date, datetime, datetime]]:
    """Split an event into per-day segments so it can be drawn in day columns."""
    segments: list[tuple[date, datetime, datetime]] = []
    cursor = event.start
    while cursor < event.end:
        day = cursor.date()
        midnight = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz)
        segment_end = min(event.end, midnight)
        if segment_end > cursor:
            segments.append((day, cursor, segment_end))
        cursor = midnight
    return segments
