import sqlite3

import pytest

from marion_stokes import db
from marion_stokes.detect import VideoRef

LEGACY_SCHEMA = """
CREATE TABLE videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT UNIQUE NOT NULL, canonical_url TEXT, title TEXT,
    platform TEXT CHECK(platform IN ('youtube', 'vimeo')), video_id TEXT, feed_source TEXT,
    download_path TEXT, file_size_bytes INTEGER, duration TEXT, uploader TEXT, resolution TEXT,
    first_seen TEXT NOT NULL, last_checked TEXT, is_available INTEGER NOT NULL DEFAULT 1,
    deleted_date TEXT, metadata_json TEXT
);
CREATE INDEX idx_videos_url ON videos(url);
CREATE INDEX idx_videos_available ON videos(is_available);
CREATE INDEX idx_videos_platform ON videos(platform);
"""


def _legacy_db(path, video_file):
    conn = sqlite3.connect(path)
    conn.executescript(LEGACY_SCHEMA)
    conn.execute(
        "INSERT INTO videos (url, canonical_url, title, platform, video_id, download_path, file_size_bytes,"
        " duration, first_seen, is_available) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
         "up", "youtube", "dQw4w9WgXcQ", str(video_file), 999999, "None", "2024-01-01T00:00:00+00:00", 1),
    )
    conn.execute(
        "INSERT INTO videos (url, canonical_url, title, platform, video_id, duration, first_seen,"
        " is_available, deleted_date) VALUES (?,?,?,?,?,?,?,?,?)",
        ("https://vimeo.com/1", "https://vimeo.com/1", "gone", "vimeo", "1", "42",
         "2024-01-01T00:00:00+00:00", 0, "2024-02-01T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()


def test_migrates_legacy_db_without_losing_data(tmp_path, logger):
    video = tmp_path / "v.mkv"
    video.write_bytes(b"x" * 1234)
    path = str(tmp_path / "videos.db")
    _legacy_db(path, video)

    conn = db.connect(path, logger)

    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert list(tmp_path.glob("videos.db.bak-v0-*")), "a backup must be written before migrating"
    rows = {r["title"]: r for r in conn.execute("SELECT * FROM videos")}
    assert len(rows) == 2
    up, gone = rows["up"], rows["gone"]
    assert up["fetch_url"] == up["url"]
    assert up["availability"] == db.AVAILABLE
    assert up["duration"] is None                 # 'None' string cleaned up
    assert up["file_size_bytes"] == 1234          # real size from disk
    assert gone["availability"] == db.UNAVAILABLE
    assert gone["deleted_date"] == "2024-02-01T00:00:00+00:00"
    assert gone["duration"] == "42"
    indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "idx_videos_url" not in indexes
    assert "idx_videos_platform_vid" in indexes

    # Re-opening is a no-op and writes no new backup.
    conn.close()
    before = set(tmp_path.glob("videos.db.bak-*"))
    db.connect(path, logger).close()
    assert set(tmp_path.glob("videos.db.bak-*")) == before


def test_fresh_db_has_no_backup(tmp_path, logger):
    db.connect(str(tmp_path / "new.db"), logger).close()
    assert not list(tmp_path.glob("*.bak-*"))


def test_refuses_newer_schema(tmp_path, logger):
    path = str(tmp_path / "videos.db")
    conn = sqlite3.connect(path)
    conn.execute(f"PRAGMA user_version = {db.SCHEMA_VERSION + 1}")
    conn.close()
    with pytest.raises(RuntimeError, match="newer"):
        db.connect(path, logger)


def test_failure_backoff_and_give_up(logger):
    conn = db.connect(":memory:", logger)
    ref = VideoRef("youtube", "dQw4w9WgXcQ", "u", "u")
    now = "2026-01-01T00:00:00+00:00"
    kw = dict(feed_source="f", entry_title="t", error="boom", now=now, max_attempts=3)

    assert db.record_failure(conn, ref, penalise=True, **kw) == (1, False)
    assert db.failure_blocks_attempt(conn, "youtube", "dQw4w9WgXcQ", now)
    assert not db.failure_blocks_attempt(conn, "youtube", "dQw4w9WgXcQ", "2026-01-01T01:00:01+00:00")
    assert db.record_failure(conn, ref, penalise=True, **kw) == (2, False)
    row = conn.execute("SELECT next_attempt FROM failed_downloads").fetchone()
    assert row[0] == "2026-01-01T02:00:00+00:00"
    assert db.record_failure(conn, ref, penalise=True, **kw) == (3, True)
    assert db.failure_blocks_attempt(conn, "youtube", "dQw4w9WgXcQ", "2099-01-01T00:00:00+00:00")

    assert db.reset_failures(conn) == 1
    assert not db.failure_blocks_attempt(conn, "youtube", "dQw4w9WgXcQ", now)


def test_unpenalised_failure_does_not_count(logger):
    conn = db.connect(":memory:", logger)
    ref = VideoRef("youtube", "dQw4w9WgXcQ", "u", "u")
    now = "2026-01-01T00:00:00+00:00"
    assert db.record_failure(conn, ref, feed_source="f", entry_title="t", error="upcoming",
                             now=now, penalise=False, max_attempts=3) == (0, False)
    assert not db.failure_blocks_attempt(conn, "youtube", "dQw4w9WgXcQ", now)
