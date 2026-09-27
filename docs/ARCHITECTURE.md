# Architecture

## Big picture

```
feeds.txt ─► feeds.load_feeds ─► feeds.scan_feed ─► detect.find_video_urls ─► VideoRef
                                  (one bad feed is logged, then skipped)          │
                                                                                  ▼
             already in videos? ─yes─► skip
             in failed_downloads and backing off / given up? ─yes─► defer
                     │ no
                     ▼
             ytdlp.download(fetch_url) ──ok──► db.insert_video, db.clear_failure
                     │ fail: classify_error(stderr) → Verdict
                     ├─ UPCOMING / THROTTLED ─► record, no penalty (THROTTLED stops downloads this run)
                     └─ other ─► record attempt, exponential backoff, give up after --max-attempts

videos (least recently checked first, --check-limit) ─► ytdlp.probe(fetch_url) ─► Verdict
    AVAILABLE / GEO_BLOCKED / RESTRICTED ─► exists: set state, restore if it was gone
    REMOVED / PRIVATE                    ─► gone immediately
    GONE_WEAK                            ─► strike; gone once strikes ≥ --confirm-checks
    UNKNOWN / UPCOMING                   ─► leave state, update last_checked only
    THROTTLED                            ─► skip; stop the run after 3 in a row
  ─► notify.send(new gone / restored)
```

## Modules

### `detect.py`
- `find_video_urls(text)` HTML-unescapes the text, runs the YouTube and Vimeo
  patterns, and returns `VideoRef`s deduplicated on `(platform, video_id)` in order
  of first appearance. When a Vimeo video shows up both with and without its
  privacy hash, the hashed `fetch_url` wins.
- YouTube IDs must be exactly 11 characters, `[A-Za-z0-9_-]`.
- Canonical forms: `https://www.youtube.com/watch?v=ID` and `https://vimeo.com/ID`.
  Unlisted Vimeo videos get the fetch URL `https://vimeo.com/ID/HASH`.

### `feeds.py`
- `feedparser.parse(url, sanitize_html=False)`. The sanitizer would strip the
  `<iframe>` embeds that blogs use for players.
- `entry_text` joins link, title, summary, description, content, links,
  `media_content`, and `media_player`.
- It raises `FeedError` for unparseable feeds and HTTP ≥ 400. `run_scan` catches this for each feed.

### `ytdlp.py`
- `download()` builds the command in `build_download_cmd`:
  `-f bestvideo+bestaudio/best --merge-output-format mkv --write-info-json
  --match-filters !is_live --no-simulate --print after_move:%()j` (+ `--write-thumbnail`,
  and optionally `--write-subs --sub-langs all,-live_chat`). Output template:
  `<dir>/%(extractor)s/%(id)s - %(title)s.%(ext)s`. Timeout 1 h.
- `extract_record_fields()` stores the real on-disk size and an integer duration.
- `probe()` runs `yt-dlp --simulate --no-playlist --print id URL`, with a 120 s timeout.
- `classify_error()` goes through `_RULES` in order: THROTTLED, GEO_BLOCKED, UPCOMING,
  RESTRICTED, PRIVATE, REMOVED, GONE_WEAK, and falls back to UNKNOWN. The order
  matters because YouTube prefixes rate limits and geo-blocks with "Video unavailable.".
  Only `ERROR:` lines from stderr are classified.

### `db.py`
- `MIGRATIONS` is a list of functions. `PRAGMA user_version` is the number that
  have been applied. Version 0 means a DB from the original script (or a new one).
  v1 is the original schema, written with `IF NOT EXISTS` so legacy DBs pass through
  untouched. v2 renames `canonical_url` to `fetch_url`, and adds `availability`,
  `gone_strikes`, `gone_since`, the `failed_downloads` table, a unique
  `(platform, video_id)` index, and an index on `last_checked`. It also drops a
  redundant index, cleans up `duration='None'`, and backfills real file sizes.
- `migrate()` backs up an existing DB with SQLite's backup API before it upgrades.
  It refuses to open a DB with a newer schema than the code.
- Availability constants: `AVAILABLE`, `GEO_BLOCKED`, `RESTRICTED`, `REMOVED`,
  `PRIVATE`, `UNAVAILABLE`. `GONE_STATES` sets `is_available = 0`.
- Backoff in `record_failure`: `2^(attempts-1)` hours, capped at 168 h. Once
  `attempts ≥ max_attempts`, `gave_up = 1`.

### `workflows.py`
The `run_*(args, logger) -> int` functions return the number of errors that
weren't fatal. `cli.main` turns a non-zero count into exit code 2.
`deleted_date` records the *first* detection. For a generic "gone" result, that
is `gone_since`, the time of the first strike.

### `cli.py`
- Subcommands share a parent parser, `common`, with `--db --feeds --download-dir --log -v --notify-url`.
- `run_lock` takes a non-blocking `fcntl.flock` on `<db>.lock` for scan, check, and retry.
- Exit codes: 0 ok, 1 fatal, 2 partial, 3 locked.
- The yt-dlp binary is required only for scan and check.

## Operations
- `cron_run.sh` runs scan and then check. Check still runs if scan fails. The
  script exits with the higher of the two exit codes. If `./.venv` exists, the
  script uses its Python and puts its `bin/` (for yt-dlp) on `PATH`.
- Relative default paths resolve against the working directory, which is why
  `cron_run.sh` changes into the repo directory first.
