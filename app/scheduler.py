"""Refresh timing and the device's sleep instruction."""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

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


def build_scheduler(settings: Settings, refresh_callable) -> AsyncIOScheduler:
    """Periodic refresh, paused overnight.

    The interval is deliberately under ten minutes: Railway treats a service as
    idle after ten minutes without outbound traffic, and the first request to a
    slept service can come back as a 502 that the display would see as a failed
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
        IntervalTrigger(minutes=settings.fetch_interval_minutes),
        id="periodic_refresh",
        max_instances=1,
        coalesce=True,
    )

    # One fetch shortly before the device wakes, so the first screen of the day
    # is current rather than eight hours old.
    wake_prep_hour = (settings.quiet_end_hour - 1) % 24
    scheduler.add_job(
        refresh_callable,
        CronTrigger(hour=wake_prep_hour, minute=50, timezone=settings.tz),
        id="morning_refresh",
        max_instances=1,
        coalesce=True,
    )

    return scheduler
