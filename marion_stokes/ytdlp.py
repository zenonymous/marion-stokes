"""Thin wrapper around the ``yt-dlp`` command-line tool.

The executable is used (not the Python module) so that yt-dlp can be updated
independently, e.g. ``yt-dlp -U`` or ``pip install -U yt-dlp``.
"""

from __future__ import annotations

import enum
import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass

YTDLP = "yt-dlp"


class Verdict(enum.Enum):
    AVAILABLE = "available"
    THROTTLED = "throttled"      # rate-limited / bot check: stop hammering, decide nothing
    UPCOMING = "upcoming"        # premiere or live stream that hasn't finished
    GEO_BLOCKED = "geo_blocked"
    RESTRICTED = "restricted"    # members-only, age-gated, login required
    PRIVATE = "private"
    REMOVED = "removed"          # definitive removal message
    GONE_WEAK = "gone_weak"      # looks gone, but the message is generic
    UNKNOWN = "unknown"          # network errors, format errors, anything else


# Checked in order; the first rule with a matching phrase wins. Messages are
# lower-cased before matching. Order matters: YouTube prefixes geo-blocks and
# rate limits with "Video unavailable.", so those rules come before GONE_WEAK.
# Phrases come from yt-dlp's extractors and YouTube/Vimeo error screens.
_RULES: list[tuple[Verdict, tuple[str, ...]]] = [
    (Verdict.THROTTLED, (
        "not a bot", "captcha", "rate-limit", "rate limit", "try again later",
        "http error 429", "too many requests", "tls fingerprint", "ip may be blocked",
    )),
    (Verdict.GEO_BLOCKED, (
        "not made this video available in your country", "not available from your location",
        "geo restriction", "geo-restrict", "in your country",
        "authentication may be needed due to your location",
    )),
    (Verdict.UPCOMING, (
        "live event will begin", "premieres in", "premiere will begin",
        "this live event has not started", "is_upcoming",
    )),
    (Verdict.RESTRICTED, (
        "join this channel", "members-only", "members only", "confirm your age",
        "age-restricted", "inappropriate for some users", "requires payment",
    )),
    (Verdict.PRIVATE, (
        "private video", "this video is private", "video is private",
    )),
    (Verdict.REMOVED, (
        "removed by the uploader", "has been terminated", "account associated with this video",
        "removed for violating", "violating youtube's", "violates youtube's",
        "due to a copyright claim", "copyright infringement", "this video has been removed",
        "this video has been deleted", "this video no longer exists",
    )),
    (Verdict.GONE_WEAK, (
        "video unavailable", "this video is unavailable", "this video is no longer available",
        "http error 404", "http error 410", "does not exist",
    )),
]


def classify_error(message: str) -> Verdict:
    """Map yt-dlp's stderr to a verdict. Unrecognised errors are UNKNOWN, never gone."""
    text = message.lower()
    for verdict, phrases in _RULES:
        if any(p in text for p in phrases):
            return verdict
    return Verdict.UNKNOWN


def _error_lines(stderr: str) -> str:
    """Only ``ERROR:`` lines: warnings often mention unrelated words like 'unavailable'."""
    errors = [ln for ln in stderr.splitlines() if ln.startswith("ERROR:")]
    return "\n".join(errors) if errors else stderr


def available() -> bool:
    return shutil.which(YTDLP) is not None


@dataclass
class DownloadResult:
    ok: bool
    verdict: Verdict | None = None   # set on failure
    error: str = ""
    meta: dict | None = None         # set on success


def build_download_cmd(url: str, download_dir: str, *, subtitles: bool, thumbnails: bool) -> list[str]:
    outtmpl = os.path.join(download_dir, "%(extractor)s/%(id)s - %(title)s.%(ext)s")
    cmd = [
        YTDLP,
        "--no-playlist",
        "-f", "bestvideo+bestaudio/best",
        "--merge-output-format", "mkv",
        "--write-info-json",
        # Skip streams that are live right now; they'll be retried after they end.
        "--match-filters", "!is_live",
        "--output", outtmpl,
        "--no-progress",
        # Print the final info dict (with the real "filepath") after all post-processing.
        "--no-simulate",
        "--print", "after_move:%()j",
    ]
    if thumbnails:
        cmd.append("--write-thumbnail")
    if subtitles:
        cmd += ["--write-subs", "--sub-langs", "all,-live_chat"]
    cmd.append(url)
    return cmd


def download(
    url: str,
    download_dir: str,
    logger: logging.Logger,
    *,
    subtitles: bool = False,
    thumbnails: bool = True,
    timeout: int = 3600,
) -> DownloadResult:
    os.makedirs(download_dir, exist_ok=True)
    cmd = build_download_cmd(url, download_dir, subtitles=subtitles, thumbnails=thumbnails)
    logger.info("  Downloading: %s", url)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return DownloadResult(False, Verdict.UNKNOWN, f"timed out after {timeout}s")

    if result.returncode != 0:
        err = _error_lines(result.stderr).strip()
        return DownloadResult(False, classify_error(err), err[:2000])

    meta = None
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                meta = json.loads(line)
            except json.JSONDecodeError:
                continue
    if meta is None:
        # Exit 0 with nothing printed: the match filter skipped a live stream.
        return DownloadResult(False, Verdict.UPCOMING, "skipped (live now or not yet available)")
    return DownloadResult(True, meta=meta)


def extract_record_fields(meta: dict) -> dict:
    """Pick the columns stored in ``videos`` from yt-dlp's info dict."""
    path = meta.get("filepath") or meta.get("_filename") or meta.get("filename") or ""
    size = os.path.getsize(path) if path and os.path.isfile(path) else (
        meta.get("filesize") or meta.get("filesize_approx")
    )
    duration = meta.get("duration")
    return {
        "title": meta.get("title") or None,
        "download_path": path,
        "file_size_bytes": size,
        "duration": str(int(duration)) if isinstance(duration, (int, float)) else None,
        "uploader": meta.get("uploader") or "",
        "resolution": meta.get("resolution") or "",
        "metadata_json": json.dumps(
            {k: meta[k] for k in (
                "id", "title", "description", "uploader", "upload_date",
                "duration", "view_count", "like_count", "resolution",
                "fps", "vcodec", "acodec", "ext", "format",
            ) if k in meta},
            indent=2,
        ),
    }


def probe(url: str, *, timeout: int = 120) -> tuple[Verdict, str]:
    """Check whether a video is still online without downloading it."""
    cmd = [YTDLP, "--simulate", "--no-playlist", "--print", "id", url]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return Verdict.UNKNOWN, f"timed out after {timeout}s"
    if result.returncode == 0:
        return Verdict.AVAILABLE, ""
    err = _error_lines(result.stderr).strip()
    return classify_error(err), err[:2000]
