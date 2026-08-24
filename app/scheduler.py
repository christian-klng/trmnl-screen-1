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


def build_scheduler(settings: Settings, refresh_callable) -> AsyncIOScheduler:
    """Refresh on fixed minutes of the hour, paused overnight.

    Fixed minutes rather than an interval because the rendered image carries an
    HH:MM stamp: on an interval it drifts across the hour, on fixed minutes it
    always reads the same tidy values.

    The gap between refreshes is now longer than the ten minutes after which
    Railway's serverless mode considers a service idle — app sleeping has to
    stay switched off for this service, or the device's poll can hit a cold
    start and see a 502 as a failed poll.
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
