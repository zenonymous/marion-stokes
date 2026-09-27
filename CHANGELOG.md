# Changelog

## 2.0.0

The single script is now the `marion_stokes` package. `python3 video_archiver.py <cmd>`
and `cron_run.sh` still work as before. Existing databases are upgraded
automatically, and a backup (`videos.db.bak-v0-<timestamp>`) is written first.

### Fixed
- **Videos wrongly flagged as deleted.** Errors such as "Requested format is not
  available", geo-blocks, and rate limits no longer count as deletions. Definite
  messages (removed, private, terminated, copyright) are acted on right away.
  Generic "Video unavailable" or 404 results must repeat on `--confirm-checks`
  checks in a row (default 2).
- **Embedded videos in blog feeds were missed.** feedparser's sanitizer stripped the
  `<iframe>` embeds. The HTML is now scanned before sanitizing.
- **Unlisted Vimeo videos** keep their privacy hash (`fetch_url`), so they can be downloaded.
- **Link forms that are now recognised:** `m.youtube.com`, `music.youtube.com`,
  `youtube-nocookie.com/embed`, `youtube.com/live/`, `vimeo.com/channels|groups|album|showcase/…/ID`,
  and `&amp;`-escaped URLs in HTML.
- `file_size_bytes` is the real size on disk (existing rows are backfilled where the file exists).
- `duration` no longer stores the string `"None"` (existing rows are cleaned up).
- `last_checked` is updated on every check, including for videos that are already gone.
- `status` no longer needs yt-dlp.

### Added
- **Retries with backoff for failed downloads** (`failed_downloads` table): 1 h, 2 h, 4 h … up to 7 days,
  giving up after `--max-attempts` (default 10). `retry` resets them. Upcoming
  premieres and streams that are live now are retried on every scan without counting as a failed attempt.
- **Run lock** (`videos.db.lock`) to prevent overlapping cron runs. Exit code 3 when locked.
- **Scalable `check`**: least recently checked first, `--check-limit` (default 500),
  `--check-delay` (default 3 s), and it stops after 3 rate-limit responses in a row.
- **Resilience**: one failing feed or video no longer stops the run. `cron_run.sh`
  runs `check` even if `scan` fails. Exit code 2 means the run finished with some errors.
- `availability` column with the states `available`, `geo_blocked`, `restricted`, `removed`, `private`, `unavailable`.
- `export` command (CSV or self-contained HTML, `--deleted-only`).
- `--notify-url` / `MARION_STOKES_NOTIFY_URL`: notification when videos go offline or come back (ntfy-compatible).
- Thumbnails are saved by default (`--no-thumbnails` turns this off). Subtitles with `--subs`.
- `--max-downloads` per run. `cron_run.sh` uses `./.venv` if present.
- Schema migrations (`PRAGMA user_version`), pytest suite, ruff, GitHub Actions CI,
  `pyproject.toml` with a `marion-stokes` console script, and `.gitignore`.

### Changed
- `canonical_url` column renamed to `fetch_url`. Uniqueness is now enforced on `(platform, video_id)`.
- **Breaking:** options must now come after the subcommand (`scan --db x`). Before, `--db x scan` also worked.
  The README examples and `cron_run.sh` already used this order.

## 1.0.0
- The original single-file `video_archiver.py`: scan, check, status.
