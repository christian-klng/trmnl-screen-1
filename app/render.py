"""Drawing the weather onto an 800x480 1-bit image.

Everything is rendered directly with Pillow rather than through a headless
browser. At this size a browser costs a large container and a slow render, and
its anti-aliased text has to be thresholded down to 1 bit afterwards, which is
exactly what makes type look muddy on e-ink. Drawing unantialiased from the
start keeps the glyphs crisp.

The layout follows the mockup: the day that matters large on the left, the day
after it small on the right. Coordinates are baselines, as in the mockup's SVG.
"""

from __future__ import annotations

import io
import math
from datetime import date
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .weather import PARTS, DayForecast

WIDTH = 800
HEIGHT = 480

BLACK = 0
WHITE = 255

FONT_DIR = Path(__file__).resolve().parent.parent / "fonts"
BOLD = FONT_DIR / "BarlowCondensed-Bold.ttf"
SEMIBOLD = FONT_DIR / "BarlowCondensed-SemiBold.ttf"
MEDIUM = FONT_DIR / "BarlowCondensed-Medium.ttf"

WEEKDAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
MONTHS = [
    "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
]

# Left column: the day shown large.
MAIN_RIGHT = 512
# Four parts of the day share the strip under the rule.
PARTS_LEFT = 20
PARTS_WIDTH = 492
# Right column: the day after.
DIVIDER_X = 534
SIDE_X = 556
SIDE_WIDTH = 222


@lru_cache(maxsize=64)
def font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(path), size)


def fitted(path: Path, size: int, text: str, max_width: float) -> ImageFont.FreeTypeFont:
    """The largest font up to ``size`` in which ``text`` fits ``max_width``.

    The layout is drawn for the common case. A long weekday in the narrow
    column, or a two-digit frost, shrinks instead of running into its neighbour.
    """
    face = font(path, size)
    width = face.getlength(text)
    if width <= max_width:
        return face
    size = max(8, math.floor(size * max_width / width))
    while size > 8 and font(path, size).getlength(text) > max_width:
        size -= 1
    return font(path, size)


def _new_canvas() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("L", (WIDTH, HEIGHT), WHITE)
    draw = ImageDraw.Draw(image)
    # The documented switch that turns off font anti-aliasing.
    draw.fontmode = "1"
    return image, draw


# --- wording -------------------------------------------------------------------


def deg(value: int) -> str:
    """A temperature with a real minus sign, which is wider and easier to read
    from across the room than a hyphen."""
    return f"{'−' if value < 0 else ''}{abs(value)}°"


def millimetres(value: float) -> str:
    if value < 0.05:
        return "0 mm"
    if value < 0.95:
        return f"{value:.1f} mm".replace(".", ",")
    return f"{math.floor(value + 0.5)} mm"


def rain_line(day: DayForecast) -> str:
    amount = millimetres(day.precip_sum)
    if day.precip_probability is None:
        return f"Regen {amount}"
    return f"Regen {day.precip_probability} % · {amount}"


def wind_line(day: DayForecast) -> str:
    if day.gusts is not None and day.gusts > day.wind:
        return f"Wind {day.wind}–{day.gusts} km/h"
    return f"Wind {day.wind} km/h"


def date_line(day: date) -> str:
    return f"{day.day}. {MONTHS[day.month - 1]}"


def weekday(day: date) -> str:
    return WEEKDAYS[day.weekday()]


# --- 1-bit weather icons ---------------------------------------------------------
#
# Each icon is drawn on a 100-unit square scaled to ``size`` pixels. Outlines
# are made by filling a shape black and then the same shape, shrunk by the
# stroke width, white: for a cloud built from overlapping circles that gives a
# clean outline of the union without any path maths.

STROKE = 6
CLOUD_LOBES = ((30, 58, 16), (52, 44, 21), (72, 59, 15))


class _Pen:
    def __init__(self, draw: ImageDraw.ImageDraw, x: int, y: int, size: int) -> None:
        self.draw = draw
        self.x = x
        self.y = y
        self.k = size / 100
        self.stroke = max(2, round(STROKE * self.k))

    def p(self, ux: float, uy: float) -> tuple[int, int]:
        return round(self.x + ux * self.k), round(self.y + uy * self.k)

    def disc(self, cx: float, cy: float, r: float, fill: int) -> None:
        x0, y0 = self.p(cx - r, cy - r)
        x1, y1 = self.p(cx + r, cy + r)
        self.draw.ellipse((x0, y0, x1, y1), fill=fill)

    def rect(self, ux0: float, uy0: float, ux1: float, uy1: float, fill: int) -> None:
        self.draw.rectangle((*self.p(ux0, uy0), *self.p(ux1, uy1)), fill=fill)

    def line(self, ux0: float, uy0: float, ux1: float, uy1: float) -> None:
        self.draw.line((*self.p(ux0, uy0), *self.p(ux1, uy1)), fill=BLACK, width=self.stroke)

    def polygon(self, points: list[tuple[float, float]]) -> None:
        self.draw.polygon([self.p(ux, uy) for ux, uy in points], fill=BLACK)


