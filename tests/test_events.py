from datetime import datetime
from zoneinfo import ZoneInfo

from app.events import clip_and_dedupe, expand_calendar, split_by_day

TZ = ZoneInfo("Europe/Berlin")
WINDOW_START = datetime(2026, 8, 3, 0, 0, tzinfo=TZ)
WINDOW_END = datetime(2026, 8, 10, 0, 0, tzinfo=TZ)


def ics(*events: str) -> str:
    body = "\n".join(events)
    return f"BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//trmnl-test//\n{body}\nEND:VCALENDAR\n"


def vevent(**fields: str) -> str:
    lines = "\n".join(f"{k.replace('_', '-')}:{v}" for k, v in fields.items())
    return f"BEGIN:VEVENT\n{lines}\nEND:VEVENT"


def expand(text: str, name: str = "Privat"):
    return expand_calendar(text, name, WINDOW_START, WINDOW_END, TZ)


def test_timed_event_is_converted_to_local_time():
    text = ics(
        vevent(
            UID="a@test",
            DTSTART="20260803T070000Z",
            DTEND="20260803T080000Z",
            SUMMARY="Standup",
        )
    )
    events = expand(text)
    assert len(events) == 1
    event = events[0]
    assert event.all_day is False
    assert event.start.hour == 9  # 07:00Z is 09:00 in Berlin in August
    assert event.summary == "Standup"
    assert event.calendar == "Privat"


def test_all_day_event_uses_date_values():
    text = ics(vevent(UID="b@test", DTSTART="20260805", DTEND="20260806", SUMMARY="Urlaub"))
    events = expand(text)
    assert len(events) == 1
    assert events[0].all_day is True
    assert events[0].start.isoformat() == "2026-08-05T00:00:00+02:00"
    assert events[0].end.isoformat() == "2026-08-06T00:00:00+02:00"


def test_all_day_event_without_dtend_covers_one_day():
    text = ics(vevent(UID="c@test", DTSTART="20260805", SUMMARY="Feiertag"))
    events = expand(text)
    assert events[0].end.isoformat() == "2026-08-06T00:00:00+02:00"


def test_weekly_recurrence_is_expanded():
    text = ics(
        vevent(
            UID="d@test",
            DTSTART="20260601T080000Z",
            DTEND="20260601T083000Z",
            RRULE="FREQ=WEEKLY;BYDAY=MO",
            SUMMARY="Wochenstart",
        )
    )
    events = expand(text)
    assert len(events) == 1
    assert events[0].start.date().isoformat() == "2026-08-03"


def test_exdate_removes_an_occurrence():
    daily = vevent(
        UID="e@test",
        DTSTART="20260803T080000Z",
        DTEND="20260803T083000Z",
        RRULE="FREQ=DAILY;COUNT=5",
        EXDATE="20260805T080000Z",
        SUMMARY="Daily",
    )
    events = expand(ics(daily))
    dates = sorted(e.start.date().isoformat() for e in events)
    assert "2026-08-05" not in dates
    assert len(events) == 4


def test_recurrence_id_override_wins():
    master = vevent(
        UID="f@test",
        DTSTART="20260803T080000Z",
        DTEND="20260803T083000Z",
        RRULE="FREQ=DAILY;COUNT=3",
        SUMMARY="Serie",
    )
    override = vevent(
        UID="f@test",
        RECURRENCE_ID="20260804T080000Z",
        DTSTART="20260804T140000Z",
        DTEND="20260804T143000Z",
        SUMMARY="Verschoben",
    )
    events = expand(ics(master, override))
    moved = [e for e in events if e.summary == "Verschoben"]
    assert len(moved) == 1
    assert moved[0].start.hour == 16  # 14:00Z -> 16:00 local
    assert not [e for e in events if e.summary == "Serie" and e.start.day == 4]


def test_cancelled_events_are_dropped():
    text = ics(
        vevent(
            UID="g@test",
            DTSTART="20260804T080000Z",
            DTEND="20260804T090000Z",
            STATUS="CANCELLED",
            SUMMARY="Abgesagt",
        )
    )
    assert expand(text) == []


def test_missing_summary_gets_a_placeholder():
    text = ics(vevent(UID="h@test", DTSTART="20260804T080000Z", DTEND="20260804T090000Z"))
    assert expand(text)[0].summary == "(ohne Titel)"


def test_zero_length_event_is_given_a_duration():
    text = ics(
        vevent(UID="i@test", DTSTART="20260804T080000Z", DTEND="20260804T080000Z", SUMMARY="Punkt")
    )
    event = expand(text)[0]
    assert event.end > event.start


def test_dedupe_collapses_the_same_event_from_two_calendars():
    text = ics(
        vevent(UID="j@test", DTSTART="20260804T080000Z", DTEND="20260804T090000Z", SUMMARY="Meeting")
    )
    events = expand(text, "Privat") + expand(text, "Arbeit")
    assert len(events) == 2
    assert len(clip_and_dedupe(events, WINDOW_START, WINDOW_END)) == 1


def test_different_occurrences_of_one_series_survive_dedupe():
    text = ics(
        vevent(
            UID="k@test",
            DTSTART="20260803T080000Z",
            DTEND="20260803T083000Z",
            RRULE="FREQ=DAILY;COUNT=3",
            SUMMARY="Serie",
        )
    )
    assert len(clip_and_dedupe(expand(text), WINDOW_START, WINDOW_END)) == 3


def test_events_outside_the_window_are_dropped_and_overlaps_clipped():
    outside = ics(
        vevent(UID="l@test", DTSTART="20260701T080000Z", DTEND="20260701T090000Z", SUMMARY="Alt")
    )
    assert clip_and_dedupe(expand(outside), WINDOW_START, WINDOW_END) == []


def test_event_crossing_the_window_end_is_clipped():
    from app.events import Event

    crossing = Event(
        uid="m@test",
        summary="Lang",
        start=datetime(2026, 8, 9, 22, 0, tzinfo=TZ),
        end=datetime(2026, 8, 10, 4, 0, tzinfo=TZ),
        all_day=False,
        calendar="Privat",
    )
    clipped = clip_and_dedupe([crossing], WINDOW_START, WINDOW_END)
    assert len(clipped) == 1
    assert clipped[0].end == WINDOW_END


def test_split_by_day_cuts_at_midnight():
    from app.events import Event

    overnight = Event(
        uid="n@test",
        summary="Nachtschicht",
        start=datetime(2026, 8, 4, 22, 0, tzinfo=TZ),
        end=datetime(2026, 8, 5, 6, 0, tzinfo=TZ),
        all_day=False,
        calendar="Privat",
    )
    segments = split_by_day(overnight, TZ)
    assert len(segments) == 2
    assert segments[0][0].isoformat() == "2026-08-04"
    assert segments[1][0].isoformat() == "2026-08-05"
    assert segments[1][2].hour == 6


def test_dst_end_day_keeps_wall_clock_times():
    # 2026-10-25 is the end of CEST; the clock goes 03:00 -> 02:00.
    start = datetime(2026, 10, 19, 0, 0, tzinfo=TZ)
    end = datetime(2026, 10, 26, 0, 0, tzinfo=TZ)
    text = ics(
        vevent(
            UID="o@test",
            DTSTART="20261025T093000",
            DTEND="20261025T103000",
            SUMMARY="Brunch",
        )
    )
    events = expand_calendar(text, "Privat", start, end, TZ)
    assert len(events) == 1
    assert events[0].start.hour == 9
    assert events[0].end.hour == 10
