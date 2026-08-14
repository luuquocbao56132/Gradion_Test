#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
ROOT="$(pwd)"
PY="$ROOT/.venv/bin/python"; [ -x "$PY" ] || PY="$ROOT/.venv/Scripts/python.exe"

echo "=== backend (pytest) ==="
( cd backend && "$PY" -m pytest -v )

echo
echo "=== frontend (vitest) ==="
( cd frontend && npm test -- --run )
