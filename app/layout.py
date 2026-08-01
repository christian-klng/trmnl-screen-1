"""Turning events into positioned blocks: adaptive time window and overlap packing."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from .events import Event, split_by_day
from .week import WeekWindow

MAX_ALL_DAY_ROWS = 3


@dataclass(frozen=True)
class Block:
    """A timed event as drawn inside one day column."""

    event: Event
    day_index: int
    start_hour: float
    end_hour: float
    column: int
    columns: int


@dataclass(frozen=True)
class AllDayBar:
    event: Event
    first_day: int
    last_day: int  # inclusive
    row: int


@dataclass
class WeekLayout:
    window: WeekWindow
    start_hour: float
    end_hour: float
    blocks: list[Block] = field(default_factory=list)
    all_day_bars: list[AllDayBar] = field(default_factory=list)
    all_day_rows: int = 0
    all_day_overflow: dict[int, int] = field(default_factory=dict)
    overflow_early: dict[int, int] = field(default_factory=dict)
    overflow_late: dict[int, int] = field(default_factory=dict)
    stale_calendars: list[str] = field(default_factory=list)
    generated_at: datetime | None = None

    @property
    def span_hours(self) -> float:
        return self.end_hour - self.start_hour


def _hour_of(moment: datetime, day) -> float:
    """Wall-clock hour within ``day``; midnight of the following day is 24.0."""
    if moment.date() > day:
        return 24.0
    return moment.hour + moment.minute / 60 + moment.second / 3600


def _segments(events: list[Event], window: WeekWindow, tz: ZoneInfo):
    """Per-day segments of timed events, as (day_index, start_hour, end_hour, event)."""
    for event in events:
        if event.all_day:
            continue
        for day, seg_start, seg_end in split_by_day(event, tz):
            index = window.index_of(day)
            if index is None:
                continue
            start_hour = _hour_of(seg_start, day)
            end_hour = _hour_of(seg_end, day)
            if end_hour <= start_hour:
                continue
            yield index, start_hour, end_hour, event


def choose_time_window(
    segments: list[tuple[int, float, float, Event]],
    min_hours: int,
    max_hours: int,
) -> tuple[float, float]:
    """Pick the visible hour range.

    Zooming to the occupied span is the whole point of an adaptive grid, but two
    guards are needed: a minimum span so a near-empty week is not absurdly
    stretched, and a maximum span so a single 04:00 flight cannot wreck the
    resolution of every other day. Events outside the chosen window are reported
    as edge markers rather than widening it.
    """
    if not segments:
        return 8.0, 8.0 + min_hours

    earliest = min(s[1] for s in segments)
    latest = max(s[2] for s in segments)
    start = float(int(earliest))  # floor to the full hour
    end = float(-(-latest // 1))  # ceil to the full hour

    if end - start < min_hours:
        # Grow symmetrically, but keep the boundaries on full hours so the
        # gridlines stay aligned with the hour labels.
        centre = (start + end) / 2
        start = float(int(centre - min_hours / 2))
        end = start + min_hours
        if start < 0:
            start, end = 0.0, float(min_hours)
        if end > 24:
            start, end = 24.0 - min_hours, 24.0
        return start, end

    if end - start > max_hours:
        # Slide a max_hours window over the day and keep the position that
        # covers the most event minutes.
        best_start, best_covered = start, -1.0
        for candidate in range(0, 24 - max_hours + 1):
            c_start = float(candidate)
            c_end = c_start + max_hours
            covered = sum(
                max(0.0, min(seg_end, c_end) - max(seg_start, c_start))
                for _, seg_start, seg_end, _ in segments
            )
            if covered > best_covered:
                best_covered, best_start = covered, c_start
        return best_start, best_start + max_hours

    return start, end


PackedSegment = tuple[int, int, float, float, Event]


def _pack_columns(day_segments: list[tuple[float, float, Event]]) -> list[PackedSegment]:
    """Assign side-by-side columns to overlapping events within one day."""
    ordered = sorted(day_segments, key=lambda s: (s[0], s[1]))
    packed: list[PackedSegment] = []

    cluster: list[tuple[float, float, Event]] = []
    cluster_end = -1.0

    def flush(group: list[tuple[float, float, Event]]) -> None:
        if not group:
            return
        column_ends: list[float] = []
        assigned: list[tuple[int, float, float, Event]] = []
        for start, end, event in group:
            placed = False
            for index, last_end in enumerate(column_ends):
                if start >= last_end:
                    column_ends[index] = end
                    assigned.append((index, start, end, event))
                    placed = True
                    break
            if not placed:
                column_ends.append(end)
                assigned.append((len(column_ends) - 1, start, end, event))
        total = len(column_ends)
        for column, start, end, event in assigned:
            packed.append((column, total, start, end, event))

    for start, end, event in ordered:
        if cluster and start >= cluster_end:
            flush(cluster)
            cluster = []
            cluster_end = -1.0
        cluster.append((start, end, event))
        cluster_end = max(cluster_end, end)
    flush(cluster)

    return packed


def _pack_all_day(events: list[Event], window: WeekWindow) -> tuple[list[AllDayBar], int, dict[int, int]]:
    bars: list[AllDayBar] = []
    overflow: dict[int, int] = {}
    rows: list[list[tuple[int, int]]] = []

    candidates = []
    for event in events:
        if not event.all_day:
            continue
        first_index = window.index_of(event.start.date())
        first = 0 if first_index is None else first_index
        # DTEND is exclusive, so the last covered day is one step back. An end
        # outside the window means the bar runs to Sunday.
        end_index = window.index_of(event.end.date())
        last = 6 if end_index is None else max(first, end_index - 1)
        candidates.append((first, last, event))
    candidates.sort(key=lambda c: (c[0], -(c[1] - c[0]), c[2].summary))

    for first, last, event in candidates:
        placed = False
        for row_index, occupied in enumerate(rows):
            if all(last < s or first > e for s, e in occupied):
                occupied.append((first, last))
                bars.append(AllDayBar(event=event, first_day=first, last_day=last, row=row_index))
                placed = True
                break
        if placed:
            continue
        if len(rows) < MAX_ALL_DAY_ROWS:
            rows.append([(first, last)])
            bars.append(AllDayBar(event=event, first_day=first, last_day=last, row=len(rows) - 1))
        else:
            for day in range(first, last + 1):
                overflow[day] = overflow.get(day, 0) + 1

    return bars, len(rows), overflow


def build_layout(
    events: list[Event],
    window: WeekWindow,
    tz: ZoneInfo,
    min_hours: int = 8,
    max_hours: int = 15,
    stale_calendars: list[str] | None = None,
    generated_at: datetime | None = None,
) -> WeekLayout:
    segments = list(_segments(events, window, tz))
    start_hour, end_hour = choose_time_window(segments, min_hours, max_hours)

    per_day: dict[int, list[tuple[float, float, Event]]] = {}
    overflow_early: dict[int, int] = {}
    overflow_late: dict[int, int] = {}

    for day_index, seg_start, seg_end, event in segments:
        if seg_end <= start_hour:
            overflow_early[day_index] = overflow_early.get(day_index, 0) + 1
            continue
        if seg_start >= end_hour:
            overflow_late[day_index] = overflow_late.get(day_index, 0) + 1
            continue
        per_day.setdefault(day_index, []).append(
            (max(seg_start, start_hour), min(seg_end, end_hour), event)
        )

    blocks: list[Block] = []
    for day_index, day_segments in per_day.items():
        for column, columns, seg_start, seg_end, event in _pack_columns(day_segments):
            blocks.append(
                Block(
                    event=event,
                    day_index=day_index,
                    start_hour=seg_start,
                    end_hour=seg_end,
                    column=column,
                    columns=columns,
                )
            )

    bars, rows, all_day_overflow = _pack_all_day(events, window)

    return WeekLayout(
        window=window,
        start_hour=start_hour,
        end_hour=end_hour,
        blocks=blocks,
        all_day_bars=bars,
        all_day_rows=rows,
        all_day_overflow=all_day_overflow,
        overflow_early=overflow_early,
        overflow_late=overflow_late,
        stale_calendars=stale_calendars or [],
        generated_at=generated_at,
    )
