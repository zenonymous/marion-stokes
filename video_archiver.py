#!/usr/bin/env python3
"""
Video Archiver — RSS Feed Video Monitor & Downloader

Reads RSS feeds from a file, detects YouTube/Vimeo links,
downloads videos at highest quality, logs them in SQLite,
and checks whether previously downloaded videos are still online.
"""

import argparse
import datetime
import hashlib
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import feedparser

# ---------------------------------------------------------------------------
# Configuration defaults (overridable via CLI flags)
# ---------------------------------------------------------------------------
DEFAULT_FEEDS_FILE = "feeds.txt"
DEFAULT_DB_FILE = "videos.db"
DEFAULT_DOWNLOAD_DIR = "downloads"
DEFAULT_LOG_FILE = "video_archiver.log"

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def setup_logging(log_file: str, verbose: bool = False) -> logging.Logger:
    logger = logging.getLogger("video_archiver")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )

    fh = logging.FileHandler(log_file)
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)

    ch = logging.StreamHandler()
    ch.setLevel(logging.DEBUG if verbose else logging.INFO)
    ch.setFormatter(fmt)

    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

DB_SCHEMA = """
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
);

CREATE INDEX IF NOT EXISTS idx_videos_url ON videos(url);
CREATE INDEX IF NOT EXISTS idx_videos_available ON videos(is_available);
CREATE INDEX IF NOT EXISTS idx_videos_platform ON videos(platform);
"""


def init_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(DB_SCHEMA)
    conn.commit()
    return conn


def video_exists(conn: sqlite3.Connection, url: str) -> bool:
    row = conn.execute("SELECT 1 FROM videos WHERE url = ?", (url,)).fetchone()
    return row is not None


def insert_video(conn: sqlite3.Connection, data: dict):
    conn.execute(
        """
        INSERT INTO videos
            (url, canonical_url, title, platform, video_id, feed_source,
             download_path, file_size_bytes, duration, uploader, resolution,
             first_seen, last_checked, is_available, metadata_json)
        VALUES
            (:url, :canonical_url, :title, :platform, :video_id, :feed_source,
             :download_path, :file_size_bytes, :duration, :uploader, :resolution,
             :first_seen, :last_checked, 1, :metadata_json)
        """,
        data,
    )
    conn.commit()


def mark_deleted(conn: sqlite3.Connection, url: str):
    now = datetime.datetime.now(datetime.UTC).isoformat()
    conn.execute(
        """
        UPDATE videos
           SET is_available = 0, deleted_date = ?, last_checked = ?
         WHERE url = ?
        """,
        (now, now, url),
    )
    conn.commit()


def mark_available(conn: sqlite3.Connection, url: str):
    now = datetime.datetime.now(datetime.UTC).isoformat()
    conn.execute(
        "UPDATE videos SET is_available = 1, last_checked = ?, deleted_date = NULL WHERE url = ?",
        (now, url),
    )
    conn.commit()


def get_all_videos(conn: sqlite3.Connection):
    return conn.execute("SELECT * FROM videos").fetchall()


# ---------------------------------------------------------------------------
# URL detection & normalisation
# ---------------------------------------------------------------------------

# Patterns that match YouTube and Vimeo URLs in feed content
VIDEO_PATTERNS = [
    # YouTube
    re.compile(r'https?://(?:www\.)?youtube\.com/watch\?[^\s"<>\)]+', re.I),
    re.compile(r'https?://youtu\.be/[A-Za-z0-9_-]+[^\s"<>\)]*', re.I),
    re.compile(r'https?://(?:www\.)?youtube\.com/embed/[A-Za-z0-9_-]+', re.I),
    re.compile(r'https?://(?:www\.)?youtube\.com/v/[A-Za-z0-9_-]+', re.I),
    re.compile(r'https?://(?:www\.)?youtube\.com/shorts/[A-Za-z0-9_-]+', re.I),
    # Vimeo
    re.compile(r'https?://(?:www\.)?vimeo\.com/\d+[^\s"<>\)]*', re.I),
    re.compile(r'https?://player\.vimeo\.com/video/\d+', re.I),
]


