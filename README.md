# marion-stokes

Watches RSS feeds, archives every YouTube and Vimeo video they link to, and tells you when those videos disappear online.

Named after [Marion Stokes](https://en.wikipedia.org/wiki/Marion_Stokes), who recorded television news around the clock for 35 years because she knew it would otherwise be lost.

## What it does

1. **`scan`** reads the feeds in `feeds.txt` and finds YouTube and Vimeo links in every entry, including `<iframe>` embeds in blog posts. It downloads each new video at the highest quality available, with its metadata and thumbnail, and records it in a local SQLite database. Failed downloads are retried later with increasing delays.
2. **`check`** re-checks archived videos to see if they are still online. A video can be:
   - **removed** or **private**: yt-dlp gave a definite message, so it is flagged right away.
   - **unavailable**: yt-dlp gave only a generic "video unavailable" or 404. It is flagged only after several checks in a row agree (2 by default).
   - **geo_blocked** or **restricted** (members-only, age-gated): the video still exists, so it is *not* flagged as deleted.

   Rate limits, network errors and other unclear errors never flag a video. A video that comes back online is restored.
3. **`status`** prints a summary: counts per state, local files that are missing, pending downloads, and the list of videos that are gone.
4. **`export`** writes the index as CSV or as a self-contained HTML page. `--deleted-only` exports only the videos that are gone.
5. **`retry`** makes the next scan try again on downloads that were given up on.

## Setup

Requires **Python 3.11+**, the `yt-dlp` command on your `PATH`, and `ffmpeg`, which merges the best video and audio streams.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt   # or: .venv/bin/pip install .
sudo apt install ffmpeg                     # Debian/Ubuntu (dnf on Fedora, brew on macOS)
```

yt-dlp needs updates often to keep working with YouTube: `.venv/bin/pip install -U yt-dlp`.

## Configuration

Edit `feeds.txt` and add one RSS or Atom feed URL per line. Lines starting with `#` and blank lines are ignored.

```text
# YouTube channel
https://www.youtube.com/feeds/videos.xml?channel_id=UC_x5XG1OV2P6uZZ5FSM9Ttw
# YouTube playlist
https://www.youtube.com/feeds/videos.xml?playlist_id=PLAYLIST_ID
# A blog that embeds videos
https://example.com/blog/feed
# Vimeo user feed
https://vimeo.com/someuser/videos/rss
```

To find a YouTube channel's ID, view the page source of the channel page and search for `channel_id`.

## Usage

```bash
python3 video_archiver.py scan      # read feeds, download new videos
python3 video_archiver.py check     # re-check archived videos
python3 video_archiver.py status    # summary
python3 video_archiver.py retry     # retry downloads that were given up on
python3 video_archiver.py export --format html -o gone.html --deleted-only
```

If you install it with `pip install .`, the same commands are also available as `marion-stokes <command>`, or as `python3 -m marion_stokes <command>`.

Options go **after** the command, for example `python3 video_archiver.py scan --feeds my.txt`.

| Option | Commands | Default | Description |
|---|---|---|---|
| `--db FILE` | all | `videos.db` | SQLite database |
| `--feeds FILE` | all | `feeds.txt` | Feed list (used by `scan`) |
| `--download-dir DIR` | all | `downloads/` | Where videos are saved (used by `scan`) |
| `--log FILE` | all | `video_archiver.log` | Log file (`""` turns it off) |
| `-v`, `--verbose` | all | off | Debug output on the console |
| `--notify-url URL` | all | env `MARION_STOKES_NOTIFY_URL` | Send a notification when videos go offline or come back (used by `check`) |
| `--max-downloads N` | scan | 0 (no limit) | Stop after N new downloads in a run |
| `--max-attempts N` | scan | 10 | Stop retrying a failing download after N attempts |
| `--subs` | scan | off | Also download subtitles in all languages |
| `--no-thumbnails` | scan | off | Don't save thumbnails |
| `--check-limit N` | check | 500 (env `MARION_STOKES_CHECK_LIMIT`) | Check at most N videos per run, least recently checked first. `0` checks all |
| `--check-delay S` | check | 3 | Seconds to wait between checks |
| `--confirm-checks N` | check | 2 | How many generic "unavailable" results in a row are needed before a video is flagged |
| `--format csv\|html`, `-o FILE`, `--deleted-only` | export | csv, stdout | Export options |

Exit codes: `0` ok, `1` fatal error, `2` finished but some feeds or videos had errors, `3` another run was already in progress.

### Notifications

`--notify-url` sends a plain-text POST, with the title in a `Title` header. That works as-is with [ntfy](https://ntfy.sh), either the hosted service or self-hosted: `--notify-url https://ntfy.sh/my-secret-topic`.

## Running it on a schedule (cron)

`cron_run.sh` runs `scan` and then `check`. `check` still runs if `scan` fails. If `./.venv` exists, the script uses it.

```bash
chmod +x cron_run.sh
crontab -e
# every 6 hours:
0 */6 * * * MARION_STOKES_NOTIFY_URL=https://ntfy.sh/my-topic /path/to/marion-stokes/cron_run.sh >> /path/to/marion-stokes/cron.log 2>&1
```

Only one run can use the database at a time. The lock file is `videos.db.lock`, so overlapping cron runs simply exit with code 3. Because of `--check-limit`, each run checks the 500 least recently checked videos. A large archive is therefore covered over several runs, without overloading YouTube.

## Where things are stored

- `downloads/<extractor>/<id> - <title>.mkv`, plus `.info.json` (the full yt-dlp metadata) and the thumbnail.
- `videos.db`, the SQLite index. The schema is upgraded automatically, and a backup (`videos.db.bak-v<N>-<timestamp>`) is written first.
- `video_archiver.log`.

None of these are committed to git (see `.gitignore`). **Back up `downloads/` and `videos.db`.** They may be the only copy left.

### Database

The `videos` table has one row per archived video:

| Column | Description |
|---|---|
| `url` | Canonical URL (`https://www.youtube.com/watch?v=ID` or `https://vimeo.com/ID`), unique |
| `fetch_url` | URL given to yt-dlp. Differs from `url` only for unlisted Vimeo videos, where it includes the privacy hash |
| `platform`, `video_id` | `youtube` or `vimeo`, and the platform ID (unique together) |
| `title`, `uploader`, `duration` (seconds), `resolution` | From yt-dlp |
| `feed_source` | The feed the video was found in |
| `download_path`, `file_size_bytes` | Local file and its real size on disk |
| `first_seen`, `last_checked` | ISO 8601 UTC timestamps |
| `availability` | `available`, `geo_blocked`, `restricted`, `removed`, `private`, or `unavailable` |
| `is_available` | `1` = still exists online, **`0` = gone** (removed, private, or unavailable) |
| `deleted_date` | When the video was first detected as gone |
| `gone_strikes`, `gone_since` | Generic "unavailable" results in a row that have not yet confirmed a deletion |
| `metadata_json` | A subset of the yt-dlp metadata |

`failed_downloads` holds videos that were found but could not be downloaded yet: attempts, next retry time, last error, and whether it was given up on.

```bash
sqlite3 videos.db "SELECT title, url, availability, deleted_date FROM videos WHERE is_available = 0;"
sqlite3 videos.db "SELECT platform, COUNT(*) FROM videos GROUP BY platform;"
sqlite3 videos.db "SELECT url, attempts, last_error FROM failed_downloads WHERE gave_up = 1;"
```

## Recognised links

- YouTube: `youtube.com/watch?v=`, `m.` and `music.` subdomains, `youtu.be/`, `/embed/`, `/v/`, `/shorts/`, `/live/`, `youtube-nocookie.com/embed/`
- Vimeo: `vimeo.com/<id>`, unlisted `vimeo.com/<id>/<hash>`, `player.vimeo.com/video/<id>?h=<hash>`, and `/channels/…/<id>`, `/groups/…/videos/<id>`, `/album/…/video/<id>`, `/showcase/…/video/<id>`

The scanner looks at each entry's link, title, summary, content, enclosures and `media:*` fields. It works with native YouTube and Vimeo feeds, blog feeds that embed videos, and any RSS 2.0 or Atom feed.

## Development

```bash
pip install -r requirements.txt -r requirements-dev.txt
ruff check . && pytest -q
```

See [`AGENTS.md`](AGENTS.md) (also loaded through `CLAUDE.md`), [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md), [`docs/KNOWN_ISSUES.md`](docs/KNOWN_ISSUES.md) and [`CHANGELOG.md`](CHANGELOG.md).