def _cloud(pen: _Pen, dy: float = 0, solid: bool = False) -> None:
    inset = STROKE
    for cx, cy, r in CLOUD_LOBES:
        pen.disc(cx, cy + dy, r, BLACK)
    pen.rect(30, 58 + dy, 72, 74 + dy, BLACK)
    if solid:
        return
    for cx, cy, r in CLOUD_LOBES:
        pen.disc(cx, cy + dy, r - inset, WHITE)
    pen.rect(30, 52 + dy, 72, 74 + dy - inset, WHITE)


def _sun(pen: _Pen, cx: float, cy: float, r: float, ray_from: float, ray_to: float) -> None:
    for i in range(8):
        angle = i * math.pi / 4
        pen.line(
            cx + math.cos(angle) * ray_from, cy + math.sin(angle) * ray_from,
            cx + math.cos(angle) * ray_to, cy + math.sin(angle) * ray_to,
        )
    pen.disc(cx, cy, r, BLACK)
    pen.disc(cx, cy, r - STROKE, WHITE)


def _moon(pen: _Pen, cx: float, cy: float, r: float) -> None:
    pen.disc(cx, cy, r, BLACK)
    pen.disc(cx + r * 0.5, cy - r * 0.36, r * 0.86, WHITE)


def _drops(pen: _Pen, xs: tuple[int, ...]) -> None:
    for i, x in enumerate(xs):
        offset = (i % 2) * 4
        pen.line(x, 70 + offset, x - 6, 86 + offset)


def draw_icon(draw: ImageDraw.ImageDraw, kind: str, x: int, y: int, size: int) -> None:
    pen = _Pen(draw, x, y, size)
    if kind == "clear":
        _sun(pen, 50, 50, 17, 24, 36)
    elif kind == "moon":
        _moon(pen, 46, 52, 28)
    elif kind == "partly":
        _sun(pen, 34, 34, 13, 19, 28)
        _cloud(pen, dy=6)
    elif kind == "partly_night":
        _moon(pen, 32, 34, 18)
        _cloud(pen, dy=6)
    elif kind == "fog":
        for row, (x0, x1) in enumerate(((18, 82), (26, 74), (18, 82))):
            pen.line(x0, 38 + row * 16, x1, 38 + row * 16)
    elif kind == "rain":
        _cloud(pen, dy=-12)
        _drops(pen, (36, 52, 68))
    elif kind == "heavyrain":
        _cloud(pen, dy=-12, solid=True)
        _drops(pen, (34, 46, 58, 70))
    elif kind == "snow":
        _cloud(pen, dy=-12)
        for fx, fy in ((36, 76), (52, 88), (68, 76)):
            pen.disc(fx, fy, 5, BLACK)
    elif kind == "thunder":
        _cloud(pen, dy=-12)
        pen.polygon([(54, 60), (40, 82), (50, 82), (44, 99), (64, 74), (53, 74), (60, 60)])
    else:  # "cloud" and anything unknown
        _cloud(pen, dy=-2)


# --- the screen ------------------------------------------------------------------


def _text(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, face, fill: int = BLACK,
          anchor: str = "ls") -> None:
    draw.text(xy, text, font=face, fill=fill, anchor=anchor)


