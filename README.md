# Video Archiver

RSS feed monitor that automatically detects, downloads, and tracks YouTube and Vimeo videos.

## What it does

1. **Scan** — Reads RSS feeds from `feeds.txt`, finds YouTube/Vimeo links in every entry, downloads each new video at the highest available quality, and logs it in a local SQLite database.
2. **Check** — Re-checks every previously downloaded video to see if it's still available online. If a video has been deleted or made private, it sets an `is_available = 0` flag and records the deletion date. If a previously-deleted video comes back, it restores the flag.
3. **Status** — Prints a summary of the database: total tracked, still available, and flagged as deleted.

## Setup

Requires **Python 3.11+**, the `yt-dlp` command on your `PATH`, and `ffmpeg`.

```bash
# Install dependencies
pip install -r requirements.txt

# yt-dlp also needs ffmpeg for merging best video+audio
sudo apt install ffmpeg        # Debian/Ubuntu
# or: sudo dnf install ffmpeg  # Fedora
# or: brew install ffmpeg       # macOS
```

## Configuration

Edit `feeds.txt` and add one RSS feed URL per line. Comments (`#`) and blank lines are ignored.

```text
# YouTube channel
https://www.youtube.com/feeds/videos.xml?channel_id=UC_x5XG1OV2P6uZZ5FSM9Ttw

# A blog that embeds Vimeo videos
https://example.com/blog/feed

# Vimeo user feed
https://vimeo.com/someuser/videos/rss
```

### Finding YouTube channel feed URLs

Every YouTube channel has an RSS feed at:
```
https://www.youtube.com/feeds/videos.xml?channel_id=CHANNEL_ID
```
You can find the channel ID by viewing the page source of any YouTube channel page and searching for `channel_id`, or use a browser extension that reveals it.

## Usage

```bash
# Scan feeds and download new videos
python3 video_archiver.py scan

# Check if previously downloaded videos are still online
python3 video_archiver.py check

# Show database summary
python3 video_archiver.py status

# Run both scan and check together
python3 video_archiver.py scan && python3 video_archiver.py check
```

### Options

| Flag              | Default            | Description                     |
|-------------------|--------------------|---------------------------------|
| `--feeds FILE`    | `feeds.txt`        | Path to the feeds list file     |
| `--db FILE`       | `videos.db`        | SQLite database path            |
| `--download-dir`  | `downloads/`       | Where videos are saved          |
| `--log FILE`      | `video_archiver.log` | Log file path                 |
| `-v, --verbose`   | off                | Verbose / debug output          |

## Automating with cron

Use the included `cron_run.sh` to schedule scans. Make it executable and add a crontab entry:

```bash
chmod +x cron_run.sh

# Run every 6 hours
crontab -e
# Add this line (adjust the path):
0 */6 * * * /path/to/video_archiver/cron_run.sh >> /path/to/video_archiver/cron.log 2>&1
```

## Database schema

The SQLite database (`videos.db`) has a single `videos` table:

| Column            | Type    | Description                                      |
|-------------------|---------|--------------------------------------------------|
| `id`              | INTEGER | Auto-increment primary key                       |
| `url`             | TEXT    | Canonical video URL (unique)                     |
| `canonical_url`   | TEXT    | Normalized URL                                   |
| `title`           | TEXT    | Video title                                      |
| `platform`        | TEXT    | `youtube` or `vimeo`                             |
| `video_id`        | TEXT    | Platform-specific video ID                       |
| `feed_source`     | TEXT    | The RSS feed URL where this was found            |
| `download_path`   | TEXT    | Local file path of the downloaded video          |
| `file_size_bytes` | INTEGER | File size                                        |
| `duration`        | TEXT    | Video duration in seconds                        |
| `uploader`        | TEXT    | Channel / uploader name                          |
| `resolution`      | TEXT    | Download resolution                              |
| `first_seen`      | TEXT    | ISO timestamp of first detection                 |
| `last_checked`    | TEXT    | ISO timestamp of last availability check         |
| `is_available`    | INTEGER | `1` = still online, **`0` = deleted/unavailable**|
| `deleted_date`    | TEXT    | ISO timestamp when deletion was detected         |
| `metadata_json`   | TEXT    | Full yt-dlp metadata as JSON                     |

### Querying the database directly

```bash
# List all deleted videos
sqlite3 videos.db "SELECT title, url, deleted_date FROM videos WHERE is_available = 0;"

# Count by platform
sqlite3 videos.db "SELECT platform, COUNT(*) FROM videos GROUP BY platform;"

# Find large files
sqlite3 videos.db "SELECT title, file_size_bytes/1048576 AS mb FROM videos ORDER BY file_size_bytes DESC LIMIT 10;"
```

## How video detection works

The scanner searches every RSS entry's link, title, summary, description, content blocks, media enclosures, and `media:content` / `media:player` fields for URLs matching YouTube and Vimeo patterns. This means it works with:

- Native YouTube channel RSS feeds (which directly contain video links)
- Blog/news feeds that embed or link to YouTube/Vimeo videos within articles
- Podcast feeds that reference video content
- Any Atom or RSS 2.0 feed

## Download quality

Videos are downloaded at the **highest available quality** using `yt-dlp`'s `bestvideo+bestaudio/best` format selection, merged into MKV containers via ffmpeg. This means you'll typically get the best resolution available (often 4K or 1080p) with the best audio track.

## For contributors and AI agents

See [`AGENTS.md`](AGENTS.md) (also loaded through `CLAUDE.md`), [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md), and [`docs/KNOWN_ISSUES.md`](docs/KNOWN_ISSUES.md).
