"""Export the archive index as CSV or a self-contained HTML page."""

from __future__ import annotations

import csv
import datetime
import html
import io
import os
import sqlite3

COLUMNS = [
    "platform", "video_id", "title", "uploader", "url", "availability",
    "is_available", "deleted_date", "first_seen", "last_checked",
    "duration", "file_size_bytes", "download_path", "feed_source",
]


def _rows(conn: sqlite3.Connection, deleted_only: bool) -> list[sqlite3.Row]:
    where = "WHERE is_available = 0" if deleted_only else ""
    return conn.execute(
        f"SELECT {', '.join(COLUMNS)} FROM videos {where} "
        "ORDER BY is_available, deleted_date DESC, first_seen DESC"
    ).fetchall()


def to_csv(conn: sqlite3.Connection, deleted_only: bool = False) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(COLUMNS)
    for row in _rows(conn, deleted_only):
        writer.writerow([row[c] for c in COLUMNS])
    return buf.getvalue()


def _size(n) -> str:
    if not n:
        return ""
    return f"{int(n) / 1_048_576:,.0f} MB"


def to_html(conn: sqlite3.Connection, deleted_only: bool = False, output_path: str | None = None) -> str:
    rows = _rows(conn, deleted_only)
    base = os.path.dirname(os.path.abspath(output_path)) if output_path else os.getcwd()
    esc = html.escape
    body = []
    for r in rows:
        local = ""
        if r["download_path"]:
            rel = os.path.relpath(os.path.abspath(r["download_path"]), base)
            local = f'<a href="{esc(rel)}">file</a>'
        gone = not r["is_available"]
        body.append(
            f'<tr class="{"gone" if gone else ""}">'
            f'<td>{esc(r["availability"] or "")}</td>'
            f'<td><a href="{esc(r["url"])}">{esc(r["title"] or r["url"])}</a></td>'
            f'<td>{esc(r["uploader"] or "")}</td>'
            f'<td>{esc(r["platform"] or "")}</td>'
            f'<td>{esc((r["deleted_date"] or "")[:10])}</td>'
            f'<td>{esc((r["first_seen"] or "")[:10])}</td>'
            f'<td class="num">{_size(r["file_size_bytes"])}</td>'
            f"<td>{local}</td></tr>"
        )
    total = len(rows)
    gone = sum(1 for r in rows if not r["is_available"])
    generated = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M UTC")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>marion-stokes archive</title>
<style>
:root {{ color-scheme: light dark; --gone: #c0392b; }}
body {{ font: 14px/1.4 system-ui, sans-serif; margin: 16px; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ text-align: left; padding: 4px 8px; border-bottom: 1px solid #8884; vertical-align: top; }}
td.num {{ text-align: right; white-space: nowrap; }}
tr.gone td:first-child {{ color: var(--gone); font-weight: 600; }}
.wrap {{ overflow-x: auto; }}
</style></head><body>
<h1>marion-stokes archive</h1>
<p>{total} video(s), {gone} no longer available online. Generated {generated}.</p>
<div class="wrap"><table>
<thead><tr><th>Status</th><th>Title</th><th>Uploader</th><th>Platform</th>
<th>Gone since</th><th>First seen</th><th>Size</th><th>Local</th></tr></thead>
<tbody>
{chr(10).join(body)}
</tbody></table></div>
</body></html>
"""
