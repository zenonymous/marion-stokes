#!/usr/bin/env bash
# ──────────────────────────────────────────────────
# Cron wrapper: runs scan, then check.
# Add to crontab with:  crontab -e
#   0 */6 * * * /path/to/marion-stokes/cron_run.sh >> /path/to/marion-stokes/cron.log 2>&1
#
# Extra arguments are passed to both commands, so only use shared options
# (--db, --feeds, --download-dir, --log, -v, --notify-url). Settings for a single
# command can come from the environment, e.g. MARION_STOKES_CHECK_LIMIT.
# If a Python virtualenv exists at ./.venv, it is used.
# ──────────────────────────────────────────────────
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PY=python3
if [[ -x .venv/bin/python ]]; then
    PY=.venv/bin/python
    export PATH="$SCRIPT_DIR/.venv/bin:$PATH"   # so the venv's yt-dlp is found
fi

# Run check even if scan fails, then exit with the worse of the two codes.
"$PY" video_archiver.py scan "$@";  scan_rc=$?
"$PY" video_archiver.py check "$@"; check_rc=$?
exit $(( scan_rc > check_rc ? scan_rc : check_rc ))
