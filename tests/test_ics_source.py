import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.config import Settings
from app.ics_source import fetch_ics_sources, fetch_one, normalise_url


def calendar_text() -> str:
    """An event inside the week currently on screen.

    Built from the live week window rather than a fixed date, so the test does
    not quietly stop asserting anything once that date rolls out of view.
    """
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    from app.week import current_week

    tz = ZoneInfo("Europe/Berlin")
    window = current_week(datetime.now(tz), tz)
    start = window.start + timedelta(days=2, hours=9)
    end = start + timedelta(hours=1)
    fmt = "%Y%m%dT%H%M%S"
    return (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//test//\r\n"
        "BEGIN:VEVENT\r\nUID:x@test\r\n"
        f"DTSTART;TZID=Europe/Berlin:{start.strftime(fmt)}\r\n"
        f"DTEND;TZID=Europe/Berlin:{end.strftime(fmt)}\r\n"
        "SUMMARY:Aus ICS\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    )


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path == "/good.ics":
            body = calendar_text().encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/calendar")
        elif self.path == "/login.html":
            body = b"<html><body>Please sign in</body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
        else:
            body = b"nope"
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # silence the test output
        pass


@pytest.fixture(scope="module")
def server():
    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def test_webcal_scheme_is_rewritten():
    assert normalise_url("webcal://example.com/a.ics") == "https://example.com/a.ics"
    assert normalise_url("webcals://example.com/a.ics") == "https://example.com/a.ics"
    assert normalise_url("https://example.com/a.ics") == "https://example.com/a.ics"


def test_fetch_one_returns_the_calendar(server):
    text = fetch_one(f"{server}/good.ics")
    assert "BEGIN:VCALENDAR" in text
    assert "Aus ICS" in text


def test_html_response_is_rejected_rather_than_parsed_as_empty(server):
    """A login page must fail loudly, not silently wipe this calendar."""
    with pytest.raises(ValueError, match="iCalendar"):
        fetch_one(f"{server}/login.html")


def test_http_error_propagates(server):
    with pytest.raises(Exception):  # noqa: B017 - any failure is fine, it must not return
        fetch_one(f"{server}/missing.ics")


def test_non_http_scheme_is_refused():
    with pytest.raises(ValueError, match="non-HTTP"):
        fetch_one("file:///etc/passwd")


def test_fetch_sources_separates_success_from_failure(server):
    result = fetch_ics_sources(
        [("Gut", f"{server}/good.ics"), ("Kaputt", f"{server}/missing.ics")]
    )
    assert "Gut" in result.documents
    assert result.failed == ["Kaputt"]


def test_one_broken_source_does_not_lose_the_others(server):
    result = fetch_ics_sources(
        [
            ("A", f"{server}/good.ics"),
            ("B", f"{server}/login.html"),
            ("C", f"{server}/good.ics"),
        ]
    )
    assert sorted(result.documents) == ["A", "C"]
    assert result.failed == ["B"]


# --- end to end through the refresh pipeline ------------------------------


def test_refresh_renders_events_from_an_ics_url(server, tmp_path):
    """An ICS URL alone must be enough to produce a screen."""
    import asyncio

    from app.config import Settings
    from app.refresh import ScreenState, refresh_screen
    from app.store import Store

    settings = Settings(data_dir=tmp_path, ics_urls=f"Google={server}/good.ics")
    store = Store(tmp_path)
    state = asyncio.run(refresh_screen(settings, store, ScreenState()))

    assert state.digest
    assert state.stale_calendars == []
    assert store.image_path(state.digest).exists()
    assert store.load_calendar("Google") is not None


def test_refresh_falls_back_to_cache_when_the_ics_url_breaks(server, tmp_path):
    import asyncio

    from app.config import Settings
    from app.refresh import ScreenState, refresh_screen
    from app.store import Store

    store = Store(tmp_path)
    good = Settings(data_dir=tmp_path, ics_urls=f"Google={server}/good.ics")
    first = asyncio.run(refresh_screen(good, store, ScreenState()))
    assert first.stale_calendars == []

    broken = Settings(data_dir=tmp_path, ics_urls=f"Google={server}/missing.ics")
    second = asyncio.run(refresh_screen(broken, store, ScreenState()))

    # The calendar is marked stale but its events are still on the screen.
    assert second.stale_calendars == ["Google"]
    assert second.event_count == first.event_count
    assert second.event_count > 0


def test_caldav_failure_does_not_mark_a_working_ics_source_stale(server, tmp_path):
    """Regression: an unreachable CalDAV server fell back to every cached
    calendar name, which includes the ICS ones, and then overwrote freshly
    fetched ICS data with the stale copy."""
    import asyncio

    from app.config import Settings
    from app.refresh import ScreenState, refresh_screen
    from app.store import Store

    store = Store(tmp_path)

    # First a clean run, so "Google" is in the calendar cache. Without this the
    # fallback list is empty and the bug cannot show itself.
    healthy = Settings(data_dir=tmp_path, ics_urls=f"Google={server}/good.ics")
    first = asyncio.run(refresh_screen(healthy, store, ScreenState()))
    assert first.event_count > 0
    assert "Google" in store.list_cached_calendars()

    # Now add an unreachable CalDAV server alongside the working ICS source.
    settings = Settings(
        data_dir=tmp_path,
        ics_urls=f"Google={server}/good.ics",
        caldav_url="http://127.0.0.1:9/dav",  # discard port, always refused
        caldav_username="u",
        caldav_password="p",
    )
    state = asyncio.run(refresh_screen(settings, store, ScreenState()))

    assert "Google" not in state.stale_calendars
    assert state.event_count == first.event_count
    assert state.last_error is not None  # the CalDAV failure is still reported


# --- configuration parsing ------------------------------------------------


def settings(**overrides) -> Settings:
    return Settings(data_dir="/tmp/x", **overrides)


def test_named_ics_sources_are_parsed():
    parsed = settings(ics_urls="Google=https://a.test/a.ics,Verein=https://b.test/b.ics").ics_sources
    assert parsed == [
        ("Google", "https://a.test/a.ics"),
        ("Verein", "https://b.test/b.ics"),
    ]


def test_bare_urls_get_a_generated_name():
    parsed = settings(ics_urls="https://a.test/a.ics").ics_sources
    assert parsed == [("ICS 1", "https://a.test/a.ics")]


def test_url_with_query_string_is_not_split_on_its_equals_sign():
    """Google's secret address carries ?cid=… — the name split must not eat it."""
    url = "https://calendar.google.com/calendar/ical/abc/private-xyz/basic.ics?foo=bar"
    assert settings(ics_urls=url).ics_sources == [("ICS 1", url)]


def test_empty_and_whitespace_entries_are_ignored():
    assert settings(ics_urls=" , ,").ics_sources == []
    assert settings(ics_urls="").ics_sources == []


def test_any_source_configured():
    assert settings().any_source_configured is False
    assert settings(ics_urls="https://a.test/a.ics").any_source_configured is True
    assert (
        settings(
            caldav_url="https://c.test/dav", caldav_username="u", caldav_password="p"
        ).any_source_configured
        is True
    )
