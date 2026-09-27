"""SQLite storage: schema migrations and queries.

The schema version is kept in ``PRAGMA user_version``. Databases created by
the original single-file script have version 0 and are upgraded in place,
after a backup copy is written next to the database file.
Migrations must never drop archive data.
"""

from __future__ import annotations

import datetime
import logging
import os
import sqlite3
from collections.abc import Callable
from pathlib import Path

# Availability states stored in videos.availability.
AVAILABLE = "available"
GEO_BLOCKED = "geo_blocked"      # exists, but not viewable from this location
RESTRICTED = "restricted"        # exists, but members-only / age-gated / login
REMOVED = "removed"              # removed by uploader, ToS, copyright, terminated account
PRIVATE = "private"
UNAVAILABLE = "unavailable"      # generic "gone" confirmed over several checks
GONE_STATES = (REMOVED, PRIVATE, UNAVAILABLE)


def utcnow() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def _add_hours(iso: str, hours: float) -> str:
    return (datetime.datetime.fromisoformat(iso) + datetime.timedelta(hours=hours)).isoformat()


# ---------------------------------------------------------------------------
# Migrations
# ---------------------------------------------------------------------------

_V1_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS videos (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        url             TEXT    UNIQUE NOT NULL,
        canonical_url   TEXT,
        title           TEXT,
        platform        TEXT    CHECK(platform IN ('youtube', 'vimeo')),
        video_id        TEXT,
        feed_source     TEXT,
        download_path   TEXT,
        file_size_bytes INTEGER,
        duration        TEXT,
        uploader        TEXT,
        resolution      TEXT,
        first_seen      TEXT    NOT NULL,
        last_checked    TEXT,
        is_available    INTEGER NOT NULL DEFAULT 1,
        deleted_date    TEXT,
        metadata_json   TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_videos_url ON videos(url)",
    "CREATE INDEX IF NOT EXISTS idx_videos_available ON videos(is_available)",
    "CREATE INDEX IF NOT EXISTS idx_videos_platform ON videos(platform)",
]


def _migrate_v1(conn: sqlite3.Connection) -> None:
    """The original schema (idempotent, so legacy DBs pass through unchanged)."""
    for stmt in _V1_SCHEMA:
        conn.execute(stmt)


