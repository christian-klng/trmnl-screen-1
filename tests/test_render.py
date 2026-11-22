import io
from dataclasses import replace
from datetime import date

import pytest
from PIL import Image, ImageDraw

from app.render import (
    BOLD,
    HEIGHT,
    MEDIUM,
    SEMIBOLD,
    SIDE_WIDTH,
    WEEKDAYS,
    WIDTH,
    deg,
    draw_icon,
    fitted,
    millimetres,
    rain_line,
    render_message,
    render_weather,
    to_bmp_bytes,
    to_device_bytes,
    to_png_bytes,
    wind_line,
)
from app.sample import sample_forecast
from app.weather import SEVERITY, parse_forecast

# A Thursday: the longest weekday is the one shown large.
THURSDAY = date(2026, 11, 26)
DAYS = parse_forecast(sample_forecast(THURSDAY))
MAIN = DAYS[THURSDAY]
SIDE = DAYS[date(2026, 11, 27)]


def screen(**kwargs) -> Image.Image:
    return render_weather(MAIN, SIDE, **kwargs)


def test_fonts_are_vendored():
    for path in (BOLD, SEMIBOLD, MEDIUM):
        assert path.exists(), "fonts must ship with the repo for deterministic rendering"


def test_render_produces_a_valid_one_bit_bmp():
    payload = to_bmp_bytes(screen())
    assert payload[:2] == b"BM"
    with Image.open(io.BytesIO(payload)) as decoded:
        assert decoded.size == (WIDTH, HEIGHT)
        assert decoded.mode == "1"


def test_rendered_screen_has_both_black_and_white_pixels():
    mono = screen().convert("1", dither=Image.Dither.NONE)
    assert {c for _, c in mono.getcolors()} == {0, 255}


def test_identical_input_renders_byte_identical_output():
    """The digest is the device's cache key, so rendering must be deterministic."""
    assert to_png_bytes(screen()) == to_png_bytes(screen())


def test_a_stale_forecast_is_marked():
    assert to_png_bytes(screen(stale_note="Daten von 14:00 · offline")) != to_png_bytes(screen())


def test_every_weekday_fits_the_narrow_column():
    for name in WEEKDAYS:
        assert fitted(BOLD, 62, name, SIDE_WIDTH).getlength(name) <= SIDE_WIDTH


def test_the_longest_weekday_keeps_its_full_size_on_the_left():
    """Donnerstag at 108 px is the whole point of the screen."""
    assert fitted(BOLD, 108, "Donnerstag", 486).size == 108


def test_extreme_values_render():
    hot = replace(MAIN, temp_max=38, feel_max=41, temp_min=24, wind=88, gusts=130, precip_sum=56.0)
    frost = replace(SIDE, temp_max=-14, temp_min=-21, feel_max=-22)
    assert to_png_bytes(render_weather(hot, frost))[:8] == b"\x89PNG\r\n\x1a\n"


def test_a_part_of_the_day_without_data_renders():
    patchy = replace(MAIN, parts=(None, *MAIN.parts[1:]))
    assert to_png_bytes(render_weather(patchy, SIDE)) != to_png_bytes(screen())


@pytest.mark.parametrize("kind", [*SEVERITY, "moon", "partly_night", "unknown"])
def test_every_icon_draws_something(kind):
    image = Image.new("L", (100, 100), 255)
    draw_icon(ImageDraw.Draw(image), kind, 0, 0, 100)
    assert image.getextrema() == (0, 255)


def test_temperatures_use_a_real_minus_sign():
    assert deg(-3) == "−3°"
    assert deg(0) == "0°"
    assert deg(12) == "12°"


def test_rain_amounts_read_naturally():
    assert millimetres(0.0) == "0 mm"
    assert millimetres(0.3) == "0,3 mm"
    assert millimetres(4.2) == "4 mm"
    assert millimetres(2.5) == "3 mm"


def test_rain_line_without_a_probability():
    assert rain_line(replace(MAIN, precip_probability=None)) == "Regen 4 mm"
    assert rain_line(MAIN) == "Regen 70 % · 4 mm"


def test_wind_line_shows_gusts_only_when_they_add_something():
    assert wind_line(MAIN) == "Wind 25–45 km/h"
    assert wind_line(replace(MAIN, gusts=None)) == "Wind 25 km/h"
    assert wind_line(replace(MAIN, gusts=25)) == "Wind 25 km/h"


def test_message_screen_renders():
    payload = to_bmp_bytes(render_message("Titel", ["Zeile eins", "Zeile zwei"]))
    with Image.open(io.BytesIO(payload)) as decoded:
        assert decoded.size == (WIDTH, HEIGHT)
        assert decoded.mode == "1"


# --- what the device actually downloads ------------------------------------
#
# The firmware's BMP branch pins its partial-update counter to 1 on every draw,
# so the "full refresh every 8 partials" test two lines later can never be
# true and the panel is never wiped. PNG leaves the counter alone — and is two
# orders of magnitude smaller on a battery-powered radio.


def test_png_is_one_bit_greyscale_without_a_palette():
    """The polarity the firmware assumes.

    It copies these bits straight into the panel plane and only consults a
    palette when there is one. A palette-carrying PNG would be read through a
    different branch, so the encoding matters, not just the pixels.
    """
    import struct

    payload = to_png_bytes(screen())

    assert payload[:8] == b"\x89PNG\r\n\x1a\n"
    width, height, bit_depth, colour_type = struct.unpack_from(">IIBB", payload, 16)
    assert (width, height) == (WIDTH, HEIGHT)
    assert bit_depth == 1
    assert colour_type == 0  # greyscale, so a set bit is white
    assert b"PLTE" not in payload


def test_png_and_bmp_carry_the_same_pixels():
    image = screen()
    with (
        Image.open(io.BytesIO(to_png_bytes(image))) as png,
        Image.open(io.BytesIO(to_bmp_bytes(image))) as bmp,
    ):
        assert png.convert("1").tobytes() == bmp.convert("1").tobytes()


def test_png_is_far_smaller_than_the_bmp():
    image = screen()
    # The BMP is a fixed 48062 bytes whatever is on the screen.
    assert len(to_png_bytes(image)) * 4 < len(to_bmp_bytes(image))


def test_to_device_bytes_picks_the_configured_format():
    image = render_message("Titel", [])
    assert to_device_bytes(image, "png")[:8] == b"\x89PNG\r\n\x1a\n"
    assert to_device_bytes(image, "bmp")[:2] == b"BM"
