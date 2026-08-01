"""Configuration. Secrets come from environment variables only."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    # --- CalDAV (Nextcloud) -------------------------------------------------
    caldav_url: str = ""
    caldav_username: str = ""
    caldav_password: str = ""
    # Empty means "every calendar the account can see".
    caldav_calendars: str = ""

    # --- Direct ICS/webcal URLs ---------------------------------------------
    # Comma separated, either "Name=https://…" or a bare URL. Fetched directly
    # on our own schedule, which bypasses a slow Nextcloud subscription job for
    # calendars that live somewhere else (a Google secret iCal address, say).
    ics_urls: str = ""

    # --- Display / device ---------------------------------------------------
    # Seconds the device sleeps between polls during the day.
    display_refresh_seconds: int = 900
    # Quiet hours: the device is told to sleep through them.
    quiet_start_hour: int = 23
    quiet_end_hour: int = 5

    # --- Fetching -----------------------------------------------------------
    # Deliberately below 10 minutes: Railway's serverless mode considers a
    # service idle after 10 minutes without outbound traffic, and the first
    # request to a slept service can return 502.
    fetch_interval_minutes: int = 9

    # --- Week window --------------------------------------------------------
    # 6 == Sunday. From this weekday and hour onward the next week is shown.
    week_switch_weekday: int = 6
    week_switch_hour: int = 12

    # --- Grid ---------------------------------------------------------------
    grid_min_hours: int = 8
    grid_max_hours: int = 15

    # --- Runtime ------------------------------------------------------------
    timezone: str = "Europe/Berlin"
    data_dir: Path = Path("/data")
    # Absolute base URL the device should use. Left empty it is derived from
    # RAILWAY_PUBLIC_DOMAIN, then from the incoming request.
    public_base_url: str = ""
    railway_public_domain: str = Field(default="", alias="RAILWAY_PUBLIC_DOMAIN")
    # Guards /image and /preview. Generated and persisted on first start.
    image_token: str = ""
    log_level: str = "INFO"

    # --- Pairing protection -------------------------------------------------
    # The device can only be given a base URL, so it cannot send a custom
    # header. These three are what actually works in that constraint.
    #
    # Mount the whole API under a secret path, e.g. "/s/9f3c…". The device URL
    # then becomes https://host/s/9f3c… and the URL itself is the secret.
    url_prefix: str = ""
    # Comma separated MAC addresses allowed to pair. Empty means any.
    allowed_macs: str = ""
    # Once one device has paired, refuse to register further ones.
    lock_after_first_pairing: bool = True
    # Optional extra: an X-Pairing-Secret header or ?secret= query parameter.
    # Only usable by clients that can send one; the TRMNL firmware cannot.
    pairing_secret: str = ""

    @field_validator("caldav_url")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @field_validator("url_prefix")
    @classmethod
    def _normalise_prefix(cls, v: str) -> str:
        v = v.strip().strip("/")
        return f"/{v}" if v else ""

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def calendar_names(self) -> list[str]:
        return [c.strip() for c in self.caldav_calendars.split(",") if c.strip()]

    @property
    def caldav_configured(self) -> bool:
        return bool(self.caldav_url and self.caldav_username and self.caldav_password)

    @property
    def ics_sources(self) -> list[tuple[str, str]]:
        """(display name, url) pairs parsed from ICS_URLS."""
        sources: list[tuple[str, str]] = []
        for index, entry in enumerate(self.ics_urls.split(",")):
            entry = entry.strip()
            if not entry:
                continue
            name, separator, url = entry.partition("=")
            if separator and not name.strip().lower().startswith(("http", "webcal")):
                sources.append((name.strip(), url.strip()))
            else:
                sources.append((f"ICS {index + 1}", entry))
        return sources

    @property
    def any_source_configured(self) -> bool:
        return self.caldav_configured or bool(self.ics_sources)

    @property
    def allowed_mac_set(self) -> set[str]:
        return {m.strip().upper() for m in self.allowed_macs.split(",") if m.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
