"""Drawing the week onto an 800x480 1-bit image.

Everything is rendered directly with Pillow rather than through a headless
browser. At this size a browser costs a large container and a slow render, and
its anti-aliased text has to be thresholded down to 1 bit afterwards, which is
exactly what makes small type look muddy on e-ink. Drawing unantialiased from
the start keeps the glyphs crisp.
"""

from __future__ import annotations

from collections import deque
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

from .layout import WeekLayout

WIDTH = 800
HEIGHT = 480

HEADER_H = 16
DAYHEAD_H = 18
TIME_COL_W = 32
ALL_DAY_ROW_H = 14
ALL_DAY_PAD = 3

BLACK = 0
WHITE = 255

FONT_DIR = Path(__file__).resolve().parent.parent / "fonts"
REGULAR = FONT_DIR / "DejaVuSansCondensed.ttf"
BOLD = FONT_DIR / "DejaVuSansCondensed-Bold.ttf"

WEEKDAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
MONTHS = [
    "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
]
MONTHS_SHORT = [
    "Jan", "Feb", "Mär", "Apr", "Mai", "Jun",
    "Jul", "Aug", "Sep", "Okt", "Nov", "Dez",
]


class Fonts:
    def __init__(self) -> None:
        self.title = ImageFont.truetype(str(BOLD), 12)
        self.meta = ImageFont.truetype(str(REGULAR), 11)
        self.dayhead = ImageFont.truetype(str(BOLD), 12)
        self.hour = ImageFont.truetype(str(REGULAR), 10)
        self.event = ImageFont.truetype(str(REGULAR), 11)
        self.small = ImageFont.truetype(str(REGULAR), 9)


_fonts: Fonts | None = None


def fonts() -> Fonts:
    global _fonts
    if _fonts is None:
        _fonts = Fonts()
    return _fonts


def _new_canvas() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("L", (WIDTH, HEIGHT), WHITE)
    draw = ImageDraw.Draw(image)
    # The documented switch that turns off font anti-aliasing.
    draw.fontmode = "1"
    return image, draw


def column_x(index: int) -> int:
    """Left edge of day column ``index`` (0..7, where 7 is the right border)."""
    return TIME_COL_W + round(index * (WIDTH - TIME_COL_W) / 7)


def _text_width(font: ImageFont.FreeTypeFont, text: str) -> float:
    return font.getlength(text)


def fit_text(text: str, font: ImageFont.FreeTypeFont, max_width: float) -> str:
    """Truncate with an ellipsis so it never spills out of its box."""
    if _text_width(font, text) <= max_width:
        return text
    ellipsis = "…"
    if _text_width(font, ellipsis) > max_width:
        return ""
    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if _text_width(font, text[:mid] + ellipsis) <= max_width:
            low = mid
        else:
            high = mid - 1
    return text[:low] + ellipsis if low else ellipsis


def _longest_prefix(word: str, font: ImageFont.FreeTypeFont, max_width: float) -> str:
    low, high = 0, len(word)
    while low < high:
        mid = (low + high + 1) // 2
        if _text_width(font, word[:mid]) <= max_width:
            low = mid
        else:
            high = mid - 1
    return word[:low]


def wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: float, max_lines: int) -> list[str]:
    """Greedy wrap.

    A word too long for the column is broken rather than ellipsised, and only
    the final line gets an ellipsis, and only when text is actually left over.
    Ellipsising mid-title would claim the title ends there while the next line
    carries on, which reads as a different event entirely.
    """
    if max_lines <= 0:
        return []
    words = deque(text.split())
    if not words:
        return []

    lines: list[str] = []
    while words and len(lines) < max_lines:
        line = ""
        while words:
            candidate = f"{line} {words[0]}".strip()
            if _text_width(font, candidate) <= max_width:
                line = candidate
                words.popleft()
            else:
                break

        if not line:
            # The next word does not fit on an empty line: break it.
            word = words.popleft()
            prefix = _longest_prefix(word, font, max_width)
            if not prefix:
                break
            line = prefix
            remainder = word[len(prefix):]
            if remainder:
                words.appendleft(remainder)

        lines.append(line)

    if words and lines:
        lines[-1] = fit_text(f"{lines[-1]} {' '.join(words)}", font, max_width)
    return lines


