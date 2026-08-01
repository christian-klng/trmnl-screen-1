from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.events import Event
from app.layout import build_layout, choose_time_window
from app.week import current_week

TZ = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 8, 5, 9, 0, tzinfo=TZ)
WEEK = current_week(NOW, TZ)


def event(day: int, start_h: float, end_h: float, summary: str = "Termin", uid: str | None = None) -> Event:
    base = datetime(2026, 8, 3, 0, 0, tzinfo=TZ) + timedelta(days=day)
    return Event(
        uid=uid or f"{day}-{start_h}-{summary}",
        summary=summary,
        start=base + timedelta(hours=start_h),
        end=base + timedelta(hours=end_h),
        all_day=False,
        calendar="Privat",
    )


def all_day(first_day: int, days: int = 1, summary: str = "Urlaub") -> Event:
    base = datetime(2026, 8, 3, 0, 0, tzinfo=TZ) + timedelta(days=first_day)
    return Event(
        uid=f"ad-{first_day}-{summary}",
        summary=summary,
        start=base,
        end=base + timedelta(days=days),
        all_day=True,
        calendar="Privat",
    )


# --- adaptive time window -------------------------------------------------


def test_empty_week_uses_the_minimum_span():
    start, end = choose_time_window([], min_hours=8, max_hours=15)
    assert end - start == 8


def test_window_hugs_the_occupied_span():
    segments = [(0, 9.0, 10.0, None), (2, 14.0, 17.5, None)]
    start, end = choose_time_window(segments, min_hours=4, max_hours=15)
    assert (start, end) == (9.0, 18.0)


def test_narrow_span_is_widened_to_the_minimum_on_full_hours():
    segments = [(0, 10.0, 11.0, None)]
    start, end = choose_time_window(segments, min_hours=8, max_hours=15)
    assert end - start == 8
    assert start == int(start)
    assert start <= 10.0 and end >= 11.0


def test_outlier_does_not_stretch_the_window_beyond_the_maximum():
    # One 04:00 event plus a normal working day.
    segments = [(0, 4.0, 5.0, None)] + [(d, 9.0, 17.0, None) for d in range(5)]
    start, end = choose_time_window(segments, min_hours=8, max_hours=10)
    assert end - start == 10
    # The dense 09-17 band must win over the lone early event.
    assert start <= 9.0 and end >= 17.0


def test_window_stays_inside_the_day():
    segments = [(0, 23.0, 24.0, None)]
    start, end = choose_time_window(segments, min_hours=8, max_hours=15)
    assert start >= 0.0
    assert end <= 24.0


# --- overlap packing ------------------------------------------------------


def test_sequential_events_get_a_full_column_each():
    layout = build_layout([event(0, 9, 10), event(0, 10, 11)], WEEK, TZ)
    assert {b.columns for b in layout.blocks} == {1}


def test_overlapping_events_are_placed_side_by_side():
    layout = build_layout([event(0, 9, 11, "A"), event(0, 10, 12, "B")], WEEK, TZ)
    assert len(layout.blocks) == 2
    assert {b.columns for b in layout.blocks} == {2}
    assert {b.column for b in layout.blocks} == {0, 1}


def test_three_way_overlap():
    layout = build_layout(
        [event(0, 9, 12, "A"), event(0, 9.5, 11, "B"), event(0, 10, 12, "C")], WEEK, TZ
    )
    assert {b.columns for b in layout.blocks} == {3}
    assert {b.column for b in layout.blocks} == {0, 1, 2}


def test_a_gap_starts_a_new_cluster():
    # A and B overlap; C is later and alone, so it must not be narrowed.
    layout = build_layout(
        [event(0, 9, 11, "A"), event(0, 10, 12, "B"), event(0, 14, 15, "C")], WEEK, TZ
    )
    by_summary = {b.event.summary: b for b in layout.blocks}
    assert by_summary["A"].columns == 2
    assert by_summary["C"].columns == 1


def test_events_land_in_the_right_day_column():
    layout = build_layout([event(0, 9, 10, "Mo"), event(6, 9, 10, "So")], WEEK, TZ)
    days = {b.event.summary: b.day_index for b in layout.blocks}
    assert days == {"Mo": 0, "So": 6}


def test_overnight_event_appears_on_both_days():
    overnight = Event(
        uid="night",
        summary="Nachtschicht",
        start=datetime(2026, 8, 4, 22, 0, tzinfo=TZ),
        end=datetime(2026, 8, 5, 6, 0, tzinfo=TZ),
        all_day=False,
        calendar="Privat",
    )
    layout = build_layout([overnight], WEEK, TZ, min_hours=24, max_hours=24)
    assert sorted(b.day_index for b in layout.blocks) == [1, 2]


# --- overflow markers -----------------------------------------------------


def test_events_outside_the_window_become_edge_markers():
    events = [event(0, 3, 4, "Früh")] + [event(d, 9, 17) for d in range(5)]
    layout = build_layout(events, WEEK, TZ, min_hours=8, max_hours=8)
    assert layout.overflow_early.get(0) == 1
    assert not any(b.event.summary == "Früh" for b in layout.blocks)


# --- all-day band ---------------------------------------------------------


def test_all_day_event_spans_the_right_columns():
    layout = build_layout([all_day(1, days=3)], WEEK, TZ)
    assert len(layout.all_day_bars) == 1
    bar = layout.all_day_bars[0]
    assert (bar.first_day, bar.last_day) == (1, 3)


def test_single_day_all_day_event():
    layout = build_layout([all_day(2, days=1)], WEEK, TZ)
    bar = layout.all_day_bars[0]
    assert (bar.first_day, bar.last_day) == (2, 2)


def test_overlapping_all_day_events_stack_into_rows():
    layout = build_layout([all_day(0, 4, "A"), all_day(1, 4, "B")], WEEK, TZ)
    assert layout.all_day_rows == 2
    assert {b.row for b in layout.all_day_bars} == {0, 1}


def test_non_overlapping_all_day_events_share_a_row():
    layout = build_layout([all_day(0, 2, "A"), all_day(4, 2, "B")], WEEK, TZ)
    assert layout.all_day_rows == 1


def test_all_day_overflow_beyond_three_rows():
    events = [all_day(0, 7, f"A{i}") for i in range(5)]
    layout = build_layout(events, WEEK, TZ)
    assert layout.all_day_rows == 3
    assert layout.all_day_overflow.get(0) == 2


def test_all_day_events_are_not_in_the_time_grid():
    layout = build_layout([all_day(0, 1)], WEEK, TZ)
    assert layout.blocks == []
