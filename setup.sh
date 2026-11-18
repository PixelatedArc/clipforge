#!/usr/bin/env bash
# ClipForge setup for macOS / Linux.  Run:  bash setup.sh
set -e
cd "$(dirname "$0")"

PY=""
for v in python3.13 python3.12 python3.11 python3.10; do
  if command -v "$v" >/dev/null 2>&1; then PY="$v"; break; fi
done
if [ -z "$PY" ]; then
  echo "Need Python 3.10-3.13."
  echo "  macOS:  brew install python@3.13"
  echo "  Ubuntu: sudo apt install python3.12 python3.12-venv"
  exit 1
fi

for tool in ffmpeg node; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "Missing $tool."
    echo "  macOS:  brew install ffmpeg node"
    echo "  Ubuntu: sudo apt install ffmpeg nodejs fonts-noto"
    exit 1
  fi
done

[ -x .venv/bin/python ] || "$PY" -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
echo
echo "Setup complete. Start with:  bash start.sh"
