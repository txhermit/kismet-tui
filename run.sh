#!/usr/bin/env bash
# Kismet TUI launcher -- creates the venv + installs Textual on first run.
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -q textual
fi
exec .venv/bin/python -m kismet_tui "$@"