def _migrate_v2(conn: sqlite3.Connection) -> None:
    # canonical_url always duplicated url; repurpose it as the URL handed to
    # yt-dlp (differs from url only for unlisted Vimeo videos).
    conn.execute("ALTER TABLE videos RENAME COLUMN canonical_url TO fetch_url")
    conn.execute("UPDATE videos SET fetch_url = url WHERE fetch_url IS NULL")

    conn.execute("ALTER TABLE videos ADD COLUMN availability TEXT")
    conn.execute(
        "UPDATE videos SET availability = CASE is_available WHEN 1 THEN ? ELSE ? END",
        (AVAILABLE, UNAVAILABLE),
    )
    # Consecutive ambiguous "gone" results; see workflows.run_check.
    conn.execute("ALTER TABLE videos ADD COLUMN gone_strikes INTEGER NOT NULL DEFAULT 0")
    conn.execute("ALTER TABLE videos ADD COLUMN gone_since TEXT")

    # The old code stored str(None) when yt-dlp had no duration.
    conn.execute("UPDATE videos SET duration = NULL WHERE duration IN ('None', '')")

    # Replace yt-dlp's size estimate with the real on-disk size where possible.
    for row_id, path in conn.execute(
        "SELECT id, download_path FROM videos WHERE download_path IS NOT NULL"
    ).fetchall():
        if path and os.path.isfile(path):
            conn.execute(
                "UPDATE videos SET file_size_bytes = ? WHERE id = ?",
                (os.path.getsize(path), row_id),
            )

    conn.execute("DROP INDEX IF EXISTS idx_videos_url")  # duplicates the UNIQUE index
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_videos_platform_vid ON videos(platform, video_id)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_videos_last_checked ON videos(last_checked)")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS failed_downloads (
            platform      TEXT    NOT NULL,
            video_id      TEXT    NOT NULL,
            url           TEXT    NOT NULL,
            fetch_url     TEXT    NOT NULL,
            feed_source   TEXT,
            entry_title   TEXT,
            first_seen    TEXT    NOT NULL,
            last_attempt  TEXT,
            attempts      INTEGER NOT NULL DEFAULT 0,
            next_attempt  TEXT,
            gave_up       INTEGER NOT NULL DEFAULT 0,
            last_error    TEXT,
            PRIMARY KEY (platform, video_id)
        )
        """
    )


MIGRATIONS: list[Callable[[sqlite3.Connection], None]] = [_migrate_v1, _migrate_v2]
SCHEMA_VERSION = len(MIGRATIONS)


def _backup(conn: sqlite3.Connection, db_path: str, version: int, logger: logging.Logger) -> None:
    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
    dest = f"{db_path}.bak-v{version}-{stamp}"
    with sqlite3.connect(dest) as bak:
        conn.backup(bak)
    bak.close()
    logger.info("Backed up database to %s before migrating", dest)


def migrate(conn: sqlite3.Connection, db_path: str, logger: logging.Logger) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        raise RuntimeError(
            f"{db_path} has schema v{version}, newer than this code (v{SCHEMA_VERSION}). "
            "Update marion-stokes."
        )
    if version == SCHEMA_VERSION:
        return

    has_data = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='videos'"
    ).fetchone()
    if has_data and db_path != ":memory:":
        _backup(conn, db_path, version, logger)

    for target in range(version + 1, SCHEMA_VERSION + 1):
        conn.execute("BEGIN")
        try:
            MIGRATIONS[target - 1](conn)
            conn.execute(f"PRAGMA user_version = {target}")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        logger.info("Migrated database schema to v%d", target)


def connect(db_path: str, logger: logging.Logger) -> sqlite3.Connection:
    """Open (creating if needed) and migrate the database. Autocommit mode."""
    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, isolation_level=None, timeout=30)
    conn.row_factory = sqlite3.Row
    migrate(conn, db_path, logger)
    return conn


# ---------------------------------------------------------------------------
# videos
# ---------------------------------------------------------------------------

def video_exists(conn: sqlite3.Connection, platform: str, video_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM videos WHERE platform = ? AND video_id = ?", (platform, video_id)
    ).fetchone()
    return row is not None


def insert_video(conn: sqlite3.Connection, data: dict) -> None:
    conn.execute(
        """
        INSERT INTO videos
            (url, fetch_url, title, platform, video_id, feed_source,
             download_path, file_size_bytes, duration, uploader, resolution,
             first_seen, last_checked, is_available, availability, metadata_json)
        VALUES
            (:url, :fetch_url, :title, :platform, :video_id, :feed_source,
             :download_path, :file_size_bytes, :duration, :uploader, :resolution,
             :first_seen, :last_checked, 1, 'available', :metadata_json)
        """,
        data,
    )


def videos_to_check(conn: sqlite3.Connection, limit: int | None) -> list[sqlite3.Row]:
    """Least recently checked first, so a capped run rotates through the archive."""
    sql = "SELECT * FROM videos ORDER BY last_checked IS NOT NULL, last_checked, id"
    if limit:
        return conn.execute(sql + " LIMIT ?", (limit,)).fetchall()
    return conn.execute(sql).fetchall()


def set_availability(
    conn: sqlite3.Connection,
    row_id: int,
    availability: str,
    *,
    now: str,
    deleted_date: str | None = None,
) -> None:
    """Record a definite check result and reset any pending strikes."""
    gone = availability in GONE_STATES
    conn.execute(
        """
        UPDATE videos
           SET availability = ?, is_available = ?, deleted_date = ?,
               gone_strikes = 0, gone_since = NULL, last_checked = ?
         WHERE id = ?
        """,
        (availability, 0 if gone else 1, (deleted_date or now) if gone else None, now, row_id),
    )


def add_gone_strike(conn: sqlite3.Connection, row_id: int, now: str) -> tuple[int, str]:
    """Count one ambiguous "gone" result. Returns (strikes so far, first strike time)."""
    conn.execute(
        """
        UPDATE videos
           SET gone_strikes = gone_strikes + 1, gone_since = COALESCE(gone_since, ?),
               last_checked = ?
         WHERE id = ?
        """,
        (now, now, row_id),
    )
    row = conn.execute(
        "SELECT gone_strikes, gone_since FROM videos WHERE id = ?", (row_id,)
    ).fetchone()
    return row[0], row[1]


def touch_checked(conn: sqlite3.Connection, row_id: int, now: str) -> None:
    conn.execute("UPDATE videos SET last_checked = ? WHERE id = ?", (now, row_id))


# ---------------------------------------------------------------------------
# failed_downloads
# ---------------------------------------------------------------------------

def failure_blocks_attempt(conn: sqlite3.Connection, platform: str, video_id: str, now: str) -> bool:
    row = conn.execute(
        "SELECT gave_up, next_attempt FROM failed_downloads WHERE platform = ? AND video_id = ?",
        (platform, video_id),
    ).fetchone()
    if row is None:
        return False
    return bool(row["gave_up"]) or (row["next_attempt"] is not None and row["next_attempt"] > now)


def record_failure(
    conn: sqlite3.Connection,
    ref,
    *,
    feed_source: str,
    entry_title: str,
    error: str,
    now: str,
    penalise: bool,
    max_attempts: int,
) -> tuple[int, bool]:
    """Record a failed download. Returns (attempts, gave_up).

    ``penalise=False`` (upcoming premiere, live now) records the error but
    doesn't count an attempt or delay the next try.
    """
    conn.execute(
        """
        INSERT INTO failed_downloads
            (platform, video_id, url, fetch_url, feed_source, entry_title, first_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(platform, video_id) DO UPDATE SET fetch_url = excluded.fetch_url
        """,
        (ref.platform, ref.video_id, ref.url, ref.fetch_url, feed_source, entry_title, now),
    )
    attempts = conn.execute(
        "SELECT attempts FROM failed_downloads WHERE platform = ? AND video_id = ?",
        (ref.platform, ref.video_id),
    ).fetchone()[0]
    next_attempt = None
    gave_up = False
    if penalise:
        attempts += 1
        # 1h, 2h, 4h ... capped at one week.
        next_attempt = _add_hours(now, min(2 ** (attempts - 1), 168))
        gave_up = attempts >= max_attempts
    conn.execute(
        """
        UPDATE failed_downloads
           SET attempts = ?, last_attempt = ?, next_attempt = ?, gave_up = ?, last_error = ?
         WHERE platform = ? AND video_id = ?
        """,
        (attempts, now, next_attempt, int(gave_up), error[:2000], ref.platform, ref.video_id),
    )
    return attempts, gave_up


def clear_failure(conn: sqlite3.Connection, platform: str, video_id: str) -> None:
    conn.execute(
        "DELETE FROM failed_downloads WHERE platform = ? AND video_id = ?", (platform, video_id)
    )


def reset_failures(conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        "UPDATE failed_downloads SET attempts = 0, next_attempt = NULL, gave_up = 0"
    )
    return cur.rowcount
