"""Render the weather screen from sample data, for layout work without a device
or a server.

    python scripts/preview_sample.py [out.png] [--stale]
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.render import render_weather, to_png_bytes  # noqa: E402
from app.sample import sample_forecast  # noqa: E402
from app.weather import parse_forecast  # noqa: E402

# A Thursday, so the large weekday is the longest one there is.
START = date(2026, 11, 26)


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    out = Path(args[0]) if args else Path("preview.png")
    days = parse_forecast(sample_forecast(START))
    first, second = sorted(days)[:2]
    note = "Daten von 14:00 · offline" if "--stale" in sys.argv else None
    image = render_weather(days[first], days[second], stale_note=note)
    out.write_bytes(to_png_bytes(image))
    print(f"{out}  {first} + {second}")


if __name__ == "__main__":
    main()
