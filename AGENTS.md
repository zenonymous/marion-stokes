# AGENTS.md — guide for AI coding agents

Read this first. It is the shortest path to being productive in this repo.
Deeper material: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) (how it works),
[`docs/KNOWN_ISSUES.md`](docs/KNOWN_ISSUES.md) (open limitations), and
[`CHANGELOG.md`](CHANGELOG.md).

## What this project is

**marion-stokes** is a personal *video preservation* tool, named after
[Marion Stokes](https://en.wikipedia.org/wiki/Marion_Stokes), who taped TV news
around the clock for decades so it wouldn't be lost. The owner runs it with cron
on a **home server**.

It watches RSS/Atom feeds, finds YouTube and Vimeo links in them, downloads each
new video at the best available quality with `yt-dlp`, and records it in SQLite.
It then keeps re-checking whether each video is **still online**. The key
output is the list of videos that have been **removed or made private
upstream** but are kept locally.

## Repo layout

| Path | What it is |
|---|---|
| `marion_stokes/detect.py` | Regexes that turn text into `VideoRef(platform, video_id, url, fetch_url)` |
| `marion_stokes/feeds.py` | `load_feeds`, `scan_feed` (feedparser with `sanitize_html=False`) |
| `marion_stokes/ytdlp.py` | Wrapper around the yt-dlp CLI: `download`, `probe`, `classify_error` → `Verdict` |
| `marion_stokes/db.py` | SQLite schema **migrations** (`PRAGMA user_version`) and all queries |
| `marion_stokes/workflows.py` | `run_scan`, `run_check`, `run_status`, `run_retry`, `run_export` |
| `marion_stokes/cli.py` | argparse subcommands, logging, run lock, exit codes |
| `marion_stokes/export.py`, `notify.py` | CSV/HTML export; ntfy-style POST notifications |
| `video_archiver.py` | Thin shim so `python3 video_archiver.py <cmd>` keeps working |
| `cron_run.sh` | Runs `scan` then `check`, uses `./.venv` if present |
| `tests/` | pytest suite. yt-dlp is faked, except one test that uses the real binary on a local HTTP server |
| `feeds.txt` | The user's config: one feed URL per line, `#` comments |

Created at runtime (and git-ignored): `videos.db`, `videos.db.lock`, `videos.db.bak-*`,
`downloads/`, `*.log`.

## Commands

```bash
pip install -r requirements.txt -r requirements-dev.txt
ruff check .           # lint (config in pyproject.toml)
pytest -q              # full suite, a few seconds, no network needed
python3 video_archiver.py {scan,check,status,retry,export} [options]
```

CI (`.github/workflows/ci.yml`) runs ruff and pytest on Python 3.11–3.13. Run both
before you push.

## Rules

1. **Protect the archive.** `videos.db` and `downloads/` may be irreplaceable,
   because the originals may already be gone upstream. Never delete, move, or
   rewrite user data. Schema changes go in a **new migration function** appended
   to `db.MIGRATIONS`. Don't edit old migrations. Changes must be additive.
   `migrate()` writes a backup before upgrading an existing DB. Keep it that way.
2. **Don't mark a video gone unless you're sure.** Flagging a live video as deleted
   is worse than missing a deletion. Anything not clearly recognised maps to
   `Verdict.UNKNOWN`, which never changes state. Generic "gone" wording
   (`GONE_WEAK`) needs `--confirm-checks` results in a row. When you add phrases to
   `ytdlp._RULES`, add a test case to `tests/test_classify.py` with the real
   yt-dlp message, and mind the rule order (see the comment there).
3. **Keep one row per video.** Identity is `(platform, video_id)`. `url` is the
   canonical URL. `fetch_url` is what yt-dlp gets (it keeps the unlisted-Vimeo hash).
4. **Keep dependencies small.** Standard library, `feedparser`, and the `yt-dlp`
   **executable**, called with `subprocess` so users can update it on its own.
   Ask before adding anything else.
5. **No network in tests.** Fake `ytdlp.download` / `ytdlp.probe` with
   monkeypatch, as `tests/test_workflows.py` does. Feeds can be local XML files.
6. Style: small functions, the `logger` passed in explicitly, `%`-style logging
   arguments, type hints, section banners (`# ----`). The ruff config is in `pyproject.toml`.
7. When behavior or options change, update `README.md`, these docs, and
   `CHANGELOG.md` in the same change.

## Where to change things

| Goal | Where |
|---|---|
| Recognise a new URL form | `detect.py` patterns, plus a case in `tests/test_detect.py` |
| Add a platform | `detect.py`, the `platform` CHECK constraint (needs a table rebuild migration), `canonical_url` |
| Change download flags or file names | `ytdlp.build_download_cmd` |
| Change how check results are read | `ytdlp._RULES` and `workflows.run_check` |
| Add a column or table | New `_migrate_vN` in `db.py`, then `insert_video`, `workflows._save_download`, the README schema, `export.COLUMNS` |
| Add a subcommand | `workflows.run_<name>`, `cli.COMMANDS`, `build_parser`, `NEEDS_LOCK`/`NEEDS_YTDLP` |

## Traps

- CLI options go **after** the subcommand. `--feeds` and `--download-dir` are
  accepted by every command on purpose, because `cron_run.sh` passes the same
  arguments to `scan` and `check`.
- `db.connect` opens in **autocommit** mode (`isolation_level=None`). Migrations run
  in explicit `BEGIN`/`COMMIT` blocks.
- The download command uses `--print after_move:%()j` with `--no-simulate`. That
  prints the final info dict with the real `filepath`. If it prints nothing and
  exits 0, the `!is_live` match filter skipped the video, and it is treated as
  "upcoming".
- yt-dlp warnings are ignored when classifying. Only `ERROR:` lines count.
- Only English error strings are matched. yt-dlp output is English regardless of locale.
