"""Scan/check behaviour with yt-dlp replaced by fakes and feeds read from local files."""

import pytest

from marion_stokes import db, workflows, ytdlp
from marion_stokes.ytdlp import DownloadResult, Verdict

RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>t</title>
{items}
</channel></rss>"""
ITEM = "<item><title>{title}</title><link>{link}</link><description>{desc}</description></item>"


def write_feed(tmp_path, name, items):
    path = tmp_path / name
    path.write_text(RSS.format(items="\n".join(ITEM.format(**i) for i in items)))
    return str(path)


@pytest.fixture
def feeds(tmp_path):
    good = write_feed(tmp_path, "good.xml", [
        {"title": "A", "link": "https://www.youtube.com/watch?v=AAAAAAAAAAA", "desc": ""},
        {"title": "B", "link": "https://example.com/post",
         "desc": '&lt;iframe src="https://player.vimeo.com/video/42?h=abcdef1234"&gt;'},
    ])
    (tmp_path / "feeds.txt").write_text(f"# comment\n{tmp_path / 'missing.xml'}\n\n{good}\n")
    return good


class FakeDownloader:
    def __init__(self, tmp_path, outcomes=None):
        self.tmp_path = tmp_path
        self.outcomes = outcomes or {}
        self.calls = []

    def __call__(self, url, download_dir, logger, **kw):
        self.calls.append(url)
        outcome = self.outcomes.get(url)
        if isinstance(outcome, DownloadResult):
            return outcome
        f = self.tmp_path / f"{len(self.calls)}.mkv"
        f.write_bytes(b"v" * 100)
        return DownloadResult(True, meta={"title": f"T{len(self.calls)}", "filepath": str(f), "duration": 12.0})


def rows(tmp_path):
    conn = db.connect(str(tmp_path / "videos.db"), None)
    return {r["video_id"]: dict(r) for r in conn.execute("SELECT * FROM videos")}


def test_scan_downloads_new_videos_and_survives_bad_feed(tmp_path, feeds, make_args, logger, monkeypatch):
    fake = FakeDownloader(tmp_path)
    monkeypatch.setattr(ytdlp, "download", fake)

    errors = workflows.run_scan(make_args("scan"), logger)

    assert errors == 1  # the missing feed, but the good feed still ran
    assert fake.calls == ["https://www.youtube.com/watch?v=AAAAAAAAAAA", "https://vimeo.com/42/abcdef1234"]
    r = rows(tmp_path)
    assert r["42"]["url"] == "https://vimeo.com/42"
    assert r["42"]["fetch_url"] == "https://vimeo.com/42/abcdef1234"
    assert r["AAAAAAAAAAA"]["file_size_bytes"] == 100
    assert r["AAAAAAAAAAA"]["duration"] == "12"

    # Second scan: nothing new to download.
    workflows.run_scan(make_args("scan"), logger)
    assert len(fake.calls) == 2


def test_scan_records_failures_with_backoff(tmp_path, feeds, make_args, logger, monkeypatch):
    yt = "https://www.youtube.com/watch?v=AAAAAAAAAAA"
    fake = FakeDownloader(tmp_path, {yt: DownloadResult(False, Verdict.UNKNOWN, "ERROR: boom")})
    monkeypatch.setattr(ytdlp, "download", fake)

    workflows.run_scan(make_args("scan"), logger)
    workflows.run_scan(make_args("scan"), logger)  # within backoff window: not retried

    assert fake.calls.count(yt) == 1
    conn = db.connect(str(tmp_path / "videos.db"), logger)
    f = conn.execute("SELECT * FROM failed_downloads").fetchone()
    assert (f["video_id"], f["attempts"], f["gave_up"]) == ("AAAAAAAAAAA", 1, 0)

    # `retry` clears the backoff; a successful download removes the failure row.
    workflows.run_retry(make_args("retry"), logger)
    fake.outcomes.clear()
    workflows.run_scan(make_args("scan"), logger)
    assert fake.calls.count(yt) == 2
    assert conn.execute("SELECT COUNT(*) FROM failed_downloads").fetchone()[0] == 0


def test_upcoming_is_retried_every_scan(tmp_path, feeds, make_args, logger, monkeypatch):
    yt = "https://www.youtube.com/watch?v=AAAAAAAAAAA"
    fake = FakeDownloader(tmp_path, {yt: DownloadResult(False, Verdict.UPCOMING, "Premieres in 2 hours")})
    monkeypatch.setattr(ytdlp, "download", fake)
    workflows.run_scan(make_args("scan"), logger)
    workflows.run_scan(make_args("scan"), logger)
    assert fake.calls.count(yt) == 2


def test_throttle_stops_downloads_for_the_run(tmp_path, feeds, make_args, logger, monkeypatch):
    yt = "https://www.youtube.com/watch?v=AAAAAAAAAAA"
    fake = FakeDownloader(tmp_path, {yt: DownloadResult(False, Verdict.THROTTLED, "not a bot")})
    monkeypatch.setattr(ytdlp, "download", fake)
    workflows.run_scan(make_args("scan"), logger)
    assert fake.calls == [yt]


def test_missing_feeds_file_is_fatal(tmp_path, make_args, logger):
    with pytest.raises(FileNotFoundError):
        workflows.run_scan(make_args("scan"), logger)


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------

@pytest.fixture
def archived(tmp_path, feeds, make_args, logger, monkeypatch):
    monkeypatch.setattr(ytdlp, "download", FakeDownloader(tmp_path))
    workflows.run_scan(make_args("scan"), logger)
    return rows(tmp_path)


def fake_probe(monkeypatch, results):
    calls = []

    def probe(url, **kw):
        calls.append(url)
        return results[url] if isinstance(results, dict) else results

    monkeypatch.setattr(ytdlp, "probe", probe)
    return calls


def check(make_args, logger, *extra):
    return workflows.run_check(make_args("check", "--check-delay", "0", *extra), logger)


def test_definitive_removal_is_immediate_and_restore_works(tmp_path, archived, make_args, logger, monkeypatch):
    fake_probe(monkeypatch, (Verdict.REMOVED, "removed by the uploader"))
    check(make_args, logger)
    r = rows(tmp_path)["AAAAAAAAAAA"]
    assert (r["is_available"], r["availability"]) == (0, db.REMOVED)
    first_deleted = r["deleted_date"]

    check(make_args, logger)  # still gone: deleted_date keeps the first detection
    assert rows(tmp_path)["AAAAAAAAAAA"]["deleted_date"] == first_deleted

    fake_probe(monkeypatch, (Verdict.AVAILABLE, ""))
    check(make_args, logger)
    r = rows(tmp_path)["AAAAAAAAAAA"]
    assert (r["is_available"], r["availability"], r["deleted_date"]) == (1, db.AVAILABLE, None)


def test_generic_unavailable_needs_confirmation(tmp_path, archived, make_args, logger, monkeypatch):
    fake_probe(monkeypatch, (Verdict.GONE_WEAK, "Video unavailable"))
    check(make_args, logger)
    r = rows(tmp_path)["AAAAAAAAAAA"]
    assert (r["is_available"], r["gone_strikes"]) == (1, 1)
    since = r["gone_since"]

    check(make_args, logger)
    r = rows(tmp_path)["AAAAAAAAAAA"]
    assert (r["is_available"], r["availability"], r["deleted_date"]) == (0, db.UNAVAILABLE, since)


def test_strikes_reset_when_seen_again(tmp_path, archived, make_args, logger, monkeypatch):
    fake_probe(monkeypatch, (Verdict.GONE_WEAK, "Video unavailable"))
    check(make_args, logger)
    fake_probe(monkeypatch, (Verdict.AVAILABLE, ""))
    check(make_args, logger)
    fake_probe(monkeypatch, (Verdict.GONE_WEAK, "Video unavailable"))
    check(make_args, logger)
    assert rows(tmp_path)["AAAAAAAAAAA"]["is_available"] == 1


@pytest.mark.parametrize("verdict", [Verdict.GEO_BLOCKED, Verdict.RESTRICTED, Verdict.UNKNOWN])
def test_ambiguous_results_never_mark_gone(tmp_path, archived, make_args, logger, monkeypatch, verdict):
    fake_probe(monkeypatch, (verdict, "whatever"))
    for _ in range(3):
        check(make_args, logger)
    assert all(r["is_available"] == 1 for r in rows(tmp_path).values())


def test_check_uses_fetch_url_and_limit_rotates(tmp_path, archived, make_args, logger, monkeypatch):
    calls = fake_probe(monkeypatch, (Verdict.AVAILABLE, ""))
    check(make_args, logger, "--check-limit", "1")
    check(make_args, logger, "--check-limit", "1")
    assert sorted(calls) == ["https://vimeo.com/42/abcdef1234", "https://www.youtube.com/watch?v=AAAAAAAAAAA"]


def test_throttling_aborts_check(tmp_path, archived, make_args, logger, monkeypatch):
    calls = fake_probe(monkeypatch, (Verdict.THROTTLED, "429"))
    monkeypatch.setattr(workflows, "MAX_THROTTLED_IN_A_ROW", 1)
    check(make_args, logger)
    assert len(calls) == 1
    assert all(r["is_available"] == 1 for r in rows(tmp_path).values())


def test_notification_sent_for_newly_gone(tmp_path, archived, make_args, logger, monkeypatch):
    sent = []
    monkeypatch.setattr(workflows.notify, "send", lambda url, title, body, lg: sent.append((url, title, body)))
    fake_probe(monkeypatch, (Verdict.PRIVATE, "Private video"))
    check(make_args, logger, "--notify-url", "https://ntfy.example/topic")
    assert len(sent) == 1
    assert sent[0][1] == "2 archived video(s) went offline"
    check(make_args, logger, "--notify-url", "https://ntfy.example/topic")
    assert len(sent) == 1  # nothing new


def test_status_and_export(tmp_path, archived, make_args, logger, monkeypatch, capsys):
    fake_probe(monkeypatch, (Verdict.REMOVED, "removed by the uploader"))
    check(make_args, logger)
    workflows.run_status(make_args("status"), logger)
    out = capsys.readouterr().out
    assert "Gone online           : 2" in out

    workflows.run_export(make_args("export", "--format", "csv", "--deleted-only"), logger)
    csv_out = capsys.readouterr().out
    assert csv_out.splitlines()[0].startswith("platform,video_id,title")
    assert len(csv_out.splitlines()) == 3

    html_path = tmp_path / "out" / "index.html"
    html_path.parent.mkdir()
    workflows.run_export(make_args("export", "--format", "html", "-o", str(html_path)), logger)
    page = html_path.read_text()
    assert "2 no longer available online" in page
    assert 'href="../1.mkv"' in page
