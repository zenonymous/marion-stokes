import functools
import http.server
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from marion_stokes import cli, ytdlp

ROOT = Path(__file__).resolve().parent.parent


def test_status_does_not_need_ytdlp(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ytdlp, "available", lambda: False)
    assert cli.main(["status", "--db", str(tmp_path / "v.db"), "--log", ""]) == cli.EXIT_OK
    assert "Total videos archived : 0" in capsys.readouterr().out


def test_scan_needs_ytdlp(tmp_path, monkeypatch):
    monkeypatch.setattr(ytdlp, "available", lambda: False)
    assert cli.main(["scan", "--db", str(tmp_path / "v.db"), "--log", ""]) == cli.EXIT_FATAL


def test_missing_feeds_file_exits_fatal(tmp_path, monkeypatch):
    monkeypatch.setattr(ytdlp, "available", lambda: True)
    rc = cli.main(["scan", "--db", str(tmp_path / "v.db"), "--log", "", "--feeds", str(tmp_path / "nope.txt")])
    assert rc == cli.EXIT_FATAL


def test_lock_prevents_overlapping_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(ytdlp, "available", lambda: True)
    db_path = str(tmp_path / "v.db")
    with cli.run_lock(db_path) as acquired:
        assert acquired
        assert cli.main(["retry", "--db", db_path, "--log", ""]) == cli.EXIT_LOCKED
    assert cli.main(["retry", "--db", db_path, "--log", ""]) == cli.EXIT_OK


def test_legacy_entry_point_runs(tmp_path):
    out = subprocess.run(
        [sys.executable, str(ROOT / "video_archiver.py"), "status", "--db", str(tmp_path / "v.db"), "--log", ""],
        capture_output=True, text=True, cwd=tmp_path, env={**os.environ, "PYTHONPATH": str(ROOT)},
    )
    assert out.returncode == 0, out.stderr
    assert "marion-stokes archive" in out.stdout


@pytest.fixture
def http_dir(tmp_path):
    served = tmp_path / "srv"
    served.mkdir()
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(served))
    handler.log_message = lambda *a: None
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield served, f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.mark.skipif(shutil.which("yt-dlp") is None, reason="yt-dlp not installed")
def test_real_ytdlp_download_and_probe(tmp_path, http_dir, logger):
    """End-to-end against the real yt-dlp binary using a local file (generic extractor)."""
    served, base = http_dir
    (served / "clip.mp4").write_bytes(os.urandom(50_000))

    res = ytdlp.download(f"{base}/clip.mp4", str(tmp_path / "dl"), logger)
    assert res.ok, res.error
    fields = ytdlp.extract_record_fields(res.meta)
    assert os.path.isfile(fields["download_path"])
    assert fields["file_size_bytes"] == 50_000
    assert fields["duration"] is None

    assert ytdlp.probe(f"{base}/clip.mp4")[0] is ytdlp.Verdict.AVAILABLE
    verdict, err = ytdlp.probe(f"{base}/missing.mp4")
    assert verdict is ytdlp.Verdict.GONE_WEAK, err


def test_db_in_new_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(ytdlp, "available", lambda: True)
    db_path = str(tmp_path / "new" / "dir" / "v.db")
    assert cli.main(["retry", "--db", db_path, "--log", ""]) == cli.EXIT_OK
