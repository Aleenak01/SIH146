#!/usr/bin/env bash
# Linux / macOS: serves the API on 127.0.0.1:8000 (local only), interactive docs at /docs.
# Usage: bash scripts/run_backend.sh
set -euo pipefail
cd "$(dirname "$0")/.."
exec ./.venv/bin/python -m backend
