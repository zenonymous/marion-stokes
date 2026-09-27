"""The subcommands: scan, check, status, retry, export.

Each ``run_*`` function takes the parsed CLI namespace and a logger and
returns the number of non-fatal errors (feeds that failed, crashes on single
videos). Fatal problems (missing feeds file, unusable DB) raise.
"""

from __future__ import annotations

import logging
import os
import sys
import time

from . import db, export, notify, ytdlp
from .feeds import load_feeds, scan_feed
from .ytdlp import Verdict

# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------

def run_scan(args, logger: logging.Logger) -> int:
    conn = db.connect(args.db, logger)
    feeds = load_feeds(args.feeds)
    logger.info("Loaded %d feed(s) from %s", len(feeds), args.feeds)

    new = known = deferred = failed = errors = 0
    throttled = False
    seen: set[tuple[str, str]] = set()

    for feed_url in feeds:
        try:
            found = scan_feed(feed_url, logger)
        except Exception as exc:  # noqa: BLE001 - one bad feed must not stop the run
            logger.error("  Feed failed: %s", exc)
            errors += 1
            continue

        for fv in found:
            ref = fv.ref
            if ref.key in seen:
                continue
            seen.add(ref.key)
            if db.video_exists(conn, ref.platform, ref.video_id):
                logger.debug("  Already archived: %s", ref.url)
                known += 1
                continue
            now = db.utcnow()
            if throttled or db.failure_blocks_attempt(conn, ref.platform, ref.video_id, now):
                logger.debug("  Deferred: %s", ref.url)
                deferred += 1
                continue
            if args.max_downloads and new >= args.max_downloads:
                deferred += 1
                continue

            try:
                res = ytdlp.download(
                    ref.fetch_url, args.download_dir, logger,
                    subtitles=args.subs, thumbnails=not args.no_thumbnails,
                )
                if res.ok:
                    _save_download(conn, fv, res.meta, logger)
                    new += 1
                    continue

                penalise = res.verdict not in (Verdict.UPCOMING, Verdict.THROTTLED)
                attempts, gave_up = db.record_failure(
                    conn, ref, feed_source=fv.feed_source, entry_title=fv.entry_title,
                    error=res.error, now=now, penalise=penalise, max_attempts=args.max_attempts,
                )
                failed += 1
                if res.verdict is Verdict.THROTTLED:
                    throttled = True
                    logger.warning("  Rate-limited by the site; no more downloads this run. %s", res.error[:300])
                elif res.verdict is Verdict.UPCOMING:
                    logger.info("  Not downloadable yet (upcoming or live): %s", ref.url)
                elif gave_up:
                    logger.warning("  Giving up on %s after %d attempts: %s", ref.url, attempts, res.error[:300])
                else:
                    logger.warning("  Download failed (attempt %d/%d) for %s: %s",
                                   attempts, args.max_attempts, ref.url, res.error[:300])
            except Exception:  # noqa: BLE001
                logger.exception("  Unexpected error while handling %s", ref.url)
                errors += 1

    logger.info(
        "Scan complete. New: %d | Already archived: %d | Failed: %d | Deferred: %d | Errors: %d",
        new, known, failed, deferred, errors,
    )
    conn.close()
    return errors


def _save_download(conn, fv, meta: dict, logger: logging.Logger) -> None:
    ref = fv.ref
    fields = ytdlp.extract_record_fields(meta)
    now = db.utcnow()
    record = {
        **fields,
        "url": ref.url,
        "fetch_url": ref.fetch_url,
        "title": fields["title"] or fv.entry_title,
        "platform": ref.platform,
        "video_id": ref.video_id,
        "feed_source": fv.feed_source,
        "first_seen": now,
        "last_checked": now,
    }
    db.insert_video(conn, record)
    db.clear_failure(conn, ref.platform, ref.video_id)
    logger.info("  ✓ Saved: %s — %s", record["title"], ref.url)


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------

_STILL_EXISTS = {
    Verdict.AVAILABLE: db.AVAILABLE,
    Verdict.GEO_BLOCKED: db.GEO_BLOCKED,
    Verdict.RESTRICTED: db.RESTRICTED,
}
_DEFINITELY_GONE = {Verdict.REMOVED: db.REMOVED, Verdict.PRIVATE: db.PRIVATE}
MAX_THROTTLED_IN_A_ROW = 3


