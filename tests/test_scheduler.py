from datetime import datetime
from zoneinfo import ZoneInfo

from app.config import Settings
from app.scheduler import in_quiet_hours, refresh_rate_for, seconds_until

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
