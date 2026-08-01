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
    # If set, /api/setup requires it. Strongly recommended on a public host.
    pairing_secret: str = ""
    log_level: str = "INFO"

    @field_validator("caldav_url")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def calendar_names(self) -> list[str]:
        return [c.strip() for c in self.caldav_calendars.split(",") if c.strip()]

    @property
    def caldav_configured(self) -> bool:
        return bool(self.caldav_url and self.caldav_username and self.caldav_password)


@lru_cache
def get_settings() -> Settings:
    return Settings()
