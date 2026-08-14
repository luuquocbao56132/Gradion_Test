#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
ROOT="$(pwd)"
PY="$ROOT/.venv/bin/python"; [ -x "$PY" ] || PY="$ROOT/.venv/Scripts/python.exe"

if [ ! -f .env ]; then
  echo "No .env found. Copy .env.example to .env and set GEMINI_API_KEY." >&2
  exit 1
fi

# One worker is required: server_run_id orphan detection and the in-memory
# realtime registry both assume a single process (design 5.3, 9.5).
( cd backend && "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 ) &
BACKEND=$!
( cd frontend && npm run dev ) &
FRONTEND=$!
trap 'kill "$BACKEND" "$FRONTEND" 2>/dev/null || true' EXIT INT TERM
wait