def _dotted_line(draw: ImageDraw.ImageDraw, x0: int, y: int, x1: int, step: int = 3) -> None:
    for x in range(x0, x1, step):
        draw.point((x, y), fill=BLACK)


def _header_range(monday: date, sunday: date) -> str:
    if monday.month == sunday.month:
        return f"{monday.day}.–{sunday.day}. {MONTHS[monday.month - 1]} {sunday.year}"
    if monday.year == sunday.year:
        return (
            f"{monday.day}. {MONTHS_SHORT[monday.month - 1]} – "
            f"{sunday.day}. {MONTHS_SHORT[sunday.month - 1]} {sunday.year}"
        )
    return (
        f"{monday.day}. {MONTHS_SHORT[monday.month - 1]} {monday.year} – "
        f"{sunday.day}. {MONTHS_SHORT[sunday.month - 1]} {sunday.year}"
    )


def render_week(layout: WeekLayout, now: datetime, tz: ZoneInfo) -> Image.Image:
    image, draw = _new_canvas()
    f = fonts()
    window = layout.window
    today = now.astimezone(tz).date()

    # --- header -----------------------------------------------------------
    week_no = window.monday.isocalendar().week
    title = f"KW {week_no} · {_header_range(window.monday, window.sunday)}"
    draw.text((2, 1), title, font=f.title, fill=BLACK)

    stamp = layout.generated_at.astimezone(tz).strftime("%H:%M") if layout.generated_at else ""
    if layout.stale_calendars:
        stamp = f"{stamp}  ! {', '.join(layout.stale_calendars)}"
        stamp = fit_text(stamp, f.meta, WIDTH / 2 - 6)
    if stamp:
        draw.text((WIDTH - 2 - _text_width(f.meta, stamp), 2), stamp, font=f.meta, fill=BLACK)

    draw.line((0, HEADER_H, WIDTH, HEADER_H), fill=BLACK)

    # --- day headers ------------------------------------------------------
    dayhead_top = HEADER_H + 1
    for index, day in enumerate(window.days):
        x0, x1 = column_x(index), column_x(index + 1)
        label = f"{WEEKDAYS[index]} {day.day:02d}."
        if day == today:
            draw.rectangle((x0, dayhead_top, x1 - 1, dayhead_top + DAYHEAD_H - 2), fill=BLACK)
            fill = WHITE
        else:
            fill = BLACK
        text_x = x0 + (x1 - x0 - _text_width(f.dayhead, label)) / 2
        draw.text((text_x, dayhead_top + 2), label, font=f.dayhead, fill=fill)

    band_top = HEADER_H + DAYHEAD_H
    draw.line((0, band_top, WIDTH, band_top), fill=BLACK)

    # --- all-day band -----------------------------------------------------
    all_day_h = layout.all_day_rows * ALL_DAY_ROW_H + ALL_DAY_PAD if layout.all_day_rows else 0
    for bar in layout.all_day_bars:
        x0 = column_x(bar.first_day) + 1
        x1 = column_x(bar.last_day + 1) - 1
        y0 = band_top + 1 + bar.row * ALL_DAY_ROW_H
        y1 = y0 + ALL_DAY_ROW_H - 2
        draw.rectangle((x0, y0, x1, y1), outline=BLACK, fill=WHITE)
        label = fit_text(bar.event.summary, f.event, x1 - x0 - 6)
        draw.text((x0 + 3, y0 + 1), label, font=f.event, fill=BLACK)

    for day_index, count in layout.all_day_overflow.items():
        x1 = column_x(day_index + 1)
        marker = f"+{count}"
        draw.text(
            (x1 - 2 - _text_width(f.small, marker), band_top + 1),
            marker,
            font=f.small,
            fill=BLACK,
        )

    grid_top = band_top + all_day_h
    if all_day_h:
        draw.line((0, grid_top, WIDTH, grid_top), fill=BLACK)

    # --- time grid --------------------------------------------------------
    grid_h = HEIGHT - grid_top
    span = layout.span_hours or 1.0
    px_per_hour = grid_h / span

    def y_of(hour: float) -> float:
        return grid_top + (hour - layout.start_hour) * px_per_hour

    hour = int(layout.start_hour)
    while hour <= layout.end_hour:
        if hour >= layout.start_hour:
            y = int(y_of(hour))
            if grid_top < y < HEIGHT:
                draw.line((TIME_COL_W, y, WIDTH, y), fill=BLACK)
            if y + 10 < HEIGHT:
                draw.text((2, y + 1), f"{hour:02d}", font=f.hour, fill=BLACK)
        half = hour + 0.5
        if layout.start_hour <= half <= layout.end_hour:
            y_half = int(y_of(half))
            if grid_top < y_half < HEIGHT:
                _dotted_line(draw, TIME_COL_W, y_half, WIDTH)
        hour += 1

    for index in range(8):
        x = column_x(index)
        draw.line((x, grid_top, x, HEIGHT), fill=BLACK)

    # --- current time -----------------------------------------------------
    today_index = window.index_of(today)
    if today_index is not None:
        local = now.astimezone(tz)
        now_hour = local.hour + local.minute / 60
        if layout.start_hour <= now_hour <= layout.end_hour:
            y = int(y_of(now_hour))
            x0, x1 = column_x(today_index), column_x(today_index + 1)
            draw.line((x0, y, x1, y), fill=BLACK, width=2)
            draw.polygon([(x0, y - 3), (x0 + 5, y), (x0, y + 3)], fill=BLACK)

    # --- event blocks -----------------------------------------------------
    for block in layout.blocks:
        col_left = column_x(block.day_index)
        col_right = column_x(block.day_index + 1)
        col_width = col_right - col_left
        slot_width = col_width / block.columns

        x0 = int(col_left + block.column * slot_width) + 1
        x1 = int(col_left + (block.column + 1) * slot_width) - 1
        y0 = int(y_of(block.start_hour)) + 1
        y1 = int(y_of(block.end_hour)) - 1
        if y1 <= y0:
            y1 = y0 + 1
        if x1 <= x0:
            continue

        draw.rectangle((x0, y0, x1, y1), outline=BLACK, fill=WHITE)

        inner_w = x1 - x0 - 5
        inner_h = y1 - y0 - 2
        line_h = 11
        max_lines = max(1, int(inner_h // line_h))
        if inner_w < 8:
            continue

        lines = wrap_text(block.event.summary, f.event, inner_w, max_lines)
        text_y = y0 + 1
        for line in lines:
            if text_y + line_h > y1 + 1:
                break
            draw.text((x0 + 3, text_y), line, font=f.event, fill=BLACK)
            text_y += line_h

    # --- events outside the visible window ---------------------------------
    for day_index, count in layout.overflow_early.items():
        x1 = column_x(day_index + 1)
        marker = f"↑{count}"
        draw.text((x1 - 2 - _text_width(f.small, marker), grid_top + 1), marker, font=f.small, fill=BLACK)

    for day_index, count in layout.overflow_late.items():
        x1 = column_x(day_index + 1)
        marker = f"↓{count}"
        draw.text(
            (x1 - 2 - _text_width(f.small, marker), HEIGHT - 11),
            marker,
            font=f.small,
            fill=BLACK,
        )

    return image


def render_message(title: str, lines: list[str]) -> Image.Image:
    """A plain screen for setup and error states."""
    image, draw = _new_canvas()
    f = fonts()
    big = ImageFont.truetype(str(BOLD), 28)
    draw.text(((WIDTH - _text_width(big, title)) / 2, 150), title, font=big, fill=BLACK)
    y = 210
    for line in lines:
        draw.text(((WIDTH - _text_width(f.event, line)) / 2, y), line, font=f.event, fill=BLACK)
        y += 18
    return image


def to_bmp_bytes(image: Image.Image) -> bytes:
    """1-bit BMP3, the format the firmware handles most reliably."""
    import io

    mono = image.convert("1", dither=Image.Dither.NONE)
    buffer = io.BytesIO()
    mono.save(buffer, format="BMP")
    return buffer.getvalue()


def to_png_bytes(image: Image.Image) -> bytes:
    import io

    mono = image.convert("1", dither=Image.Dither.NONE)
    buffer = io.BytesIO()
    mono.save(buffer, format="PNG")
    return buffer.getvalue()
