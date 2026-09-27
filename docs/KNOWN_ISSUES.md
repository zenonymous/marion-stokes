# Known issues and limitations

Found by reading the code and exercising the pure functions (Python 3.11,
2026-09). None of these has been fixed yet. Remove an entry when it is fixed.

Severity: **high** means data is lost or wrong, **med** means videos are missed or
work is wasted, **low** means cosmetic or tidy-up.

## Correctness

| # | Sev  | Issue | Where |
|---|------|-------|-------|
| 1 | high | **Deletion false positives from loose stderr matching.** The substrings `"not available"`, `"removed"`, `"404"`, and `"copyright"` also appear in errors that are not deletions. One example is yt-dlp's `Requested format is not available`. Another is a geo-block (`Video unavailable. The uploader has not made this video available in your country`), which says nothing about whether the video still exists. These get flagged as deleted. | `check_availability` |
| 2 | med  | **Unlisted Vimeo videos can't be downloaded.** `player.vimeo.com/video/123?h=abc` is canonicalised to `vimeo.com/123`, which drops the `h` privacy hash that unlisted videos need. | `canonical_url` |
| 3 | med  | **URL forms that are missed** (checked): `m.youtube.com/watch`, `youtube-nocookie.com/embed`, `youtube.com/live/<id>`, `vimeo.com/channels/<name>/<id>`. | `VIDEO_PATTERNS` |
| 4 | med  | **Failed downloads aren't recorded.** They are retried on every scan with no backoff. Permanently failing items, such as members-only videos or removed videos, cost a yt-dlp call on every run. There is also no record of videos that were seen but never saved. | `run_scan` |
| 5 | med  | **Upcoming premieres or livestreams** in YouTube feeds fail the download until they air. That's fine because they're retried, but they add noise to the log on every run (see #4). | `download_video` |
| 6 | low  | **`file_size_bytes` is yt-dlp's estimate**, and is often empty for merged formats. It isn't the size on disk (`os.path.getsize`). | `download_video` |
| 7 | low  | **`last_checked` isn't updated** for videos that are still gone. | `run_check` |

## Robustness and operations

| # | Sev  | Issue | Where |
|---|------|-------|-------|
| 8  | med | **No run lock.** A `scan` that takes longer than the cron interval can overlap the next run. That can cause duplicate downloads or `database is locked`. | `cron_run.sh`, `main` |
| 9  | med | **`check` doesn't scale.** It probes every row one after another, with no delay, on every run. Large archives take a long time, and YouTube may rate-limit or bot-check the host. | `run_check` |
| 10 | med | **An exception anywhere aborts the whole run.** One example is a missing feeds file. Because of `set -e`, `check` is then skipped. | `run_scan`, `cron_run.sh` |
| 11 | low | `status` refuses to run without yt-dlp, even though it doesn't need it. | `main` |
| 12 | low | There's no `.gitignore`, so `videos.db`, `downloads/`, and `*.log` are easy to commit by accident. | repo |
| 13 | low | There are no tests, linting, or CI. | repo |
| 14 | low | The Python version isn't pinned anywhere. It needs 3.11+ for `datetime.UTC`. | `requirements.txt`, README |

## Redundancy and cleanup

- The `url` and `canonical_url` columns always hold the same value.
- `idx_videos_url` duplicates the automatic `UNIQUE` index.
- `--simulate` and `--skip-download` are both passed. Either one is enough.
- `duration` is stored as TEXT. If yt-dlp reports `duration: null`, the stored value is the literal string `"None"`.
