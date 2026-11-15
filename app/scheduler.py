"""Refresh timing and the device's sleep instruction."""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import Settings

log = logging.getLogger(__name__)


def in_quiet_hours(local: datetime, start_hour: int, end_hour: int) -> bool:
    if start_hour == end_hour:
        return False
    hour = local.hour
    if start_hour < end_hour:
        return start_hour <= hour < end_hour
    return hour >= start_hour or hour < end_hour


def seconds_until(local: datetime, hour: int) -> int:
    target = datetime.combine(local.date(), time(hour=hour), tzinfo=local.tzinfo)
    if target <= local:
        target += timedelta(days=1)
    return max(60, int((target - local).total_seconds()))


def refresh_rate_for(now: datetime, settings: Settings) -> int:
    """How long the device should sleep before polling again."""
    local = now.astimezone(settings.tz)
    if in_quiet_hours(local, settings.quiet_start_hour, settings.quiet_end_hour):
        return seconds_until(local, settings.quiet_end_hour)
    return settings.display_refresh_seconds


def full_refresh_due(
    *,
    changed: bool,
    draws_since_full: int,
    last_full_date: str | None,
    today: str,
    every: int,
) -> bool:
    """Whether this redraw should flash the panel black and white.

    The firmware draws a 1-bit image with a partial, flicker-free update and
    only wipes the panel properly when its own counter says so — a counter its
    BMP branch resets on every draw, so for years of screens it would never
    fire. Rather than depend on which branch the firmware takes, we ask for the
    full refresh ourselves through ``maximum_compatibility``.

    An unchanged filename is not a redraw: the device keeps its cached image
    and leaves the panel alone, so a flag spent there would be spent on
    nothing. Hence ``changed``.

    The first redraw of each local day is always full. With the device asleep
    through the quiet hours that is the screen someone walks up to in the
    morning, and it makes the cadence self-correcting if the counter is ever
    lost with the volume.
    """
    if not changed:
        return False
    if last_full_date != today:
        return True
    return draws_since_full + 1 >= every


def build_scheduler(settings: Settings, refresh_callable) -> AsyncIOScheduler:
    """Refresh on fixed minutes of the hour, paused overnight.

    Fixed minutes rather than an interval because the rendered image carries an
    HH:MM stamp: on an interval it drifts across the hour, on fixed minutes it
    always reads the same tidy values.

    The gap between refreshes is long, and nothing else keeps the process busy
    in between. It has to run on a host that does not put idle services to
    sleep, or the device's poll can hit a cold start and see a 502 as a failed
    poll.
    """
    scheduler = AsyncIOScheduler(timezone=settings.tz)

    async def periodic() -> None:
        local = datetime.now(settings.tz)
        if in_quiet_hours(local, settings.quiet_start_hour, settings.quiet_end_hour):
            log.debug("Quiet hours, skipping refresh")
            return
        await refresh_callable()

    scheduler.add_job(
        periodic,
        CronTrigger(minute=settings.fetch_at_minutes, timezone=settings.tz),
        id="periodic_refresh",
        max_instances=1,
        coalesce=True,
    )

    # One fetch shortly before the device wakes, so the first screen of the day
    # is current rather than eight hours old. On the half hour like every other
    # refresh, so the stamp on that first screen reads like all the others.
    wake_prep_hour = (settings.quiet_end_hour - 1) % 24
    scheduler.add_job(
        refresh_callable,
        CronTrigger(hour=wake_prep_hour, minute=30, timezone=settings.tz),
        id="morning_refresh",
        max_instances=1,
        coalesce=True,
    )

    return scheduler
