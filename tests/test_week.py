from datetime import datetime
from zoneinfo import ZoneInfo

from app.week import current_week

TZ = ZoneInfo("Europe/Berlin")


def test_midweek_shows_current_week():
    # Wednesday 2026-08-05
    week = current_week(datetime(2026, 8, 5, 9, 0, tzinfo=TZ), TZ)
    assert week.monday.isoformat() == "2026-08-03"
    assert week.sunday.isoformat() == "2026-08-09"


def test_sunday_before_noon_still_shows_current_week():
    week = current_week(datetime(2026, 8, 9, 11, 59, tzinfo=TZ), TZ)
    assert week.monday.isoformat() == "2026-08-03"


def test_sunday_from_noon_switches_to_next_week():
    week = current_week(datetime(2026, 8, 9, 12, 0, tzinfo=TZ), TZ)
    assert week.monday.isoformat() == "2026-08-10"


def test_monday_early_shows_that_week():
    week = current_week(datetime(2026, 8, 10, 0, 5, tzinfo=TZ), TZ)
    assert week.monday.isoformat() == "2026-08-10"


def test_window_bounds_are_local_midnight():
    week = current_week(datetime(2026, 8, 5, 9, 0, tzinfo=TZ), TZ)
    assert week.start.isoformat() == "2026-08-03T00:00:00+02:00"
    assert week.end.isoformat() == "2026-08-10T00:00:00+02:00"


def test_index_of():
    week = current_week(datetime(2026, 8, 5, 9, 0, tzinfo=TZ), TZ)
    assert week.index_of(week.monday) == 0
    assert week.index_of(week.sunday) == 6
    assert week.index_of(week.monday.replace(day=2)) is None


def test_switch_is_configurable():
    at_six = datetime(2026, 8, 9, 18, 0, tzinfo=TZ)
    assert current_week(at_six, TZ, 6, 12).monday.isoformat() == "2026-08-10"
    assert current_week(at_six, TZ, 6, 20).monday.isoformat() == "2026-08-03"
