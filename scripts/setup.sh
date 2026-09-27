#!/usr/bin/env bash
# Linux / macOS setup: creates the venv, installs backend + frontend dependencies.
# Usage: bash scripts/setup.sh   (or: chmod +x scripts/setup.sh && ./scripts/setup.sh)
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -r requirements.txt -r requirements-dev.txt

(cd frontend && npm install)

echo
echo "Setup complete. Next:"
echo "  Terminal 1: bash scripts/run_backend.sh   (http://127.0.0.1:8000, docs at /docs)"
echo "  Terminal 2: bash scripts/run_frontend.sh  (http://localhost:5173)"