def detect_platform(url: str) -> str | None:
    lower = url.lower()
    if "youtube.com" in lower or "youtu.be" in lower:
        return "youtube"
    if "vimeo.com" in lower:
        return "vimeo"
    return None


def extract_video_id(url: str, platform: str) -> str | None:
    """Return the platform-specific video identifier."""
    if platform == "youtube":
        parsed = urlparse(url)
        if "youtu.be" in parsed.netloc:
            return parsed.path.lstrip("/").split("/")[0]
        qs = parse_qs(parsed.query)
        if "v" in qs:
            return qs["v"][0]
        # /embed/ID, /v/ID, /shorts/ID
        parts = parsed.path.strip("/").split("/")
        if len(parts) >= 2:
            return parts[-1]
    elif platform == "vimeo":
        parsed = urlparse(url)
        parts = parsed.path.strip("/").split("/")
        for p in parts:
            if p.isdigit():
                return p
    return None


def canonical_url(url: str, platform: str, vid: str) -> str:
    if platform == "youtube" and vid:
        return f"https://www.youtube.com/watch?v={vid}"
    if platform == "vimeo" and vid:
        return f"https://vimeo.com/{vid}"
    return url


def find_video_urls(text: str) -> list[dict]:
    """Scan a block of text for video URLs, return deduplicated list."""
    seen = set()
    results = []
    for pat in VIDEO_PATTERNS:
        for match in pat.finditer(text):
            raw_url = match.group(0).rstrip(".,;:!?)'\"")
            platform = detect_platform(raw_url)
            if not platform:
                continue
            vid = extract_video_id(raw_url, platform)
            canon = canonical_url(raw_url, platform, vid)
            key = (platform, vid or canon)
            if key in seen:
                continue
            seen.add(key)
            results.append(
                {"url": canon, "platform": platform, "video_id": vid}
            )
    return results


# ---------------------------------------------------------------------------
# Feed reading
# ---------------------------------------------------------------------------

def load_feeds(feeds_file: str) -> list[str]:
    path = Path(feeds_file)
    if not path.exists():
        raise FileNotFoundError(f"Feeds file not found: {feeds_file}")
    feeds = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            feeds.append(line)
    return feeds


def scan_feed(feed_url: str, logger: logging.Logger) -> list[dict]:
    """Parse one RSS/Atom feed, return video URL dicts with feed_source."""
    logger.info("Parsing feed: %s", feed_url)
    feed = feedparser.parse(feed_url)

    if feed.bozo and not feed.entries:
        logger.warning("Failed to parse feed %s: %s", feed_url, feed.bozo_exception)
        return []

    videos = []
    for entry in feed.entries:
        # Build a text blob from all useful fields
        blobs = [
            entry.get("link", ""),
            entry.get("title", ""),
            entry.get("summary", ""),
            entry.get("description", ""),
        ]
        # Some feeds put video URLs inside content or media fields
        for content_block in entry.get("content", []):
            blobs.append(content_block.get("value", ""))
        for link_obj in entry.get("links", []):
            blobs.append(link_obj.get("href", ""))
        # media:content / media:player
        for mc in entry.get("media_content", []):
            blobs.append(mc.get("url", ""))
        if hasattr(entry, "media_player"):
            blobs.append(getattr(entry.media_player, "url", "") or "")
            blobs.append(getattr(entry.media_player, "content", "") or "")

        combined = "\n".join(blobs)
        found = find_video_urls(combined)
        for v in found:
            v["feed_source"] = feed_url
            v["entry_title"] = entry.get("title", "")
        videos.extend(found)

    logger.info("  Found %d video link(s) in feed", len(videos))
    return videos


# ---------------------------------------------------------------------------
# Downloading with yt-dlp
# ---------------------------------------------------------------------------

