from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.day import shown_days

TZ = ZoneInfo("Europe/Berlin")
THU = date(2026, 11, 26)
FRI = date(2026, 11, 27)
SAT = date(2026, 11, 28)


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=TZ)


def test_daytime_shows_today_and_tomorrow():
    assert shown_days(at(26, 7, 30), TZ) == (THU, FRI)
    assert shown_days(at(26, 19, 59), TZ) == (THU, FRI)


def test_from_eight_in_the_evening_it_moves_on():
    assert shown_days(at(26, 20, 0), TZ) == (FRI, SAT)
    assert shown_days(at(26, 23, 59), TZ) == (FRI, SAT)


def test_after_midnight_it_still_shows_the_same_days():
    """The image drawn at 20:00 stays up overnight and has to stay right."""
    assert shown_days(at(27, 0, 30), TZ) == (FRI, SAT)
    assert shown_days(at(27, 7, 0), TZ) == (FRI, SAT)


def test_the_switch_hour_is_configurable():
    assert shown_days(at(26, 21, 0), TZ, switch_hour=22) == (THU, FRI)
    assert shown_days(at(26, 22, 0), TZ, switch_hour=22) == (FRI, SAT)


def test_hour_24_never_switches_early():
    assert shown_days(at(26, 23, 59), TZ, switch_hour=24) == (THU, FRI)


def test_a_utc_clock_is_read_in_local_time():
    """The container runs on UTC. 19:30 UTC is 20:30 in Berlin in winter."""
    utc = datetime(2026, 11, 26, 19, 30, tzinfo=ZoneInfo("UTC"))
    assert shown_days(utc, TZ) == (FRI, SAT)
