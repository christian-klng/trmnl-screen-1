"""Configuration. Secrets come from environment variables only."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .firmware import release_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    # --- Weather -------------------------------------------------------------
    # Where the forecast is for. The default is Treptower Park in Berlin, where
    # this display stands on a kitchen table.
    latitude: float = 52.4886
    longitude: float = 13.4697
    # Open-Meteo's forecast endpoint. Free, no key; for Germany its default
    # model blend is DWD's ICON. Overridable for tests or a self-hosted copy.
    weather_url: str = "https://api.open-meteo.com/v1/forecast"

    # From this hour on the screen shows the next day large. 24 means it never
    # switches early and simply follows the date.
    day_switch_hour: int = 20

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
    # Minutes of the hour at which the forecast is refreshed, comma separated.
    # Open-Meteo recomputes its forecast about once an hour, so twice an hour
    # picks up every new run without hammering a free service.
    #
    # The server has to stay up between refreshes: a host that puts idle
    # services to sleep would answer the device's poll with a cold start.
    fetch_at_minutes: str = "0,30"

    # --- Runtime ------------------------------------------------------------
    timezone: str = "Europe/Berlin"
    data_dir: Path = Path("/data")
    # Absolute base URL the device should use. Set it in production. Left
    # empty it is derived from the incoming request and the proxy's
    # X-Forwarded-* headers.
    public_base_url: str = ""
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

    @field_validator("latitude")
    @classmethod
    def _validate_latitude(cls, v: float) -> float:
        if not -90 <= v <= 90:
            raise ValueError(f"LATITUDE must lie between -90 and 90, not {v}")
        return v

    @field_validator("longitude")
    @classmethod
    def _validate_longitude(cls, v: float) -> float:
        if not -180 <= v <= 180:
            raise ValueError(f"LONGITUDE must lie between -180 and 180, not {v}")
        return v

    @field_validator("day_switch_hour")
    @classmethod
    def _validate_day_switch_hour(cls, v: int) -> int:
        # 0 would show tomorrow around the clock, which is never what is meant.
        if not 1 <= v <= 24:
            raise ValueError(f"DAY_SWITCH_HOUR must lie between 1 and 24, not {v}")
        return v

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
    def allowed_mac_set(self) -> set[str]:
        return {m.strip().upper() for m in self.allowed_macs.split(",") if m.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