def ytdlp_available() -> bool:
    try:
        subprocess.run(
            ["yt-dlp", "--version"],
            capture_output=True, check=True, text=True,
        )
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def download_video(url: str, download_dir: str, logger: logging.Logger) -> dict | None:
    """
    Download a video with yt-dlp at highest quality.
    Returns a dict of metadata on success, None on failure.
    """
    os.makedirs(download_dir, exist_ok=True)

    # Use an output template that includes platform and video id
    outtmpl = os.path.join(download_dir, "%(extractor)s/%(id)s - %(title)s.%(ext)s")

    cmd = [
        "yt-dlp",
        "--no-playlist",
        # Best video+audio merged, fallback to best single file
        "-f", "bestvideo+bestaudio/best",
        "--merge-output-format", "mkv",
        "--write-info-json",
        "--output", outtmpl,
        "--print-json",          # dump metadata JSON to stdout
        "--no-progress",
        url,
    ]

    logger.info("  Downloading: %s", url)
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=3600,
        )
    except subprocess.TimeoutExpired:
        logger.error("  Download timed out for %s", url)
        return None

    if result.returncode != 0:
        logger.error("  yt-dlp failed (rc=%d): %s", result.returncode, result.stderr[:500])
        return None

    # yt-dlp --print-json may output multiple JSON objects (one per format);
    # take the last complete one which represents the final merged file.
    meta = None
    for line in result.stdout.strip().splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                meta = json.loads(line)
            except json.JSONDecodeError:
                continue

    if not meta:
        logger.warning("  Could not parse yt-dlp JSON output for %s", url)
        return None

    filepath = meta.get("_filename") or meta.get("filename", "")
    return {
        "title": meta.get("title", "Unknown"),
        "download_path": filepath,
        "file_size_bytes": meta.get("filesize") or meta.get("filesize_approx"),
        "duration": str(meta.get("duration", "")),
        "uploader": meta.get("uploader", ""),
        "resolution": meta.get("resolution", ""),
        "metadata_json": json.dumps(
            {k: meta[k] for k in (
                "id", "title", "description", "uploader", "upload_date",
                "duration", "view_count", "like_count", "resolution",
                "fps", "vcodec", "acodec", "ext", "format",
            ) if k in meta},
            indent=2,
        ),
    }


# ---------------------------------------------------------------------------
# Availability checking
# ---------------------------------------------------------------------------

