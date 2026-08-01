"""Which Monday-to-Sunday week the display shows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class WeekWindow:
    monday: date
    tz: ZoneInfo

    @property
    def sunday(self) -> date:
        return self.monday + timedelta(days=6)

    @property
    def days(self) -> list[date]:
        return [self.monday + timedelta(days=i) for i in range(7)]

    @property
    def start(self) -> datetime:
        """Inclusive start, local midnight on Monday."""
        return datetime.combine(self.monday, time.min, tzinfo=self.tz)

    @property
    def end(self) -> datetime:
        """Exclusive end, local midnight after Sunday."""
        return datetime.combine(self.monday + timedelta(days=7), time.min, tzinfo=self.tz)

    def index_of(self, day: date) -> int | None:
        offset = (day - self.monday).days
        return offset if 0 <= offset < 7 else None


def current_week(now: datetime, tz: ZoneInfo, switch_weekday: int = 6, switch_hour: int = 12) -> WeekWindow:
    """The week to display.

    Normally the week containing ``now``. From ``switch_weekday`` at
    ``switch_hour`` onward it flips to the following week, so the display shows
    what is coming rather than what is over.

    Note the deliberate consequence: with the default Sunday 12:00 switch, any
    remaining events on that same Sunday afternoon drop off the display.
    """
    local = now.astimezone(tz)
    monday = local.date() - timedelta(days=local.weekday())
    if local.weekday() == switch_weekday and local.hour >= switch_hour:
        monday = monday + timedelta(days=7)
    return WeekWindow(monday=monday, tz=tz)
