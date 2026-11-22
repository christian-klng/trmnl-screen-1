"""Which two days the screen shows."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


def shown_days(now: datetime, tz: ZoneInfo, switch_hour: int = 20) -> tuple[date, date]:
    """The day shown large and the day after it.

    Normally today and tomorrow. From ``switch_hour`` on, the evening belongs
    to the next day: what matters at the kitchen table at 21:00 is tomorrow's
    weather, not the rest of tonight's. ``switch_hour`` 24 never switches early.

    The screen names these days by weekday, never as "today" or "tomorrow". An
    e-ink image stays up across midnight while the device sleeps, and a label
    that was right at 20:00 would be wrong by 00:01. A weekday stays right.
    """
    local = now.astimezone(tz)
    main = local.date()
    if local.hour >= switch_hour:
        main += timedelta(days=1)
    return main, main + timedelta(days=1)
