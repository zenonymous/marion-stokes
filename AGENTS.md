# AGENTS.md — guide for AI coding agents

Read this first. It is the shortest path to being productive in this repo.
Deeper material: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) (how it works) and
[`docs/KNOWN_ISSUES.md`](docs/KNOWN_ISSUES.md) (verified bugs and limitations).

## What this project is

**marion-stokes** is a small personal *video preservation* tool, named after
[Marion Stokes](https://en.wikipedia.org/wiki/Marion_Stokes), who taped TV news
around the clock for decades so it wouldn't be lost.

It watches RSS/Atom feeds, finds YouTube and Vimeo links in them, downloads
each new video at the best available quality with `yt-dlp`, records it in
SQLite, and later re-checks whether each video is **still online**. The key
output is a list of videos that have been **deleted or made private upstream**
but are still kept locally.

## Repo layout

| Path                | What it is                                                         |
|---------------------|--------------------------------------------------------------------|
| `video_archiver.py` | The whole application: one script, standard library + `feedparser` |
| `feeds.txt`         | User config: one feed URL per line, `#` comments                   |
| `cron_run.sh`       | Cron wrapper: `cd` to the repo, run `scan` then `check`            |
| `requirements.txt`  | `feedparser>=6.0`, `yt-dlp>=2024.0`                                 |
| `README.md`         | End-user documentation                                             |

Created at runtime in the working directory (not in git, and there is no `.gitignore` yet):
`videos.db`, `downloads/<extractor>/<id> - <title>.mkv` (+ `.info.json`), `video_archiver.log`.

## Requirements

- **Python 3.11+.** The code uses `datetime.UTC` (3.11) and `X | None` type hints (3.10).
- `yt-dlp` **on `PATH` as a command.** The script calls the executable with
  `subprocess`; it never imports the Python module.
- `ffmpeg` on `PATH`, to merge the separate best video and best audio streams into MKV.

## Commands

```bash
pip install -r requirements.txt
python3 video_archiver.py scan     # read feeds, download new videos, insert rows
python3 video_archiver.py check    # re-probe every row, flip is_available
python3 video_archiver.py status   # print counts + list of deleted videos
# Global flags: --feeds FILE --db FILE --download-dir DIR --log FILE -v
```

There is **no test suite, linter config, or CI** yet. To check a change, at minimum:

```bash
python3 -m py_compile video_archiver.py
# Pure functions can be exercised without network or yt-dlp:
python3 -c "import video_archiver as v; print(v.find_video_urls('https://youtu.be/dQw4w9WgXcQ'))"
```

Use a throwaway `--db`, `--download-dir`, and `--log` when you test end to end,
so you don't touch a real archive.

## Rules

1. **Protect the archive.** `videos.db` and `downloads/` may be irreplaceable, because
   the videos can be gone upstream, which is the point of the tool. Never delete,
   move, or rewrite them, and don't write migrations that drop data. Schema changes
   must be additive (`ALTER TABLE ... ADD COLUMN`) because `init_db` only runs
   `CREATE TABLE IF NOT EXISTS`.
2. **Don't mark a video deleted unless you're sure.** In `check_availability`,
   an unclear yt-dlp error counts as *available* on purpose. Keep that bias:
   wrongly reporting a deletion is worse than missing one.
3. **Keep one row per video.** `url` is always the *canonical* URL
   (`https://www.youtube.com/watch?v=ID` or `https://vimeo.com/ID`) and is `UNIQUE`.
   New URL forms must pass through `canonical_url()` so they dedupe against existing rows.
4. **Keep dependencies small.** Standard library, `feedparser`, and the `yt-dlp` CLI.
   Ask before adding more.
5. Match the existing style: section banners (`# ----`), small functions,
   `logger` passed in explicitly, `%`-style logging arguments, type hints on signatures.
6. When behavior or flags change, update `README.md` (users) and these docs (agents)
   in the same change.

## Where to change things

| Goal                                  | Where                                                               |
|---------------------------------------|---------------------------------------------------------------------|
| Detect a new URL shape or host        | `VIDEO_PATTERNS`, `detect_platform`, `extract_video_id`, `canonical_url` |
| Add a platform                        | the above, plus the `CHECK(platform IN ...)` constraint in `DB_SCHEMA` (a table rebuild for existing DBs) |
| Change download format or filenames   | `download_video` (`cmd`, `outtmpl`)                                 |
| Change the deletion heuristics        | `check_availability` (`unavailable_signals`)                        |
| Add a CLI subcommand                  | `main()` choices, plus a new `run_<name>(args, logger)`             |
| Store a new field                     | `DB_SCHEMA`, `insert_video`, the `record` dict in `run_scan`, README schema table |

## Traps to know before editing

- The `url` and `canonical_url` columns always hold the same value.
- Failed downloads are **not** recorded, so they are retried on every `scan`, indefinitely.
- `file_size_bytes` is yt-dlp's *estimate*, not the size on disk.
- `check` probes every row one after another, with no throttling. Its run time grows with the archive.
- `main()` requires yt-dlp even for `status`.

The full list, with reproductions, is in [`docs/KNOWN_ISSUES.md`](docs/KNOWN_ISSUES.md).
