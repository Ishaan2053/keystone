#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  echo "Missing .venv. Run python3 -m venv .venv and install requirements.txt first." >&2
  exit 1
fi
if ! command -v npm >/dev/null 2>&1; then
  echo "Missing npm. Install Node.js first." >&2
  exit 1
fi
if [[ ! -d "$ROOT/web/node_modules" ]]; then
  echo "Missing web dependencies. Run npm ci in $ROOT/web first." >&2
  exit 1
fi

# Separate job groups let cleanup stop reload workers and npm's children too.
set -m
api_pid=""
web_pid=""

cleanup() {
  trap - EXIT INT TERM
  for pid in "$api_pid" "$web_pid"; do
    if [[ -n "$pid" ]]; then
      kill -TERM -- "-$pid" 2>/dev/null || true
    fi
  done
  for pid in "$api_pid" "$web_pid"; do
    if [[ -n "$pid" ]]; then
      wait "$pid" 2>/dev/null || true
    fi
  done
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

cd "$ROOT"
.venv/bin/python -m uvicorn api.main:app --reload --port 8000 &
api_pid=$!
(cd "$ROOT/web" && exec npm run dev) &
web_pid=$!

printf '\nStarting API: http://localhost:8000/docs\nStarting web: http://localhost:3001/t/falkordb\nPress Ctrl+C to stop both servers.\n\n'

# macOS ships Bash 3.2, which does not support wait -n.
while kill -0 "$api_pid" 2>/dev/null && kill -0 "$web_pid" 2>/dev/null; do
  sleep 1
done
status=0
if kill -0 "$api_pid" 2>/dev/null; then
  wait "$web_pid" || status=$?
else
  wait "$api_pid" || status=$?
fi
exit "$status"
