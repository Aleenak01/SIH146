#!/usr/bin/env bash
# Linux / macOS: the full backend test suite, then the automated end-to-end check.
# Usage: bash scripts/run_tests.sh
set -euo pipefail
cd "$(dirname "$0")/.."
./.venv/bin/python -m pytest
./.venv/bin/python scripts/e2e_check.py
