from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.scheduler import build_scheduler, in_quiet_hours, refresh_rate_for, seconds_until

TZ = ZoneInfo("Europe/Berlin")


def settings(**overrides) -> Settings:
    base = {
        "quiet_start_hour": 23,
        "quiet_end_hour": 5,
        "display_refresh_seconds": 900,
        "timezone": "Europe/Berlin",
    }
    base.update(overrides)
    return Settings(**base)


async def _noop() -> None:
    """Stand-in for the refresh callable; the jobs are never run in tests."""


def test_quiet_hours_wrap_around_midnight():
    assert in_quiet_hours(datetime(2026, 8, 5, 23, 30, tzinfo=TZ), 23, 5) is True
    assert in_quiet_hours(datetime(2026, 8, 5, 2, 0, tzinfo=TZ), 23, 5) is True
    assert in_quiet_hours(datetime(2026, 8, 5, 4, 59, tzinfo=TZ), 23, 5) is True
    assert in_quiet_hours(datetime(2026, 8, 5, 5, 0, tzinfo=TZ), 23, 5) is False
    assert in_quiet_hours(datetime(2026, 8, 5, 12, 0, tzinfo=TZ), 23, 5) is False


def test_quiet_hours_without_wrap():
    assert in_quiet_hours(datetime(2026, 8, 5, 13, 0, tzinfo=TZ), 12, 14) is True
    assert in_quiet_hours(datetime(2026, 8, 5, 15, 0, tzinfo=TZ), 12, 14) is False


def test_equal_bounds_disable_quiet_hours():
    assert in_quiet_hours(datetime(2026, 8, 5, 3, 0, tzinfo=TZ), 5, 5) is False


def test_seconds_until_next_occurrence():
    assert seconds_until(datetime(2026, 8, 5, 23, 0, tzinfo=TZ), 5) == 6 * 3600
    assert seconds_until(datetime(2026, 8, 5, 4, 0, tzinfo=TZ), 5) == 3600


def test_daytime_refresh_rate_is_the_configured_one():
    assert refresh_rate_for(datetime(2026, 8, 5, 12, 0, tzinfo=TZ), settings()) == 900


def test_night_refresh_rate_sleeps_until_morning():
    # 23:10 to 05:00 the next day is 5h50m.
    rate = refresh_rate_for(datetime(2026, 8, 5, 23, 10, tzinfo=TZ), settings())
    assert rate == 5 * 3600 + 50 * 60


def test_refresh_rate_never_returns_zero():
    rate = refresh_rate_for(datetime(2026, 8, 5, 4, 59, 59, tzinfo=TZ), settings())
    assert rate >= 60


def test_fetch_minutes_default_is_on_the_hour_and_half_hour():
    assert settings().fetch_at_minutes == "0,30"


def test_fetch_minutes_are_normalised():
    assert settings(fetch_at_minutes=" 0 , 15,30 ,45 ").fetch_at_minutes == "0,15,30,45"


@pytest.mark.parametrize("value", ["", "60", "-1", "halb", "0;30"])
def test_invalid_fetch_minutes_are_rejected(value):
    # A typo must fail loudly here rather than take the app down at scheduler
    # start, where the whole display would go dark for one stray character.
    with pytest.raises(ValidationError):
        settings(fetch_at_minutes=value)


def test_stray_separators_are_tolerated():
    # Same leniency as the other comma-separated settings: an empty entry is a
    # typo worth ignoring, not worth refusing to boot over.
    assert settings(fetch_at_minutes="0,,30").fetch_at_minutes == "0,30"


def test_periodic_job_fires_on_the_configured_minutes():
    scheduler = build_scheduler(settings(), _noop)
    trigger = scheduler.get_job("periodic_refresh").trigger
    minutes = next(f for f in trigger.fields if f.name == "minute")
    assert str(minutes) == "0,30"


def test_morning_refresh_lands_on_a_half_hour():
    # Otherwise the first screen of the day would carry a stamp that no other
    # refresh in the day produces.
    scheduler = build_scheduler(settings(), _noop)
    trigger = scheduler.get_job("morning_refresh").trigger
    fields = {f.name: str(f) for f in trigger.fields}
    assert fields["hour"] == "4"
    assert fields["minute"] == "30"
