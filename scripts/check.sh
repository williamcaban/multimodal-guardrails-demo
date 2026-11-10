#!/usr/bin/env bash
# Full quality gate: lint, format, types, dead code, unit + integration tests.
# Run before every commit. Live tests (-m live) are opt-in, see tests/test_live.py.
set -euo pipefail
cd "$(dirname "$0")/.."

BIN=.venv/bin
TARGETS=(ui rhoai tests demo.py make_samples.py)

echo "== ruff";    "$BIN/ruff" check .
echo "== black";   "$BIN/black" --check --quiet .
echo "== mypy";    "$BIN/mypy" "${TARGETS[@]}"
echo "== vulture"; "$BIN/vulture"
echo "== pytest";  "$BIN/pytest" -q -m "not live"
