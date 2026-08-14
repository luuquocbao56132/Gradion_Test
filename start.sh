#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
ROOT="$(pwd)"
PY="$ROOT/.venv/bin/python"; [ -x "$PY" ] || PY="$ROOT/.venv/Scripts/python.exe"

BACKEND_PORT=8000
FRONTEND_PORT=5173

if [ ! -f .env ]; then
  echo "No .env found. Copy .env.example to .env and set GEMINI_API_KEY." >&2
  exit 1
fi

is_windows() {
  case "$(uname -s)" in MINGW*|MSYS*|CYGWIN*) return 0 ;; *) return 1 ;; esac
}

# Shut down by port rather than by job id.
#
# Killing the shell's job PIDs is not enough. `npm run dev` spawns vite as a
# separate process that outlives the npm wrapper, so the frontend keeps serving
# after the backend is gone. And under Git Bash the shell's job ids are MSYS
# numbers, not Windows PIDs, so neither kill nor taskkill can reach the real
# process. The listening port is the only handle that is correct on both.
free_port() {
  port="$1"
  if is_windows; then
    powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id \$_.OwningProcess -Force -ErrorAction SilentlyContinue }" >/dev/null 2>&1 || true
  else
    lsof -ti tcp:"$port" 2>/dev/null | xargs kill -TERM 2>/dev/null || true
  fi
}

cleanup() {
  trap - EXIT INT TERM
  echo ""
  echo "Stopping backend and frontend..."
  free_port "$BACKEND_PORT"
  free_port "$FRONTEND_PORT"
}
trap cleanup EXIT INT TERM

# Startup is idempotent. Answering "Y" to CMD's "Terminate batch job?" prompt
# kills the shell before any cleanup can run, which strands vite on its port;
# clearing both ports here means the next run just works instead of failing with
# "address already in use".
free_port "$BACKEND_PORT"
free_port "$FRONTEND_PORT"

# One worker is required: server_run_id orphan detection and the in-memory
# realtime registry both assume a single process (design 5.3, 9.5).
( cd backend && "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port "$BACKEND_PORT" --workers 1 ) &
( cd frontend && npm run dev ) &
wait