def run_check(args, logger: logging.Logger) -> int:
    conn = db.connect(args.db, logger)
    rows = db.videos_to_check(conn, args.check_limit)
    logger.info("Checking availability of %d video(s)…", len(rows))

    still_up = unknown = 0
    newly_gone: list[tuple] = []
    restored: list = []
    throttled_streak = 0

    for i, row in enumerate(rows):
        if i and args.check_delay:
            time.sleep(args.check_delay)
        url = row["fetch_url"] or row["url"]
        verdict, err = ytdlp.probe(url)
        now = db.utcnow()
        was_gone = not row["is_available"]

        if verdict is Verdict.THROTTLED:
            throttled_streak += 1
            logger.warning("  Rate-limited while checking %s: %s", url, err[:200])
            if throttled_streak >= MAX_THROTTLED_IN_A_ROW:
                logger.error("  Rate-limited %d times in a row; stopping this check run.", throttled_streak)
                break
            continue
        throttled_streak = 0

        if verdict in _STILL_EXISTS:
            state = _STILL_EXISTS[verdict]
            db.set_availability(conn, row["id"], state, now=now)
            if was_gone:
                logger.info("  ↑ Restored: %s — %s", row["title"], url)
                restored.append(row)
            else:
                still_up += 1
                if state != db.AVAILABLE and row["availability"] != state:
                    logger.info("  Online but %s: %s — %s", state, row["title"], url)
        elif verdict in _DEFINITELY_GONE:
            state = _DEFINITELY_GONE[verdict]
            db.set_availability(
                conn, row["id"], state, now=now,
                deleted_date=row["deleted_date"] if was_gone else None,
            )
            if not was_gone:
                logger.info("  ✗ Gone (%s): %s — %s", state, row["title"], url)
                newly_gone.append((row, state, err))
        elif verdict is Verdict.GONE_WEAK and not was_gone:
            strikes, since = db.add_gone_strike(conn, row["id"], now)
            if strikes >= args.confirm_checks:
                db.set_availability(conn, row["id"], db.UNAVAILABLE, now=now, deleted_date=since)
                logger.info("  ✗ Gone (unavailable, confirmed %dx): %s — %s", strikes, row["title"], url)
                newly_gone.append((row, db.UNAVAILABLE, err))
            else:
                logger.info("  ? Possibly gone (%d/%d): %s — %s",
                            strikes, args.confirm_checks, row["title"], err[:200])
        else:
            # UNKNOWN / UPCOMING, or GONE_WEAK for a video already marked gone.
            db.touch_checked(conn, row["id"], now)
            if verdict is not Verdict.GONE_WEAK:
                unknown += 1
                logger.warning("  Inconclusive check for %s, leaving state unchanged: %s", url, err[:300])

    logger.info(
        "Check complete. Available: %d | Newly gone: %d | Restored: %d | Inconclusive: %d",
        still_up, len(newly_gone), len(restored), unknown,
    )
    _notify_check(args, newly_gone, restored, logger)
    conn.close()
    return 0


def _notify_check(args, newly_gone, restored, logger) -> None:
    if not args.notify_url:
        return
    if newly_gone:
        lines = [f"[{state}] {row['title']}\n{row['url']}" for row, state, _ in newly_gone]
        notify.send(
            args.notify_url, f"{len(newly_gone)} archived video(s) went offline",
            "\n\n".join(lines), logger,
        )
    if restored:
        lines = [f"{row['title']}\n{row['url']}" for row in restored]
        notify.send(
            args.notify_url, f"{len(restored)} video(s) are back online",
            "\n\n".join(lines), logger,
        )


# ---------------------------------------------------------------------------
# status / retry / export
# ---------------------------------------------------------------------------

def run_status(args, logger: logging.Logger) -> int:
    conn = db.connect(args.db, logger)
    total = conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0]
    by_state = dict(conn.execute(
        "SELECT COALESCE(availability, 'unknown'), COUNT(*) FROM videos GROUP BY 1"
    ).fetchall())
    suspect = conn.execute(
        "SELECT COUNT(*) FROM videos WHERE gone_strikes > 0 AND is_available = 1"
    ).fetchone()[0]
    pending = conn.execute("SELECT COUNT(*) FROM failed_downloads WHERE gave_up = 0").fetchone()[0]
    gave_up = conn.execute("SELECT COUNT(*) FROM failed_downloads WHERE gave_up = 1").fetchone()[0]
    missing = sum(
        1 for (p,) in conn.execute("SELECT download_path FROM videos")
        if not (p and os.path.isfile(p))
    )
    gone = sum(by_state.get(s, 0) for s in db.GONE_STATES)

    bar = "═" * 60
    print(f"\n{bar}\n  marion-stokes archive: {args.db}\n{bar}")
    print(f"  Total videos archived : {total}")
    print(f"  Available online      : {by_state.get(db.AVAILABLE, 0)}")
    print(f"  Geo-blocked here      : {by_state.get(db.GEO_BLOCKED, 0)}")
    print(f"  Restricted (login/age): {by_state.get(db.RESTRICTED, 0)}")
    print(f"  Gone online           : {gone}"
          f"  (removed {by_state.get(db.REMOVED, 0)}, private {by_state.get(db.PRIVATE, 0)},"
          f" unavailable {by_state.get(db.UNAVAILABLE, 0)})")
    print(f"  Possibly gone (unconfirmed): {suspect}")
    print(f"  Local file missing    : {missing}")
    print(f"  Downloads pending retry: {pending} | given up: {gave_up}")
    print(bar)

    if gone:
        print("\n  Gone videos:")
        for row in conn.execute(
            "SELECT title, url, platform, availability, deleted_date FROM videos "
            "WHERE is_available = 0 ORDER BY deleted_date DESC"
        ):
            print(f"    [{row['platform']}] {row['title']}")
            print(f"      URL: {row['url']}")
            print(f"      Gone since: {row['deleted_date']} ({row['availability']})")
    if gave_up:
        print("\n  Downloads given up on (run `retry` to try again):")
        for row in conn.execute(
            "SELECT url, entry_title, attempts, last_error FROM failed_downloads WHERE gave_up = 1"
        ):
            print(f"    {row['entry_title'] or row['url']}")
            print(f"      URL: {row['url']} ({row['attempts']} attempts)")
            print(f"      Last error: {(row['last_error'] or '')[:160]}")
    print()
    conn.close()
    return 0


def run_retry(args, logger: logging.Logger) -> int:
    conn = db.connect(args.db, logger)
    n = db.reset_failures(conn)
    logger.info("Reset %d failed download(s); they will be retried on the next scan.", n)
    conn.close()
    return 0


def run_export(args, logger: logging.Logger) -> int:
    conn = db.connect(args.db, logger)
    if args.format == "csv":
        text = export.to_csv(conn, deleted_only=args.deleted_only)
    else:
        text = export.to_html(conn, deleted_only=args.deleted_only, output_path=args.output)
    conn.close()
    if args.output:
        with open(args.output, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        logger.info("Wrote %s", args.output)
    else:
        sys.stdout.write(text)
    return 0
