"""Over-the-air firmware updates, handed out through our own poll response.

The firmware installs whatever it is pointed at. When /api/display says
``update_firmware`` with a ``firmware_url``, it downloads the image into the
spare app partition, verifies it, switches over and reboots. The WiFi
credentials, the api_key and the server URL live in a different part of the
flash and survive, so unlike a USB flash nothing has to be set up again.

What it does not do is compare versions. After a successful update it would
flash the same image again on the very next poll, and on the one after that —
its 24-hour back-off only follows a *failed* attempt. So whether a device
needs an update is decided here, from the version it reports on every poll,
and an update is never offered to a device that is already there or ahead.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

# Where TRMNL publishes its release images, the same bucket its own cloud and
# the web flasher use. The OTA image sits at the top level; the one under
# flash/ carries the bootloader and partition table as well and belongs at
# offset 0 over USB, never in an app partition.
RELEASE_BASE_URL = "https://trmnl-fw.s3.us-east-2.amazonaws.com/trmnl_og"

_VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)")


def parse_version(value: str | None) -> tuple[int, int, int] | None:
    """``"1.8.12"`` as ``(1, 8, 12)``, so that 1.8.9 sorts before 1.8.12.

    Only the leading three numbers count: a build suffix such as
    ``-sensors`` names a variant, not a newer release.
    """
    if not value:
        return None
    match = _VERSION.match(value.strip())
    if not match:
        return None
    major, minor, patch = (int(part) for part in match.groups())
    return major, minor, patch


def release_url(version: str) -> str:
    return f"{RELEASE_BASE_URL}/FW{version}.bin"


def update_due(
    *,
    reported: str | None,
    target: str,
    last_offer: datetime | None,
    now: datetime,
    retry_after: timedelta,
) -> bool:
    """Whether this poll should tell the device to update itself.

    A device that does not say which version it runs is never flashed blind.

    The retry window covers the case the version check cannot: an image that
    installs fine but still reports the old version — a URL pointing at the
    wrong release, say. Without it the device would download and flash 1.5 MB
    on every single poll until its battery gave out.
    """
    wanted = parse_version(target)
    running = parse_version(reported)
    if wanted is None or running is None:
        return False
    if running >= wanted:
        return False
    return last_offer is None or now - last_offer >= retry_after
