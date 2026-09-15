from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.firmware import parse_version, release_url, update_due

NOW = datetime(2026, 9, 15, 10, 0, tzinfo=UTC)
WINDOW = timedelta(hours=6)


def due(**overrides) -> bool:
    base = {
        "reported": "1.8.12",
        "target": "1.8.16",
        "last_offer": None,
        "now": NOW,
        "retry_after": WINDOW,
    }
    base.update(overrides)
    return update_due(**base)


# --- versions ---------------------------------------------------------------


def test_versions_parse_into_comparable_numbers():
    assert parse_version("1.8.12") == (1, 8, 12)
    assert parse_version(" 1.8.16 ") == (1, 8, 16)


def test_a_build_suffix_does_not_make_a_version_newer():
    assert parse_version("1.7.8-sensors") == (1, 7, 8)


def test_garbage_is_not_a_version():
    assert parse_version(None) is None
    assert parse_version("") is None
    assert parse_version("unknown") is None
    assert parse_version("FW1.8.12") is None  # the device reports the bare number


def test_the_release_url_points_at_the_ota_image_not_the_flash_image():
    url = release_url("1.8.16")
    assert url == "https://trmnl-fw.s3.us-east-2.amazonaws.com/trmnl_og/FW1.8.16.bin"
    assert "/flash/" not in url


# --- when to offer an update --------------------------------------------------


def test_an_older_device_is_offered_the_update():
    assert due() is True


def test_a_device_on_the_target_is_left_alone():
    """The firmware would otherwise reflash the same image on every poll."""
    assert due(reported="1.8.16") is False


def test_a_device_ahead_of_the_target_is_never_downgraded():
    assert due(reported="1.8.17") is False
    assert due(reported="1.9.0") is False


def test_versions_compare_as_numbers_not_strings():
    """As strings, "1.8.9" sorts after "1.8.12" and would never be updated."""
    assert due(reported="1.8.9", target="1.8.12") is True
    assert due(reported="1.8.12", target="1.8.9") is False


def test_a_device_that_does_not_report_a_version_is_not_flashed_blind():
    assert due(reported=None) is False
    assert due(reported="") is False
    assert due(reported="unknown") is False


def test_no_target_means_no_updates():
    assert due(target="") is False


def test_an_offer_is_not_repeated_inside_the_retry_window():
    assert due(last_offer=NOW - timedelta(minutes=15)) is False
    assert due(last_offer=NOW - WINDOW + timedelta(seconds=1)) is False


def test_an_offer_is_repeated_once_the_window_has_passed():
    assert due(last_offer=NOW - WINDOW) is True
    assert due(last_offer=NOW - timedelta(days=2)) is True


# --- settings -----------------------------------------------------------------


def test_the_target_accepts_the_usual_spellings():
    for raw in ("1.8.16", " v1.8.16 ", "FW1.8.16"):
        assert Settings(firmware_target=raw).firmware_target == "1.8.16"


def test_a_malformed_target_is_rejected():
    """It would never parse, and the update would silently never be offered."""
    for raw in ("1.8", "latest", "1.8.16.1"):
        with pytest.raises(ValidationError):
            Settings(firmware_target=raw)


def test_the_download_url_defaults_to_the_trmnl_release():
    assert Settings(firmware_target="1.8.16").firmware_download_url == release_url("1.8.16")


def test_no_target_means_no_download_url():
    assert Settings(firmware_target="").firmware_download_url == ""
    # A URL on its own is not a rollout: there is no version to stop at.
    assert Settings(firmware_url="https://example.test/x.bin").firmware_download_url == ""


def test_a_custom_url_must_be_https():
    with pytest.raises(ValidationError):
        Settings(firmware_target="1.8.16", firmware_url="http://example.test/x.bin")


def test_the_retry_window_must_be_positive():
    with pytest.raises(ValidationError):
        Settings(firmware_retry_hours=0)
