#!/usr/bin/env bash
# Linux / macOS: the UI at http://localhost:5173. Add --host below to also serve it to a phone on the same network.
# Usage: bash scripts/run_frontend.sh
set -euo pipefail
cd "$(dirname "$0")/../frontend"
exec npm run dev
