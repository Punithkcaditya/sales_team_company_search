#!/usr/bin/env bash
# Runs the API and the UI. Ctrl-C stops both.
# API_PORT overrides the default when something else already holds 8000.
set -euo pipefail
cd "$(dirname "$0")"

VENV_PY="backend/.venv/bin/python"
[ -x "$VENV_PY" ] || VENV_PY="backend/.venv/Scripts/python.exe"
[ -x "$VENV_PY" ] || { echo "Run ./setup.sh first."; exit 1; }

API_PORT="${API_PORT:-8000}"
export VITE_API_TARGET="http://127.0.0.1:${API_PORT}"

# Probing the port is not enough: a socket can stay listed under a dead process
# for a while, which looks busy but binds fine. Only a real bind tells the truth.
if ! "$VENV_PY" -c "import socket,sys
s=socket.socket()
try: s.bind(('127.0.0.1', $API_PORT))
except OSError: sys.exit(1)
finally: s.close()" 2>/dev/null; then
  echo "Port ${API_PORT} is in use by another program."
  echo "Start on another one with:  API_PORT=8030 ./dev.sh"
  exit 1
fi

echo "API on :${API_PORT} - the UI port is printed by Vite below"
"$VENV_PY" -m uvicorn app.main:app --app-dir backend --port "$API_PORT" --reload &
API_PID=$!
trap 'kill $API_PID 2>/dev/null' EXIT INT TERM

npm run dev --prefix frontend