def check_availability(url: str, logger: logging.Logger) -> bool:
    """Return True if the video is still available online."""
    cmd = [
        "yt-dlp",
        "--simulate",            # don't download
        "--no-playlist",
        "--skip-download",
        "--print", "id",         # just print the id if reachable
        url,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0:
            return True
        # yt-dlp returns non-zero for unavailable / private / deleted
        stderr = result.stderr.lower()
        unavailable_signals = [
            "video unavailable", "removed", "private video",
            "been deleted", "this video is no longer available",
            "not available", "account associated", "terminated",
            "copyright", "violates", "404", "does not exist",
        ]
        for sig in unavailable_signals:
            if sig in stderr:
                return False
        # Some other error (network issue etc.) — treat as still available
        # to avoid false-flagging on transient problems.
        logger.warning(
            "  Ambiguous yt-dlp result for %s (rc=%d), assuming available. stderr: %s",
            url, result.returncode, stderr[:300],
        )
        return True
    except subprocess.TimeoutExpired:
        logger.warning("  Availability check timed out for %s, assuming available", url)
        return True


# ---------------------------------------------------------------------------
# Main workflow
# ---------------------------------------------------------------------------

def run_scan(args, logger: logging.Logger):
    """Main scan: read feeds, download new videos."""
    conn = init_db(args.db)
    feeds = load_feeds(args.feeds)
    logger.info("Loaded %d feed(s) from %s", len(feeds), args.feeds)

    new_count = 0
    skip_count = 0

    for feed_url in feeds:
        videos = scan_feed(feed_url, logger)
        for v in videos:
            url = v["url"]
            if video_exists(conn, url):
                logger.debug("  Already known: %s", url)
                skip_count += 1
                continue

            # Download
            dl = download_video(url, args.download_dir, logger)
            if dl is None:
                logger.warning("  Skipping %s (download failed)", url)
                continue

            now = datetime.datetime.now(datetime.UTC).isoformat()
            record = {
                "url": url,
                "canonical_url": v["url"],
                "title": dl["title"] or v.get("entry_title", ""),
                "platform": v["platform"],
                "video_id": v.get("video_id"),
                "feed_source": v["feed_source"],
                "download_path": dl["download_path"],
                "file_size_bytes": dl["file_size_bytes"],
                "duration": dl["duration"],
                "uploader": dl["uploader"],
                "resolution": dl["resolution"],
                "first_seen": now,
                "last_checked": now,
                "metadata_json": dl["metadata_json"],
            }
            insert_video(conn, record)
            logger.info("  ✓ Saved: %s — %s", dl["title"], url)
            new_count += 1

    logger.info("Scan complete. New: %d | Already known: %d", new_count, skip_count)
    conn.close()


def run_check(args, logger: logging.Logger):
    """Check availability of all previously downloaded videos."""
    conn = init_db(args.db)
    rows = get_all_videos(conn)
    logger.info("Checking availability of %d video(s)…", len(rows))

    still_up = 0
    newly_deleted = 0
    restored = 0

    for row in rows:
        url = row["url"]
        was_available = bool(row["is_available"])
        is_up = check_availability(url, logger)

        if is_up and not was_available:
            logger.info("  ↑ Restored: %s — %s", row["title"], url)
            mark_available(conn, url)
            restored += 1
        elif not is_up and was_available:
            logger.info("  ✗ Deleted:  %s — %s", row["title"], url)
            mark_deleted(conn, url)
            newly_deleted += 1
        elif is_up:
            still_up += 1
            now = datetime.datetime.now(datetime.UTC).isoformat()
            conn.execute("UPDATE videos SET last_checked = ? WHERE url = ?", (now, url))

        conn.commit()

    logger.info(
        "Check complete. Available: %d | Newly deleted: %d | Restored: %d",
        still_up, newly_deleted, restored,
    )
    conn.close()


def run_status(args, logger: logging.Logger):
    """Print a summary of the database."""
    conn = init_db(args.db)
    total = conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0]
    avail = conn.execute("SELECT COUNT(*) FROM videos WHERE is_available = 1").fetchone()[0]
    deleted = conn.execute("SELECT COUNT(*) FROM videos WHERE is_available = 0").fetchone()[0]

    print(f"\n{'═' * 60}")
    print(f"  Video Archiver Database: {args.db}")
    print(f"{'═' * 60}")
    print(f"  Total videos tracked : {total}")
    print(f"  Still available      : {avail}")
    print(f"  Flagged as deleted   : {deleted}")
    print(f"{'═' * 60}")

    if deleted > 0:
        print("\n  Deleted videos:")
        for row in conn.execute(
            "SELECT title, url, platform, deleted_date FROM videos WHERE is_available = 0"
        ):
            print(f"    [{row[2]}] {row[0]}")
            print(f"      URL: {row[1]}")
            print(f"      Deleted: {row[3]}")
        print()

    conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Video Archiver — monitor RSS feeds, download & track YouTube/Vimeo videos",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  %(prog)s scan                         # read feeds, download new videos
  %(prog)s check                        # check if downloads are still online
  %(prog)s scan --feeds my_feeds.txt    # use a custom feeds file
  %(prog)s status                       # print database summary
  %(prog)s scan && %(prog)s check       # do both in sequence
        """,
    )
    parser.add_argument(
        "command",
        choices=["scan", "check", "status"],
        help="scan = read feeds & download | check = verify availability | status = show summary",
    )
    parser.add_argument("--feeds", default=DEFAULT_FEEDS_FILE, help="path to feeds file")
    parser.add_argument("--db", default=DEFAULT_DB_FILE, help="SQLite database path")
    parser.add_argument("--download-dir", default=DEFAULT_DOWNLOAD_DIR, help="video download directory")
    parser.add_argument("--log", default=DEFAULT_LOG_FILE, help="log file path")
    parser.add_argument("--verbose", "-v", action="store_true", help="verbose output")

    args = parser.parse_args()
    logger = setup_logging(args.log, args.verbose)

    if not ytdlp_available():
        logger.error("yt-dlp is not installed or not in PATH. Please install it first.")
        logger.error("  pip install yt-dlp   OR   https://github.com/yt-dlp/yt-dlp#installation")
        sys.exit(1)

    if args.command == "scan":
        run_scan(args, logger)
    elif args.command == "check":
        run_check(args, logger)
    elif args.command == "status":
        run_status(args, logger)


if __name__ == "__main__":
    main()
