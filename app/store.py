"""Persistence.

Everything here has to survive a redeploy. A container's filesystem is
replaced on every deploy, so this database and the rendered image live on a
mounted volume. Losing the
device's api_key would silently unpair the display on every deploy.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    mac           TEXT PRIMARY KEY,
    api_key       TEXT NOT NULL,
    friendly_id   TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    last_seen     TEXT,
    last_battery  REAL,
    last_rssi     INTEGER,
    last_firmware TEXT
);

CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS calendar_cache (
    name       TEXT PRIMARY KEY,
    documents  TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);
"""

# Columns added to `devices` after the first deploy. CREATE TABLE IF NOT EXISTS
# leaves an existing table alone, and the live database sits on a volume that
# outlives every deploy, so these have to be added by hand on start-up.
LATER_DEVICE_COLUMNS = {
    "last_filename": "TEXT",
    "draws_since_full": "INTEGER NOT NULL DEFAULT 0",
    "last_full_date": "TEXT",
    "last_firmware_offer": "TEXT",
}


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


class Store:
    def __init__(self, data_dir: Path, image_ext: str = "png") -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "state.db"
        # The single source of truth for the served format: whoever renders
        # asks the store rather than reading the setting a second time, so the
        # bytes on disk and the filename in the API response cannot disagree.
        self.image_ext = image_ext
        self._lock = threading.Lock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(SCHEMA)
            existing = {row["name"] for row in conn.execute("PRAGMA table_info(devices)")}
            for column, definition in LATER_DEVICE_COLUMNS.items():
                if column not in existing:
                    conn.execute(f"ALTER TABLE devices ADD COLUMN {column} {definition}")
            # Without auto_vacuum SQLite never returns freed pages to the
            # filesystem, so the file only ever grows. Enabling it on an
            # existing database requires one VACUUM, which also reclaims
            # whatever has accumulated so far.
            if conn.execute("PRAGMA auto_vacuum").fetchone()[0] != 1:
                conn.execute("PRAGMA auto_vacuum = FULL")
                conn.commit()
                conn.execute("VACUUM")

    # --- devices ----------------------------------------------------------

    def get_device(self, mac: str) -> dict | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM devices WHERE mac = ?", (mac,)).fetchone()
        return dict(row) if row else None

    def create_device(self, mac: str, api_key: str, friendly_id: str) -> dict:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO devices (mac, api_key, friendly_id, created_at) VALUES (?, ?, ?, ?)",
                (mac, api_key, friendly_id, _utcnow()),
            )
            row = conn.execute("SELECT * FROM devices WHERE mac = ?", (mac,)).fetchone()
        return dict(row)

    def record_poll(
        self,
        mac: str,
        battery: float | None = None,
        rssi: int | None = None,
        firmware: str | None = None,
    ) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """UPDATE devices
                   SET last_seen = ?, last_battery = ?, last_rssi = ?, last_firmware = ?
                   WHERE mac = ?""",
                (_utcnow(), battery, rssi, firmware, mac),
            )

    def record_redraw(self, mac: str, filename: str, *, full_refresh: bool, today: str) -> None:
        """Note that ``filename`` was handed out and the panel will be redrawn.

        Only called when the name actually changed: an unchanged name means the
        device keeps its cached copy and never touches the panel, and counting
        that as a redraw would spread the full refreshes ever further apart.
        """
        with self._lock, self._connect() as conn:
            if full_refresh:
                conn.execute(
                    """UPDATE devices
                       SET last_filename = ?, draws_since_full = 0, last_full_date = ?
                       WHERE mac = ?""",
                    (filename, today, mac),
                )
            else:
                conn.execute(
                    """UPDATE devices
                       SET last_filename = ?, draws_since_full = COALESCE(draws_since_full, 0) + 1
                       WHERE mac = ?""",
                    (filename, mac),
                )

    def record_firmware_offer(self, mac: str, when: datetime) -> None:
        """Remember when this device was last told to update its firmware."""
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE devices SET last_firmware_offer = ? WHERE mac = ?",
                (when.astimezone(UTC).isoformat(), mac),
            )

    def list_devices(self) -> list[dict]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT * FROM devices ORDER BY created_at").fetchall()
        return [dict(r) for r in rows]

    # --- key/value --------------------------------------------------------

    def get_kv(self, key: str) -> str | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_kv(self, key: str, value: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO kv (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    # --- last known good calendar data ------------------------------------

    def save_calendar(self, name: str, documents: list[str]) -> None:
        payload = json.dumps(documents)
        with self._lock, self._connect() as conn:
            # A calendar export can be megabytes, and any UPDATE rewrites the
            # whole row. Rewriting an unchanged blob every fetch makes the
            # database file grow through fragmentation, so skip it entirely.
            row = conn.execute(
                "SELECT documents FROM calendar_cache WHERE name = ?", (name,)
            ).fetchone()
            if row and row["documents"] == payload:
                return
            conn.execute(
                "INSERT INTO calendar_cache (name, documents, fetched_at) VALUES (?, ?, ?) "
                "ON CONFLICT(name) DO UPDATE SET documents = excluded.documents, "
                "fetched_at = excluded.fetched_at",
                (name, payload, _utcnow()),
            )

    def list_cached_calendars(self) -> list[str]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT name FROM calendar_cache ORDER BY name").fetchall()
        return [r["name"] for r in rows]

    def load_calendar(self, name: str) -> tuple[list[str], str] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT documents, fetched_at FROM calendar_cache WHERE name = ?", (name,)
            ).fetchone()
        if not row:
            return None
        return json.loads(row["documents"]), row["fetched_at"]

    # --- rendered screen --------------------------------------------------

    def image_path(self, digest: str) -> Path:
        return self.data_dir / f"screen-{digest}.{self.image_ext}"

    def save_image(self, digest: str, payload: bytes, keep: int = 3) -> Path:
        path = self.image_path(digest)
        # Write to a temporary file first so a poll never sees a half-written image.
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(payload)
        tmp.replace(path)
        self.set_kv("current_digest", digest)

        # Every extension, not just the current one, so that switching the
        # served format leaves the old screens behind to age out rather than
        # stranding them on the volume forever.
        images = sorted(
            (p for p in self.data_dir.glob("screen-*") if p.suffix != ".tmp"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for stale in images[keep:]:
            stale.unlink(missing_ok=True)
        return path

    def current_digest(self) -> str | None:
        return self.get_kv("current_digest")