def render_weather(main: DayForecast, side: DayForecast, stale_note: str | None = None) -> Image.Image:
    """The screen: ``main`` large on the left, ``side`` compact on the right.

    ``stale_note`` is shown only when the forecast could not be refreshed for a
    while. There is deliberately no clock time otherwise: without one the image
    changes only when the forecast does, so the panel is redrawn far less often.
    """
    image, draw = _new_canvas()

    # --- the day that matters ------------------------------------------------
    name = weekday(main.day)
    _text(draw, (22, 104), name, fitted(BOLD, 108, name, MAIN_RIGHT - 22 - 4))
    _text(draw, (24, 146), date_line(main.day), font(MEDIUM, 32))

    draw_icon(draw, main.icon, 14, 170, 110)
    big = deg(main.temp_max)
    _text(draw, (300, 278), big, fitted(BOLD, 128, big, 300 - 132), anchor="rs")

    facts_x = 320
    facts_width = MAIN_RIGHT - facts_x
    feel = f"Gefühlt {deg(main.feel_max)}"
    _text(draw, (facts_x, 208), feel, fitted(SEMIBOLD, 30, feel, facts_width))
    rain = rain_line(main)
    _text(draw, (facts_x, 244), rain, fitted(MEDIUM, 30, rain, facts_width))
    wind = wind_line(main)
    _text(draw, (facts_x, 280), wind, fitted(MEDIUM, 30, wind, facts_width))

    if main.text:
        _text(draw, (24, 326), main.text, fitted(MEDIUM, 30, main.text, MAIN_RIGHT - 24))

    # --- four parts of the day ------------------------------------------------
    draw.rectangle((PARTS_LEFT, 346, PARTS_LEFT + PARTS_WIDTH - 1, 347), fill=BLACK)
    cell = PARTS_WIDTH / len(PARTS)
    for i, (part, forecast) in enumerate(zip(PARTS, main.parts, strict=True)):
        left = PARTS_LEFT + i * cell
        right = round(left + cell)
        if i:
            draw.rectangle((round(left), 360, round(left), 469), fill=BLACK)
        lx = round(left + (10 if i else 2))
        _text(draw, (lx, 376), part.label, font(SEMIBOLD, 18))
        if forecast is None:
            _text(draw, (lx + 52, 428), "–", font(BOLD, 42))
            continue
        draw_icon(draw, forecast.icon, lx - 2, 386, 50)
        temp = deg(forecast.temp)
        _text(draw, (lx + 52, 428), temp, fitted(BOLD, 42, temp, right - 4 - (lx + 52)))
        _text(draw, (lx + 54, 460), deg(forecast.feel), font(MEDIUM, 24))

    draw.rectangle((DIVIDER_X, 24, DIVIDER_X + 1, 455), fill=BLACK)

    # --- the day after --------------------------------------------------------
    rx = SIDE_X
    name = weekday(side.day)
    _text(draw, (rx, 74), name, fitted(BOLD, 62, name, SIDE_WIDTH))
    _text(draw, (rx + 1, 106), date_line(side.day), font(MEDIUM, 24))
    draw_icon(draw, side.icon, rx - 6, 120, 100)
    high = deg(side.temp_max)
    _text(draw, (rx + 112, 186), high, fitted(BOLD, 66, high, SIDE_WIDTH - 112))
    _text(draw, (rx + 114, 218), f"min {deg(side.temp_min)}", font(SEMIBOLD, 26))

    # One size, one line pitch: 24 px type on a 32 px step.
    for y, line in ((270, side.text), (302, rain_line(side)), (334, wind_line(side))):
        if line:
            _text(draw, (rx, y), line, fitted(MEDIUM, 24, line, SIDE_WIDTH))

    if stale_note:
        draw.rectangle((rx - 6, 430, rx + SIDE_WIDTH + 5, 461), fill=BLACK)
        _text(draw, (rx + 4, 453), stale_note, fitted(SEMIBOLD, 20, stale_note, SIDE_WIDTH - 4), fill=WHITE)

    return image


def render_message(title: str, lines: list[str]) -> Image.Image:
    """A plain screen for start-up and error states."""
    image, draw = _new_canvas()
    title_face = fitted(BOLD, 56, title, WIDTH - 80)
    _text(draw, (WIDTH // 2, 200), title, title_face, anchor="ms")
    y = 260
    for line in lines:
        _text(draw, (WIDTH // 2, y), line, fitted(MEDIUM, 28, line, WIDTH - 80), anchor="ms")
        y += 38
    return image


# --- what the device downloads ---------------------------------------------------


def to_bmp_bytes(image: Image.Image) -> bytes:
    """1-bit BMP3. Kept as a fallback; see to_device_bytes for why not to use it."""
    mono = image.convert("1", dither=Image.Dither.NONE)
    buffer = io.BytesIO()
    mono.save(buffer, format="BMP")
    return buffer.getvalue()


def to_png_bytes(image: Image.Image) -> bytes:
    """1-bit greyscale PNG.

    Pillow writes mode "1" as bit depth 1, colour type 0, no palette. The
    firmware copies those bits straight into the panel plane, and its
    convention (a set bit is white) is the same one the BMP raster uses, so
    the two formats put identical pixels on the glass.
    """
    mono = image.convert("1", dither=Image.Dither.NONE)
    buffer = io.BytesIO()
    mono.save(buffer, format="PNG")
    return buffer.getvalue()


def to_device_bytes(image: Image.Image, image_format: str) -> bytes:
    """The bytes the device downloads.

    PNG, and not for the size — a typical screen is a few KB against the BMP's
    fixed 48 062 bytes, welcome as that is on a battery-powered radio.

    The firmware's BMP branch ends with ``iUpdateCount = 1`` on every single
    draw, and two lines later decides whether to flash the panel with
    ``(iUpdateCount & 7) == 0``. That test can therefore never be true, so a
    device fed BMPs performs partial, flicker-free updates forever and slowly
    washes out. The PNG branch leaves the counter alone and the every-eighth
    full refresh happens as intended. We no longer depend on that — see
    FULL_REFRESH_EVERY — but there is no reason to keep fighting it.
    """
    return to_png_bytes(image) if image_format == "png" else to_bmp_bytes(image)
