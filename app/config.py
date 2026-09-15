"""Configuration. Secrets come from environment variables only."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .firmware import release_url


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

    # --- Panel health -------------------------------------------------------
    # What the device downloads, "png" or "bmp". The choice matters far beyond
    # file size; see render.to_device_bytes.
    image_format: str = "png"
    # Every Nth redraw is a full black/white refresh instead of a silent
    # partial one. Without them the panel washes out over weeks. 8 is what the
    # firmware does on its own when it is left to count.
    full_refresh_every: int = 8
    # Waveform profile the firmware should keep: "default", "a" or "b". We have
    # to send this on every response — the firmware overwrites its stored value
    # from each one, so omitting it pins the device to "default" forever.
    temperature_profile: str = "default"

    # --- Firmware -----------------------------------------------------------
    # Version the device should run, e.g. "1.8.16". Empty means no update is
    # ever offered. The device updates itself over WiFi on its next poll and
    # keeps its WiFi and server settings; see app/firmware.py.
    firmware_target: str = ""
    # Where to download it. Empty means TRMNL's own release image for
    # FIRMWARE_TARGET, which is what it should almost always be.
    firmware_url: str = ""
    # Minimum gap between two offers to the same device. Covers an image that
    # installs but keeps reporting the old version, which would otherwise be
    # downloaded and flashed on every poll.
    firmware_retry_hours: int = 6

    # --- Fetching -----------------------------------------------------------
    # Minutes of the hour at which calendars are refreshed, comma separated.
    # Fixed minutes rather than an interval because the rendered image carries
    # an HH:MM stamp: on an interval that stamp drifts, on fixed minutes it
    # always reads a tidy value and the screen updates predictably.
    #
    # Note the trade-off against Railway's serverless mode, which considers a
    # service idle after ten minutes without outbound traffic: with gaps longer
    # than that, the device's poll may hit a cold start. Keep app sleeping
    # switched off for this service, or narrow the gaps here.
    fetch_at_minutes: str = "0,30"

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

    @field_validator("fetch_at_minutes")
    @classmethod
    def _validate_minutes(cls, v: str) -> str:
        """Reject a typo here rather than at scheduler start.

        An invalid cron expression would otherwise take the whole app down on
        boot, which is a lot of damage for a stray character in a variable.
        """
        minutes = [m.strip() for m in v.split(",") if m.strip()]
        if not minutes:
            raise ValueError("FETCH_AT_MINUTES must name at least one minute")
        for minute in minutes:
            if not minute.isdigit() or not 0 <= int(minute) <= 59:
                raise ValueError(f"Not a minute of the hour: {minute!r}")
        return ",".join(str(int(m)) for m in minutes)

    @field_validator("image_format")
    @classmethod
    def _validate_image_format(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ("png", "bmp"):
            raise ValueError(f"IMAGE_FORMAT must be 'png' or 'bmp', not {v!r}")
        return v

    @field_validator("temperature_profile")
    @classmethod
    def _validate_temperature_profile(cls, v: str) -> str:
        """The firmware only knows "a" and "b"; anything else means default.

        Rejecting a typo here rather than silently treating it as the default
        matters, because trying the profiles out is the whole point of the
        setting — silently ignoring "A " would look like the profile made no
        difference.
        """
        v = v.strip().lower()
        if v not in ("default", "a", "b"):
            raise ValueError(f"TEMPERATURE_PROFILE must be 'default', 'a' or 'b', not {v!r}")
        return v

    @field_validator("full_refresh_every")
    @classmethod
    def _validate_full_refresh_every(cls, v: int) -> int:
        if v < 1:
            raise ValueError("FULL_REFRESH_EVERY must be at least 1")
        return v

    @field_validator("firmware_target")
    @classmethod
    def _normalise_firmware_target(cls, v: str) -> str:
        """Accept "1.8.16", "v1.8.16" or "FW1.8.16"; keep the bare number.

        The bare number is what the device reports. A target in any other shape
        would not parse, and the update would silently never be offered.
        """
        v = v.strip()
        for prefix in ("FW", "fw", "v", "V"):
            if v.startswith(prefix):
                v = v[len(prefix):]
                break
        if v and not re.fullmatch(r"\d+\.\d+\.\d+", v):
            raise ValueError(f"FIRMWARE_TARGET must look like 1.8.16, not {v!r}")
        return v

    @field_validator("firmware_url")
    @classmethod
    def _validate_firmware_url(cls, v: str) -> str:
        v = v.strip()
        if v and not v.startswith("https://"):
            raise ValueError("FIRMWARE_URL must be an https:// URL")
        return v

    @field_validator("firmware_retry_hours")
    @classmethod
    def _validate_firmware_retry_hours(cls, v: int) -> int:
        if v < 1:
            raise ValueError("FIRMWARE_RETRY_HOURS must be at least 1")
        return v

    @field_validator("url_prefix")
    @classmethod
    def _normalise_prefix(cls, v: str) -> str:
        v = v.strip().strip("/")
        return f"/{v}" if v else ""

    @property
    def firmware_download_url(self) -> str:
        """The image a device is pointed at, or "" when no update is configured."""
        if not self.firmware_target:
            return ""
        return self.firmware_url or release_url(self.firmware_target)

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
