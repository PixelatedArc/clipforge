#!/usr/bin/env bash
# ClipForge for macOS / Linux.  Run:  bash start.sh        (add --lan for phone mode)
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || bash setup.sh || exit 1
.venv/bin/python -m pip install -q --upgrade "yt-dlp[default]" >/dev/null 2>&1
exec .venv/bin/python app.py "$@"
