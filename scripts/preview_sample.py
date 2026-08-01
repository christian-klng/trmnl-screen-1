"""Render a sample week to PNG, for layout work without a device or a server.

    python scripts/preview_sample.py [out.png]
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.events import Event  # noqa: E402
from app.layout import build_layout  # noqa: E402
from app.render import render_week, to_png_bytes  # noqa: E402
from app.week import current_week  # noqa: E402

TZ = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 8, 5, 10, 37, tzinfo=TZ)
MONDAY = datetime(2026, 8, 3, 0, 0, tzinfo=TZ)

# A deliberately awkward week: parallel meetings, long titles, all-day events,
# an early outlier and an evening event.
SAMPLE = [
    (0, 8.5, 9.0, "Standup"),
    (0, 9.0, 10.5, "Projektbesprechung Migration"),
    (0, 10.0, 11.0, "1:1 Anna"),
    (0, 13.0, 14.0, "Mittagessen"),
    (0, 16.0, 17.5, "Review Q3"),
    (1, 8.5, 9.0, "Standup"),
    (1, 9.5, 12.0, "Workshop Architektur"),
    (1, 14.0, 15.0, "Zahnarzt"),
    (1, 14.5, 16.0, "Telko Lieferant"),
    (2, 6.0, 7.0, "Frühflug HAM"),
    (2, 8.5, 9.0, "Standup"),
    (2, 11.0, 12.0, "Kundentermin Nord"),
    (2, 15.0, 16.0, "Retro"),
    (3, 8.5, 9.0, "Standup"),
    (3, 9.0, 17.0, "Offsite Team"),
    (4, 8.5, 9.0, "Standup"),
    (4, 10.0, 11.0, "Planung nächste Woche"),
    (4, 11.0, 12.0, "Freitagsrunde"),
    (5, 10.0, 13.0, "Umzug Keller"),
    (6, 11.0, 13.0, "Brunch mit Familie"),
    (6, 19.0, 21.0, "Tatort"),
]

ALL_DAY = [
    (3, 2, "Konferenz Berlin"),
    (5, 2, "Wochenende Ostsee"),
]


def build_events() -> list[Event]:
    events: list[Event] = []
    for day, start_h, end_h, summary in SAMPLE:
        base = MONDAY + timedelta(days=day)
        events.append(
            Event(
                uid=f"{day}-{start_h}-{summary}",
                summary=summary,
                start=base + timedelta(hours=start_h),
                end=base + timedelta(hours=end_h),
                all_day=False,
                calendar="Arbeit",
            )
        )
    for day, span, summary in ALL_DAY:
        base = MONDAY + timedelta(days=day)
        events.append(
            Event(
                uid=f"ad-{day}-{summary}",
                summary=summary,
                start=base,
                end=base + timedelta(days=span),
                all_day=True,
                calendar="Privat",
            )
        )
    return events


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("preview.png")
    window = current_week(NOW, TZ)
    layout = build_layout(build_events(), window, TZ, generated_at=NOW)
    image = render_week(layout, NOW, TZ)
    out.write_bytes(to_png_bytes(image))
    print(
        f"{out}  window {layout.start_hour:.0f}-{layout.end_hour:.0f}h  "
        f"{len(layout.blocks)} blocks  {layout.all_day_rows} all-day rows  "
        f"early={layout.overflow_early} late={layout.overflow_late}"
    )


if __name__ == "__main__":
    main()
