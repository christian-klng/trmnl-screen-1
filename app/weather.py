"""The forecast: fetched from Open-Meteo and reduced to what the screen shows.

Open-Meteo is free for this kind of use, needs no key, and for Germany its
default model blend is DWD's ICON. One request returns everything: daily
figures for the headline and hourly ones for the four parts of the day.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date, datetime
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .config import Settings

DAILY = (
    "weather_code",
    "temperature_2m_max",
    "temperature_2m_min",
    "apparent_temperature_max",
    "precipitation_sum",
    "precipitation_probability_max",
    "wind_speed_10m_max",
    "wind_gusts_10m_max",
)
HOURLY = ("temperature_2m", "apparent_temperature", "weather_code", "is_day")

# Today plus two: after the evening switch the screen shows tomorrow and the
# day after.
FORECAST_DAYS = 3


@dataclass(frozen=True)
class DayPart:
    label: str
    start_hour: int
    end_hour: int  # exclusive


PARTS = (
    DayPart("FRÜH", 6, 10),
    DayPart("MITTAG", 10, 14),
    DayPart("NACHMITTAG", 14, 18),
    DayPart("ABEND", 18, 22),
)


@dataclass(frozen=True)
class PartForecast:
    label: str
    icon: str
    temp: int
    feel: int


@dataclass(frozen=True)
class DayForecast:
    day: date
    icon: str
    text: str
    temp_max: int
    temp_min: int
    feel_max: int
    precip_probability: int | None
    precip_sum: float
    wind: int
    gusts: int | None
    # One entry per PARTS, None where the forecast has no hours for it.
    parts: tuple[PartForecast | None, ...]


# --- WMO weather codes -------------------------------------------------------

ICON_BY_CODE = {
    0: "clear", 1: "clear", 2: "partly", 3: "cloud",
    45: "fog", 48: "fog",
    51: "rain", 53: "rain", 55: "rain", 56: "rain", 57: "rain",
    61: "rain", 63: "rain", 80: "rain", 81: "rain",
    65: "heavyrain", 66: "heavyrain", 67: "heavyrain", 82: "heavyrain",
    71: "snow", 73: "snow", 75: "snow", 77: "snow", 85: "snow", 86: "snow",
    95: "thunder", 96: "thunder", 99: "thunder",
}

TEXT_BY_CODE = {
    0: "Sonnig", 1: "Überwiegend sonnig", 2: "Teils bewölkt", 3: "Bedeckt",
    45: "Nebel", 48: "Nebel mit Reif",
    51: "Leichter Nieselregen", 53: "Nieselregen", 55: "Starker Nieselregen",
    56: "Gefrierender Niesel", 57: "Gefrierender Niesel",
    61: "Leichter Regen", 63: "Regen", 65: "Starker Regen",
    66: "Gefrierender Regen", 67: "Gefrierender Regen",
    71: "Leichter Schneefall", 73: "Schneefall", 75: "Starker Schneefall", 77: "Schneegriesel",
    80: "Leichte Regenschauer", 81: "Regenschauer", 82: "Heftige Regenschauer",
    85: "Leichte Schneeschauer", 86: "Schneeschauer",
    95: "Gewitter", 96: "Gewitter mit Hagel", 99: "Gewitter mit Hagel",
}

# Worst last. A part of the day takes the worst weather of its hours: one
# rainy hour out of four is the one that decides whether to take an umbrella.
SEVERITY = ("clear", "partly", "cloud", "fog", "rain", "heavyrain", "snow", "thunder")
NIGHT_ICON = {"clear": "moon", "partly": "partly_night"}


def icon_for(code: int | None) -> str:
    return ICON_BY_CODE.get(code, "cloud") if code is not None else "cloud"


def text_for(code: int | None) -> str:
    return TEXT_BY_CODE.get(code, "") if code is not None else ""


def round_half_up(value: float) -> int:
    """Round as people do: 2.5 is 3 and -2.5 is -2.

    Python's round() rounds halves to even, so 0.5° and 1.5° would both
    display as 2°, which reads like a bug on a thermometer.
    """
    return math.floor(value + 0.5)


# --- fetching ------------------------------------------------------------------


def forecast_url(settings: Settings) -> str:
    params = {
        "latitude": f"{settings.latitude:.4f}",
        "longitude": f"{settings.longitude:.4f}",
        "timezone": settings.timezone,
        "forecast_days": FORECAST_DAYS,
        "daily": ",".join(DAILY),
        "hourly": ",".join(HOURLY),
    }
    return f"{settings.weather_url}?{urlencode(params)}"


def fetch_forecast(settings: Settings) -> dict:
    """Fetch the raw forecast. Blocking; run it in a thread.

    The response is parsed once here before it is returned, so that a
    malformed answer fails the fetch rather than overwriting the last good copy.
    """
    request = Request(forecast_url(settings), headers={"User-Agent": "trmnl-screen-1"})
    with urlopen(request, timeout=20) as response:
        raw = json.load(response)
    parse_forecast(raw)
    return raw


# --- parsing -------------------------------------------------------------------


def _series(block: dict, names: tuple[str, ...], kind: str) -> dict[str, list]:
    times = block.get("time")
    if not isinstance(times, list) or not times:
        raise ValueError(f"forecast has no {kind} times")
    series = {"time": times}
    for name in names:
        values = block.get(name)
        if not isinstance(values, list) or len(values) != len(times):
            raise ValueError(f"forecast is missing {kind} {name}")
        series[name] = values
    return series


def _part(part: DayPart, hours: list[dict]) -> PartForecast | None:
    inside = [
        h for h in hours
        if part.start_hour <= h["hour"] < part.end_hour and h["temp"] is not None
    ]
    if not inside:
        return None

    temps = [h["temp"] for h in inside]
    feels = [h["feel"] if h["feel"] is not None else h["temp"] for h in inside]
    if part is PARTS[0]:
        # The early part stands in for the day's low, so it shows its coldest
        # hour rather than an average.
        temp, feel = min(temps), min(feels)
    else:
        temp, feel = sum(temps) / len(temps), sum(feels) / len(feels)

    icon = max((icon_for(h["code"]) for h in inside), key=SEVERITY.index)
    night_hours = sum(1 for h in inside if h["is_day"] == 0)
    if night_hours * 2 > len(inside):
        icon = NIGHT_ICON.get(icon, icon)

    return PartForecast(part.label, icon, round_half_up(temp), round_half_up(feel))


def parse_forecast(raw: dict) -> dict[date, DayForecast]:
    """Turn an Open-Meteo response into one DayForecast per day.

    Times in the response are already local, because the request names the
    time zone.

    Raises ValueError for anything that is not a usable forecast. A day whose
    headline figures are missing is left out rather than drawn half empty.
    """
    if not isinstance(raw, dict):
        raise ValueError("forecast is not a JSON object")
    if raw.get("error"):
        raise ValueError(f"Open-Meteo: {raw.get('reason', 'unknown error')}")
    daily = _series(raw.get("daily") or {}, DAILY, "daily")
    hourly = _series(raw.get("hourly") or {}, HOURLY, "hourly")

    hours_by_day: dict[date, list[dict]] = {}
    for i, stamp in enumerate(hourly["time"]):
        moment = datetime.fromisoformat(stamp)
        hours_by_day.setdefault(moment.date(), []).append(
            {
                "hour": moment.hour,
                "temp": hourly["temperature_2m"][i],
                "feel": hourly["apparent_temperature"][i],
                "code": hourly["weather_code"][i],
                "is_day": hourly["is_day"][i],
            }
        )

    days: dict[date, DayForecast] = {}
    for i, stamp in enumerate(daily["time"]):
        day = date.fromisoformat(stamp)
        temp_max = daily["temperature_2m_max"][i]
        temp_min = daily["temperature_2m_min"][i]
        if temp_max is None or temp_min is None:
            continue
        feel_max = daily["apparent_temperature_max"][i]
        probability = daily["precipitation_probability_max"][i]
        gusts = daily["wind_gusts_10m_max"][i]
        code = daily["weather_code"][i]
        hours = hours_by_day.get(day, [])

        days[day] = DayForecast(
            day=day,
            icon=icon_for(code),
            text=text_for(code),
            temp_max=round_half_up(temp_max),
            temp_min=round_half_up(temp_min),
            feel_max=round_half_up(feel_max if feel_max is not None else temp_max),
            precip_probability=round_half_up(probability) if probability is not None else None,
            precip_sum=float(daily["precipitation_sum"][i] or 0.0),
            wind=round_half_up(daily["wind_speed_10m_max"][i] or 0.0),
            gusts=round_half_up(gusts) if gusts is not None else None,
            parts=tuple(_part(part, hours) for part in PARTS),
        )

    if not days:
        raise ValueError("forecast contains no complete day")
    return days
