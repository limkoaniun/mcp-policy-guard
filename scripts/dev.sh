#!/usr/bin/env bash
# Launch the demo: the FastAPI/uvicorn backend (agent + proxy) and the frontend.
#
#   scripts/dev.sh          backend + Vite dev server (hot reload)  -> http://localhost:5173
#   scripts/dev.sh demo     backend only, serves the committed build -> http://localhost:8765
#
# Needs: .venv with the project installed (pip install -e ".[dev]"),
#        Ollama running on localhost:11434, and for dev mode `npm install` in frontend/.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

MODE="${1:-dev}"
BACKEND_PORT="${BACKEND_PORT:-8765}"   # vite.config.js proxies /api here; keep in sync
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
PY="$ROOT/.venv/bin/python"

[ -x "$PY" ] || { echo "no .venv: run  python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'" >&2; exit 1; }

if ! curl -s -m 2 -o /dev/null localhost:11434/api/tags; then
  echo "Ollama is not answering on :11434. Start it with:  ollama serve" >&2
  exit 1
fi

pids=()
cleanup() { trap - INT TERM EXIT; kill "${pids[@]}" 2>/dev/null || true; }
trap cleanup INT TERM EXIT

# Poll a URL until it answers 2xx, or give up after ~30s.
wait_for() {
  for _ in $(seq 1 60); do
    curl -sf -m 1 -o /dev/null "$1" && return 0
    sleep 0.5
  done
  echo "!! $1 never came up; see the log above" >&2
  return 1
}

"$PY" -m uvicorn enrolment.demo.app:create_app --factory --port "$BACKEND_PORT" &
pids+=($!)
wait_for "http://localhost:$BACKEND_PORT/api/tools"

case "$MODE" in
  demo)
    OPEN_URL="http://localhost:$BACKEND_PORT"
    ;;
  dev)
    [ -d frontend/node_modules ] || (cd frontend && npm install)
    (cd frontend && npm run dev -- --port "$FRONTEND_PORT" --strictPort) &
    pids+=($!)
    wait_for "http://localhost:$FRONTEND_PORT/api/tools"
    OPEN_URL="http://localhost:$FRONTEND_PORT"
    ;;
  *)
    echo "usage: $0 [dev|demo]" >&2; exit 2 ;;
esac

cat <<BANNER

==================================================
  Ready.  Open this in your browser:

      $OPEN_URL

  backend API:  http://localhost:$BACKEND_PORT/api
  Ctrl+C stops everything.
==================================================

BANNER

wait
