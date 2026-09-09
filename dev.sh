#!/usr/bin/env bash
# Runs the API on :8000 and the UI on :5173. Ctrl-C stops both.
set -euo pipefail
cd "$(dirname "$0")"

VENV_PY="backend/.venv/bin/python"
[ -x "$VENV_PY" ] || VENV_PY="backend/.venv/Scripts/python.exe"
[ -x "$VENV_PY" ] || { echo "Run ./setup.sh first."; exit 1; }

"$VENV_PY" -m uvicorn app.main:app --app-dir backend --port 8000 --reload &
API_PID=$!
trap 'kill $API_PID 2>/dev/null' EXIT INT TERM

npm run dev --prefix frontend
