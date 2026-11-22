"""An example Open-Meteo response, for previews and tests.

Shaped exactly like the real answer, so it goes through the same parser as the
live forecast. The figures are the ones from the mockup: a grey, wet start, a
stormy second day and a clear, frosty third.
"""

from __future__ import annotations

from datetime import date, timedelta

from .weather import DAILY, HOURLY, PARTS

# daily: in the order of weather.DAILY — code, max, min, felt max, mm,
# probability %, wind, gusts
# parts: (code, temperature, felt) for FRÜH, MITTAG, NACHMITTAG, ABEND
PROFILES = [
    {
        "daily": (61, 7.2, 0.8, 4.1, 4.2, 70, 25.0, 45.0),
        "parts": [(3, 2.0, -1.0), (3, 6.0, 3.0), (61, 7.0, 4.0), (61, 4.0, 1.0)],
    },
    {
        "daily": (65, 9.0, 3.1, 5.0, 8.0, 85, 35.0, 60.0),
        "parts": [(61, 4.0, 0.0), (65, 7.0, 3.0), (61, 9.0, 5.0), (3, 6.0, 3.0)],
    },
    {
        "daily": (2, 5.0, -2.0, 3.0, 0.0, 10, 10.0, 20.0),
        "parts": [(0, -1.0, -5.0), (2, 3.0, 0.0), (0, 5.0, 3.0), (0, 1.0, -2.0)],
    },
]

# Daylight in late November, roughly 08:00 to 16:00 in Berlin.
SUNRISE_HOUR = 8
SUNSET_HOUR = 16


def sample_forecast(start: date, days: int = 3) -> dict:
    daily: dict[str, list] = {"time": [], **{name: [] for name in DAILY}}
    hourly: dict[str, list] = {"time": [], **{name: [] for name in HOURLY}}

    for offset in range(days):
        day = start + timedelta(days=offset)
        profile = PROFILES[offset % len(PROFILES)]
        values = dict(zip(DAILY, profile["daily"], strict=True))
        daily["time"].append(day.isoformat())
        for name in DAILY:
            daily[name].append(values[name])

        low = values["temperature_2m_min"]
        for hour in range(24):
            code, temp, feel = 3, low, low - 3
            for part, (part_code, part_temp, part_feel) in zip(PARTS, profile["parts"], strict=True):
                if part.start_hour <= hour < part.end_hour:
                    code, temp, feel = part_code, part_temp, part_feel
            hourly["time"].append(f"{day.isoformat()}T{hour:02d}:00")
            hourly["temperature_2m"].append(temp)
            hourly["apparent_temperature"].append(feel)
            hourly["weather_code"].append(code)
            hourly["is_day"].append(1 if SUNRISE_HOUR <= hour < SUNSET_HOUR else 0)

    return {
        "latitude": 52.49,
        "longitude": 13.47,
        "timezone": "Europe/Berlin",
        "daily": daily,
        "hourly": hourly,
    }
