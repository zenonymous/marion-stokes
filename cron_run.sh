#!/usr/bin/env bash
# ──────────────────────────────────────────────────
# Cron wrapper — runs scan + check in sequence
# Add to crontab with:  crontab -e
#   0 */6 * * * /path/to/video_archiver/cron_run.sh
# ──────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

python3 video_archiver.py scan   "$@"
python3 video_archiver.py check  "$@"
