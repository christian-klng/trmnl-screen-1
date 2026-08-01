"""Fetching plain iCalendar documents over HTTP.

This exists to bypass a slow intermediary. A Google calendar subscribed inside
Nextcloud is only as fresh as Nextcloud's subscription job, which defaults to
once a day and needs shell access to change. Pointing at the secret iCal
address directly puts the calendar back on our own 9-minute schedule.
"""

from __future__ import annotations

import logging
import urllib.error
import urllib.request
from dataclasses import dataclass

log = logging.getLogger(__name__)

USER_AGENT = "trmnl-screen-1/1.0 (+https://github.com/christian-klng/trmnl-screen-1)"
MAX_BYTES = 20 * 1024 * 1024


@dataclass
class IcsFetchResult:
    documents: dict[str, list[str]]
    failed: list[str]


def normalise_url(url: str) -> str:
    """Calendar apps hand out webcal:// links; HTTP is what we can fetch."""
    if url.startswith("webcal://"):
        return "https://" + url[len("webcal://") :]
    if url.startswith("webcals://"):
        return "https://" + url[len("webcals://") :]
    return url


def fetch_one(url: str, timeout: int = 20) -> str:
    request = urllib.request.Request(  # noqa: S310 - scheme is validated below
        normalise_url(url),
        headers={"User-Agent": USER_AGENT, "Accept": "text/calendar, */*"},
    )
    if request.type not in ("http", "https"):
        raise ValueError(f"Refusing to fetch non-HTTP URL: {url!r}")

    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        payload = response.read(MAX_BYTES + 1)

    if len(payload) > MAX_BYTES:
        raise ValueError(f"Calendar at {url!r} is larger than {MAX_BYTES} bytes")

    text = payload.decode("utf-8", errors="replace")
    if "BEGIN:VCALENDAR" not in text:
        # A login page or an error page would otherwise be parsed as an empty
        # calendar and silently wipe the events for this source.
        raise ValueError("Response does not look like iCalendar data")
    return text


def fetch_ics_sources(sources: list[tuple[str, str]], timeout: int = 20) -> IcsFetchResult:
    documents: dict[str, list[str]] = {}
    failed: list[str] = []

    for name, url in sources:
        try:
            documents[name] = [fetch_one(url, timeout=timeout)]
        except (urllib.error.URLError, ValueError, TimeoutError, OSError):
            log.exception("Fetching ICS calendar %r failed", name)
            failed.append(name)

    return IcsFetchResult(documents=documents, failed=failed)
