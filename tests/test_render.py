import io
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from PIL import Image, ImageFont

from app.events import Event
from app.layout import build_layout
from app.render import (
    BOLD,
    HEIGHT,
    REGULAR,
    WIDTH,
    column_x,
    fit_text,
    render_message,
    render_week,
    to_bmp_bytes,
    to_device_bytes,
    to_png_bytes,
    wrap_text,
)
from app.week import current_week

TZ = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 8, 5, 10, 30, tzinfo=TZ)
WEEK = current_week(NOW, TZ)


def event(day: int, start_h: float, end_h: float, summary: str = "Termin", all_day: bool = False) -> Event:
    base = datetime(2026, 8, 3, 0, 0, tzinfo=TZ) + timedelta(days=day)
    return Event(
        uid=f"{day}-{start_h}-{summary}",
        summary=summary,
        start=base + timedelta(hours=start_h),
        end=base + timedelta(hours=end_h),
        all_day=all_day,
        calendar="Privat",
    )


def test_fonts_are_vendored():
    assert REGULAR.exists(), "font must ship with the repo for deterministic rendering"
    assert BOLD.exists()


def test_column_boundaries_cover_the_full_width():
    assert column_x(0) == 32
    assert column_x(7) == WIDTH
    widths = [column_x(i + 1) - column_x(i) for i in range(7)]
    assert sum(widths) == WIDTH - 32
    # No column may be more than a pixel off from its neighbours.
    assert max(widths) - min(widths) <= 1


def test_fit_text_truncates_with_an_ellipsis():
    font = ImageFont.truetype(str(REGULAR), 11)
    assert fit_text("kurz", font, 200) == "kurz"
    long = fit_text("Projektbesprechung mit dem Team", font, 40)
    assert long.endswith("…")
    assert font.getlength(long) <= 40


def test_fit_text_handles_impossibly_narrow_boxes():
    font = ImageFont.truetype(str(REGULAR), 11)
    assert fit_text("Termin", font, 1) == ""


def test_wrap_text_respects_the_line_limit():
    font = ImageFont.truetype(str(REGULAR), 11)
    lines = wrap_text("Ein ziemlich langer Titel fuer eine schmale Spalte", font, 60, 2)
    assert len(lines) <= 2
    for line in lines:
        assert font.getlength(line) <= 60


def test_wrap_text_marks_dropped_words_with_an_ellipsis():
    font = ImageFont.truetype(str(REGULAR), 11)
    lines = wrap_text("Alpha Beta Gamma Delta Epsilon Zeta Eta Theta", font, 50, 1)
    assert len(lines) == 1
    assert lines[0].endswith("…")


def test_render_produces_a_valid_one_bit_bmp():
    events = [event(d, 9, 10, f"Tag {d}") for d in range(7)]
    layout = build_layout(events, WEEK, TZ, generated_at=NOW)
    image = render_week(layout, NOW, TZ)
    payload = to_bmp_bytes(image)

    assert payload[:2] == b"BM"
    with Image.open(io.BytesIO(payload)) as decoded:
        assert decoded.size == (WIDTH, HEIGHT)
        assert decoded.mode == "1"


def test_rendered_screen_has_both_black_and_white_pixels():
    layout = build_layout([event(0, 9, 10, "Standup")], WEEK, TZ, generated_at=NOW)
    mono = render_week(layout, NOW, TZ).convert("1", dither=Image.Dither.NONE)
    colours = {c for _, c in mono.getcolors()}
    assert colours == {0, 255}


def test_render_survives_an_empty_week():
    layout = build_layout([], WEEK, TZ, generated_at=NOW)
    payload = to_bmp_bytes(render_week(layout, NOW, TZ))
    with Image.open(io.BytesIO(payload)) as decoded:
        assert decoded.size == (WIDTH, HEIGHT)


def test_render_survives_a_heavily_overlapping_day():
    events = [event(2, 9 + i * 0.25, 12, f"Parallel {i}") for i in range(8)]
    layout = build_layout(events, WEEK, TZ, generated_at=NOW)
    payload = to_bmp_bytes(render_week(layout, NOW, TZ))
    assert payload[:2] == b"BM"


def test_render_handles_a_full_all_day_band():
    events = [event(d, 0, 24, f"Ganztag {d}", all_day=True) for d in range(5)]
    layout = build_layout(events, WEEK, TZ, generated_at=NOW)
    payload = to_bmp_bytes(render_week(layout, NOW, TZ))
    assert payload[:2] == b"BM"


def test_message_screen_renders():
    payload = to_bmp_bytes(render_message("Titel", ["Zeile eins", "Zeile zwei"]))
    with Image.open(io.BytesIO(payload)) as decoded:
        assert decoded.size == (WIDTH, HEIGHT)
        assert decoded.mode == "1"


def test_identical_input_renders_byte_identical_output():
    """The digest is the device's cache key, so rendering must be deterministic."""
    layout = build_layout([event(0, 9, 10, "Standup")], WEEK, TZ, generated_at=NOW)
    first = to_bmp_bytes(render_week(layout, NOW, TZ))
    second = to_bmp_bytes(render_week(layout, NOW, TZ))
    assert first == second


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

    layout = build_layout([event(0, 9, 10, "Standup")], WEEK, TZ, generated_at=NOW)
    payload = to_png_bytes(render_week(layout, NOW, TZ))

    assert payload[:8] == b"\x89PNG\r\n\x1a\n"
    width, height, bit_depth, colour_type = struct.unpack_from(">IIBB", payload, 16)
    assert (width, height) == (WIDTH, HEIGHT)
    assert bit_depth == 1
    assert colour_type == 0  # greyscale, so a set bit is white
    assert b"PLTE" not in payload


def test_png_and_bmp_carry_the_same_pixels():
    layout = build_layout([event(1, 14, 16, "Review")], WEEK, TZ, generated_at=NOW)
    image = render_week(layout, NOW, TZ)
    with (
        Image.open(io.BytesIO(to_png_bytes(image))) as png,
        Image.open(io.BytesIO(to_bmp_bytes(image))) as bmp,
    ):
        assert png.convert("1").tobytes() == bmp.convert("1").tobytes()


def test_png_is_far_smaller_than_the_bmp():
    layout = build_layout([event(d, 9, 10, f"Tag {d}") for d in range(7)], WEEK, TZ, generated_at=NOW)
    image = render_week(layout, NOW, TZ)
    # The BMP is a fixed 48062 bytes whatever is on the screen.
    assert len(to_png_bytes(image)) * 10 < len(to_bmp_bytes(image))


def test_to_device_bytes_picks_the_configured_format():
    image = render_message("Titel", [])
    assert to_device_bytes(image, "png")[:8] == b"\x89PNG\r\n\x1a\n"
    assert to_device_bytes(image, "bmp")[:2] == b"BM"
