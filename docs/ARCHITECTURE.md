# Architecture

All code lives in `video_archiver.py`. This document explains how it fits together.
Line numbers refer to the version this document was written against. Search for
function names if they have drifted.

## Big picture

```
feeds.txt ──► load_feeds ──► scan_feed (feedparser) ──► find_video_urls
                                                          │ canonical URL + platform + id
                                                          ▼
                                  video_exists? ──yes──► skip
                                        │ no
                                        ▼
                         download_video (yt-dlp subprocess) ──fail──► log, skip (retried next scan)
                                        │ ok: metadata dict
                                        ▼
                                  insert_video ──► videos.db

videos.db ──► run_check ──► check_availability (yt-dlp --simulate) ──► mark_deleted / mark_available
videos.db ──► run_status ──► stdout summary
```

There are three independent subcommands, and each opens its own SQLite
connection. Nothing runs concurrently, and there is no lock against two
overlapping runs.

## Modules (sections of the script)

### Logging: `setup_logging`
The `video_archiver` logger writes to a file handler (always DEBUG) and a console
handler (INFO, or DEBUG with `-v`). The logger is passed to functions explicitly.

### Database: `DB_SCHEMA`, `init_db`, `video_exists`, `insert_video`, `mark_deleted`, `mark_available`, `get_all_videos`
- One table, `videos`. The README has the full column table.
- `init_db` runs `CREATE TABLE/INDEX IF NOT EXISTS` on every start, so it cannot
  migrate an existing table. New columns need an explicit `ALTER TABLE`.
- `platform` has a `CHECK (platform IN ('youtube','vimeo'))` constraint.
- Timestamps are ISO 8601 UTC strings (`datetime.now(datetime.UTC).isoformat()`).
- Each helper commits right away. There are no batched transactions.
- `idx_videos_url` duplicates the index SQLite already builds for `UNIQUE`.

### URL detection: `VIDEO_PATTERNS`, `detect_platform`, `extract_video_id`, `canonical_url`, `find_video_urls`
- `find_video_urls(text)` runs every regex over a text blob, strips trailing
  punctuation, works out the platform and ID, canonicalises the URL, and
  dedupes on `(platform, id)`.
- Canonical forms: `https://www.youtube.com/watch?v=<id>` and `https://vimeo.com/<id>`.
- Recognised: `youtube.com/watch?…v=`, `youtu.be/`, `youtube.com/embed/`, `/v/`, `/shorts/`,
  `vimeo.com/<digits>`, `player.vimeo.com/video/<digits>`.
- Not recognised (checked): `m.youtube.com`, `youtube-nocookie.com`, `youtube.com/live/`,
  `vimeo.com/channels/<name>/<id>`.

### Feed reading: `load_feeds`, `scan_feed`
- `load_feeds` returns non-empty lines that don't start with `#`.
- `scan_feed` calls `feedparser.parse(url)`. If the result is bozo with no entries,
  the feed is skipped with a warning. For each entry, it joins `link`, `title`,
  `summary`, `description`, every `content[].value`, `links[].href`,
  `media_content[].url`, and `media_player.url`/`content` into one blob, then runs
  `find_video_urls` on it. Each hit gets `feed_source` and `entry_title` added.

### Download: `ytdlp_available`, `download_video`
The script shells out to:
```
yt-dlp --no-playlist -f bestvideo+bestaudio/best --merge-output-format mkv
       --write-info-json --output "<dir>/%(extractor)s/%(id)s - %(title)s.%(ext)s"
       --print-json --no-progress <url>
```
- The timeout is 1 hour. A non-zero exit code or a timeout returns `None`.
- The last JSON line on stdout is parsed. The script keeps `_filename`, `filesize`
  (or `filesize_approx`), `duration`, `uploader`, `resolution`, and a subset of
  metadata keys, serialised into `metadata_json`.
- yt-dlp also writes a `.info.json` next to each video, with the full metadata.

### Availability: `check_availability`
- Runs `yt-dlp --simulate --skip-download --no-playlist --print id <url>` with a 120 s timeout.
- Exit code 0 means available.
- A non-zero exit code means **deleted only if** stderr (lower-cased) contains one of
  the `unavailable_signals` substrings. Any other error, or a timeout, counts as
  *available*, and the script logs a warning.

### Workflows: `run_scan`, `run_check`, `run_status`, `main`
- `run_scan`: for each feed, and each video found in it, skip it if its canonical
  URL is already in the DB. Otherwise download it, and insert a row only if the
  download succeeded. The title comes from yt-dlp, with the feed entry title as fallback.
- `run_check`: loads every row, including rows already marked deleted, so a
  video that comes back gets restored. The transitions are:
  - available → gone: `mark_deleted` (sets `deleted_date`)
  - gone → available: `mark_available` (clears `deleted_date`)
  - available → available: updates `last_checked`
  - gone → gone: **no update**, so `last_checked` stays unchanged
- `run_status`: prints counts and the list of deleted videos.
- `main`: argparse, then logging, then it exits with an error if the yt-dlp CLI is missing, then dispatches.

## Operations

- `cron_run.sh` changes into the script directory and runs `scan "$@"`, then `check "$@"`,
  under `set -euo pipefail`. A crash in `scan` means `check` doesn't run.
- All default paths are relative to the working directory. That's why the cron wrapper `cd`s first.
