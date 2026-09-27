"""Command-line entry point."""

from __future__ import annotations

import argparse
import contextlib
import logging
import os
import sys

from . import __version__, ytdlp
from .workflows import run_check, run_export, run_retry, run_scan, run_status

DEFAULT_FEEDS_FILE = "feeds.txt"
DEFAULT_DB_FILE = "videos.db"
DEFAULT_DOWNLOAD_DIR = "downloads"
DEFAULT_LOG_FILE = "video_archiver.log"

# Exit codes
EXIT_OK = 0
EXIT_FATAL = 1
EXIT_PARTIAL = 2     # finished, but some feeds/videos errored
EXIT_LOCKED = 3      # another run holds the lock

COMMANDS = {
    "scan": run_scan,
    "check": run_check,
    "status": run_status,
    "retry": run_retry,
    "export": run_export,
}
NEEDS_YTDLP = {"scan", "check"}
NEEDS_LOCK = {"scan", "check", "retry"}


def setup_logging(log_file: str, verbose: bool = False) -> logging.Logger:
    logger = logging.getLogger("marion_stokes")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    if log_file:
        parent = os.path.dirname(log_file)
        if parent:
            os.makedirs(parent, exist_ok=True)
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        logger.addHandler(fh)

    ch = logging.StreamHandler()  # stderr, so `export` can write to stdout
    ch.setLevel(logging.DEBUG if verbose else logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    return logger


@contextlib.contextmanager
def run_lock(db_path: str):
    """Non-blocking exclusive lock next to the DB. Yields False if already held."""
    try:
        import fcntl
    except ImportError:  # not POSIX; no locking
        yield True
        return
    lock_path = f"{db_path}.lock"
    parent = os.path.dirname(lock_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(lock_path, "w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            fh.write(str(os.getpid()))
            fh.flush()
            yield True
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="marion-stokes",
        description="Monitor RSS feeds, archive YouTube/Vimeo videos, and track which ones disappear.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  %(prog)s scan                              # read feeds, download new videos
  %(prog)s check                             # re-check archived videos are still online
  %(prog)s status                            # print a summary
  %(prog)s retry                             # retry downloads that were given up on
  %(prog)s export --format html -o index.html --deleted-only
""",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", default=DEFAULT_DB_FILE, help="SQLite database path (default: %(default)s)")
    common.add_argument("--log", default=DEFAULT_LOG_FILE, help="log file path; '' disables (default: %(default)s)")
    # --feeds/--download-dir are accepted by every command so one argument list
    # can be passed to both `scan` and `check` (as cron_run.sh does).
    common.add_argument("--feeds", default=DEFAULT_FEEDS_FILE, help="feeds list file (default: %(default)s)")
    common.add_argument("--download-dir", default=DEFAULT_DOWNLOAD_DIR,
                        help="where videos are saved (default: %(default)s)")
    common.add_argument("-v", "--verbose", action="store_true", help="debug output on the console")
    common.add_argument("--notify-url", default=os.environ.get("MARION_STOKES_NOTIFY_URL"),
                        help="POST a notification here when videos go offline or come back "
                             "(e.g. an ntfy topic URL; env MARION_STOKES_NOTIFY_URL)")

    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    scan = sub.add_parser("scan", parents=[common], help="read feeds and download new videos")
    scan.add_argument("--max-downloads", type=int, default=0, help="stop after N new downloads per run (0 = no limit)")
    scan.add_argument("--max-attempts", type=int, default=10,
                      help="give up on a failing download after N attempts (default: %(default)s)")
    scan.add_argument("--subs", action="store_true", help="also download subtitles (all languages)")
    scan.add_argument("--no-thumbnails", action="store_true", help="don't save thumbnails")

    check = sub.add_parser("check", parents=[common], help="re-check whether archived videos are still online")
    check.add_argument("--check-limit", type=int, default=_env_int("MARION_STOKES_CHECK_LIMIT", 500),
                       help="check at most N videos per run, least recently checked first; 0 = all "
                            "(default: %(default)s)")
    check.add_argument("--check-delay", type=float, default=3.0,
                       help="seconds to wait between checks (default: %(default)s)")
    check.add_argument("--confirm-checks", type=int, default=2,
                       help="consecutive generic 'unavailable' results needed before marking a video "
                            "gone (default: %(default)s)")

    sub.add_parser("status", parents=[common], help="print a summary of the archive")
    sub.add_parser("retry", parents=[common], help="reset failed downloads so the next scan retries them")

    exp = sub.add_parser("export", parents=[common], help="export the archive index as CSV or HTML")
    exp.add_argument("--format", choices=["csv", "html"], default="csv")
    exp.add_argument("-o", "--output", help="output file (default: stdout)")
    exp.add_argument("--deleted-only", action="store_true", help="only videos that are gone online")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logger = setup_logging(args.log, args.verbose)

    if args.command in NEEDS_YTDLP and not ytdlp.available():
        logger.error("yt-dlp is not installed or not on PATH.")
        logger.error("  pip install -U yt-dlp   or see https://github.com/yt-dlp/yt-dlp#installation")
        return EXIT_FATAL

    handler = COMMANDS[args.command]
    lock = run_lock(args.db) if args.command in NEEDS_LOCK else contextlib.nullcontext(True)
    try:
        with lock as acquired:
            if not acquired:
                logger.warning("Another marion-stokes run is using %s; exiting.", args.db)
                return EXIT_LOCKED
            errors = handler(args, logger)
    except FileNotFoundError as exc:
        logger.error("%s", exc)
        return EXIT_FATAL
    except Exception:
        logger.exception("Fatal error during %s", args.command)
        return EXIT_FATAL
    return EXIT_PARTIAL if errors else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
