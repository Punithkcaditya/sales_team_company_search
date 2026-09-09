#!/usr/bin/env bash
# Installs both halves of the app.
set -euo pipefail
cd "$(dirname "$0")"

echo "==> backend"
python3 -m venv backend/.venv 2>/dev/null || python -m venv backend/.venv
VENV_PY="backend/.venv/bin/python"
[ -x "$VENV_PY" ] || VENV_PY="backend/.venv/Scripts/python.exe"
"$VENV_PY" -m pip install --quiet --upgrade pip
"$VENV_PY" -m pip install --quiet -r backend/requirements-dev.txt

echo "==> frontend"
npm install --prefix frontend --no-audit --no-fund

echo
echo "Done. Run ./dev.sh, then open http://localhost:5173"
echo "Optional: cp backend/.env.example backend/.env and add API keys for live research."
