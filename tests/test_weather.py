import copy
import json
from datetime import date
from urllib.parse import parse_qs, urlparse

import pytest

from app.config import Settings
from app.sample import sample_forecast
from app.weather import (
    DAILY,
    FORECAST_DAYS,
    HOURLY,
    PARTS,
    forecast_url,
    icon_for,
    parse_forecast,
    round_half_up,
    text_for,
)

START = date(2026, 11, 26)


def raw():
    return sample_forecast(START)


def test_every_day_is_parsed():
    days = parse_forecast(raw())
    assert sorted(days) == [date(2026, 11, 26), date(2026, 11, 27), date(2026, 11, 28)]


def test_headline_figures_are_rounded_as_people_round():
    day = parse_forecast(raw())[START]
    assert (day.temp_max, day.temp_min, day.feel_max) == (7, 1, 4)
    assert day.precip_probability == 70
    assert day.precip_sum == pytest.approx(4.2)
    assert (day.wind, day.gusts) == (25, 45)
    assert day.icon == "rain"
    assert day.text == "Leichter Regen"


def test_round_half_up():
    assert round_half_up(0.5) == 1
    assert round_half_up(1.5) == 2
    assert round_half_up(2.5) == 3
    assert round_half_up(-0.4) == 0
    assert round_half_up(-2.5) == -2
    assert round_half_up(-2.6) == -3


def test_the_early_part_shows_its_coldest_hour():
    data = raw()
    hourly = data["hourly"]
    # 06:00 to 09:00 on the first day: one cold hour among mild ones.
    first = hourly["time"].index("2026-11-26T06:00")
    for i, temp in enumerate([1.0, 4.0, 5.0, 6.0]):
        hourly["temperature_2m"][first + i] = temp
        hourly["apparent_temperature"][first + i] = temp - 3
    early = parse_forecast(data)[START].parts[0]
    assert (early.temp, early.feel) == (1, -2)


def test_later_parts_show_their_average():
    data = raw()
    hourly = data["hourly"]
    first = hourly["time"].index("2026-11-26T10:00")
    for i, temp in enumerate([4.0, 6.0, 8.0, 10.0]):
        hourly["temperature_2m"][first + i] = temp
    midday = parse_forecast(data)[START].parts[1]
    assert midday.temp == 7


def test_one_rainy_hour_makes_the_part_rainy():
    data = raw()
    hourly = data["hourly"]
    first = hourly["time"].index("2026-11-26T10:00")
    hourly["weather_code"][first : first + 4] = [0, 0, 63, 0]
    assert parse_forecast(data)[START].parts[1].icon == "rain"


def test_a_clear_evening_shows_the_moon():
    # The third sample day is clear in the evening, which is after dark.
    evening = parse_forecast(raw())[date(2026, 11, 28)].parts[3]
    assert evening.icon == "moon"


def test_a_partly_cloudy_night_keeps_the_moon():
    data = raw()
    hourly = data["hourly"]
    first = hourly["time"].index("2026-11-28T18:00")
    hourly["weather_code"][first : first + 4] = [2, 2, 2, 2]
    assert parse_forecast(data)[date(2026, 11, 28)].parts[3].icon == "partly_night"


def test_parts_follow_the_layout():
    assert [p.label for p in PARTS] == ["FRÜH", "MITTAG", "NACHMITTAG", "ABEND"]
    assert [(p.start_hour, p.end_hour) for p in PARTS] == [(6, 10), (10, 14), (14, 18), (18, 22)]


def test_a_part_without_hours_is_left_empty():
    data = raw()
    hourly = data["hourly"]
    for i, stamp in enumerate(hourly["time"]):
        if stamp.startswith("2026-11-26T0") and stamp >= "2026-11-26T06":
            hourly["temperature_2m"][i] = None
    assert parse_forecast(data)[START].parts[0] is None


def test_a_missing_probability_is_kept_as_unknown():
    data = raw()
    data["daily"]["precipitation_probability_max"][0] = None
    assert parse_forecast(data)[START].precip_probability is None


def test_an_incomplete_day_is_left_out():
    data = raw()
    data["daily"]["temperature_2m_max"][2] = None
    assert date(2026, 11, 28) not in parse_forecast(data)


@pytest.mark.parametrize(
    "broken",
    [
        {},
        {"error": True, "reason": "Latitude must be in range of -90 to 90°."},
        {"daily": {"time": []}, "hourly": {"time": []}},
    ],
)
def test_a_broken_response_is_rejected(broken):
    with pytest.raises(ValueError):
        parse_forecast(broken)


def test_a_missing_series_is_rejected():
    data = copy.deepcopy(raw())
    del data["hourly"]["apparent_temperature"]
    with pytest.raises(ValueError):
        parse_forecast(data)


def test_unknown_codes_fall_back_to_a_cloud():
    assert icon_for(42) == "cloud"
    assert icon_for(None) == "cloud"
    assert text_for(42) == ""


def test_the_request_asks_for_everything_the_parser_needs():
    settings = Settings(latitude=52.4886, longitude=13.4697, timezone="Europe/Berlin")
    query = parse_qs(urlparse(forecast_url(settings)).query)
    assert query["latitude"] == ["52.4886"]
    assert query["longitude"] == ["13.4697"]
    assert query["timezone"] == ["Europe/Berlin"]
    assert query["forecast_days"] == [str(FORECAST_DAYS)]
    assert query["daily"][0].split(",") == list(DAILY)
    assert query["hourly"][0].split(",") == list(HOURLY)


def test_the_sample_survives_a_json_round_trip():
    """The forecast is cached as JSON on the volume."""
    assert parse_forecast(json.loads(json.dumps(raw()))) == parse_forecast(raw())
