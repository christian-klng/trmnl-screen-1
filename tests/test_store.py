"""The database sits on a volume that outlives every deploy, so schema changes
have to reach a file that already exists — CREATE TABLE IF NOT EXISTS will not
do it, and a paired device must not be lost on the way."""

import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from app.store import LATER_DEVICE_COLUMNS, Store

# The `devices` table exactly as the first deploy created it.
ORIGINAL_SCHEMA = """
CREATE TABLE devices (
    mac           TEXT PRIMARY KEY,
    api_key       TEXT NOT NULL,
    friendly_id   TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    last_seen     TEXT,
    last_battery  REAL,
    last_rssi     INTEGER,
    last_firmware TEXT
);
"""

MAC = "AA:BB:CC:DD:EE:FF"


def old_database(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(tmp_path / "state.db")
    conn.executescript(ORIGINAL_SCHEMA)
    conn.execute(
        "INSERT INTO devices (mac, api_key, friendly_id, created_at) VALUES (?, ?, ?, ?)",
        (MAC, "secret-key", "N97Q65", "2026-08-13T09:00:00+00:00"),
    )
    conn.commit()
    conn.close()


def test_the_new_columns_are_added_to_an_existing_database(tmp_path):
    old_database(tmp_path)
    store = Store(tmp_path)

    device = store.get_device(MAC)
    assert set(LATER_DEVICE_COLUMNS) <= set(device)
    assert device["draws_since_full"] == 0
    assert device["last_filename"] is None


def test_a_paired_device_survives_the_migration(tmp_path):
    """Losing the api_key would silently unpair the display."""
    old_database(tmp_path)
    device = Store(tmp_path).get_device(MAC)
    assert device["api_key"] == "secret-key"
    assert device["friendly_id"] == "N97Q65"


def test_the_migration_runs_only_once(tmp_path):
    old_database(tmp_path)
    Store(tmp_path)
    store = Store(tmp_path)  # a second start-up must not trip over its own work
    store.record_redraw(MAC, "abc.png", full_refresh=False, today="2026-08-29")
    assert store.get_device(MAC)["draws_since_full"] == 1


def test_a_redraw_counts_and_a_full_refresh_resets(tmp_path):
    old_database(tmp_path)
    store = Store(tmp_path)

    for _ in range(3):
        store.record_redraw(MAC, "a.png", full_refresh=False, today="2026-08-29")
    assert store.get_device(MAC)["draws_since_full"] == 3

    store.record_redraw(MAC, "b.png", full_refresh=True, today="2026-08-29")
    device = store.get_device(MAC)
    assert device["draws_since_full"] == 0
    assert device["last_full_date"] == "2026-08-29"
    assert device["last_filename"] == "b.png"


def test_a_firmware_offer_is_remembered_in_utc(tmp_path):
    old_database(tmp_path)
    store = Store(tmp_path)
    assert store.get_device(MAC)["last_firmware_offer"] is None

    local = datetime(2026, 9, 15, 12, 0, tzinfo=ZoneInfo("Europe/Berlin"))
    store.record_firmware_offer(MAC, local)
    assert store.get_device(MAC)["last_firmware_offer"] == "2026-09-15T10:00:00+00:00"


# --- rendered screens on disk ----------------------------------------------


def test_the_image_extension_follows_the_served_format(tmp_path):
    assert Store(tmp_path / "png").image_path("abc").name == "screen-abc.png"
    assert Store(tmp_path / "bmp", "bmp").image_path("abc").name == "screen-abc.bmp"


def test_switching_format_does_not_strand_the_old_screens(tmp_path):
    """Cleanup used to glob one extension, so screens from before a switch
    would have sat on the volume forever."""
    bmp = Store(tmp_path, "bmp")
    for digest in ("aaa", "bbb", "ccc"):
        bmp.save_image(digest, b"old")

    png = Store(tmp_path, "png")
    for digest in ("ddd", "eee", "fff"):
        png.save_image(digest, b"new")

    left = sorted(p.name for p in tmp_path.glob("screen-*"))
    assert left == ["screen-ddd.png", "screen-eee.png", "screen-fff.png"]


def test_the_current_screen_is_never_cleaned_up(tmp_path):
    store = Store(tmp_path)
    for digest in ("aaa", "bbb", "ccc", "ddd", "eee"):
        store.save_image(digest, b"screen")
    assert store.image_path(store.current_digest()).exists()
