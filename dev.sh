#!/usr/bin/env bash
#
# dev.sh - one-command local development for Keystone.
#
# From a fresh checkout this script:
#   1. checks the tools it needs (docker, docker compose, python3, npm)
#   2. creates .env from .env.example and generates a FalkorDB password if blank
#   3. creates .venv + installs Python deps, installs web deps when missing
#   4. starts FalkorDB in Docker (no-op if already healthy) and waits for it
#   5. runs the API (uvicorn --reload, :8000) and web (next dev, :3001) locally
#
# Ctrl+C stops the API and web. FalkorDB keeps running; stop every Docker
# service (containers and the named data volume are kept) with:
#   ./dev.sh --stop
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$ROOT/.env"
ENV_EXAMPLE="$ROOT/.env.example"
VENV="$ROOT/.venv"
API_PORT=8000
WEB_PORT=3001

log()  { printf '%s\n' "$*"; }
warn() { printf 'warn: %s\n' "$*" >&2; }
die()  { printf 'error: %s\n' "$*" >&2; exit 1; }

# --- .env helpers ----------------------------------------------------------

# Print the value of KEY in .env (surrounding quotes stripped); empty if absent.
env_get() {
  key="$1"
  [ -f "$ENV_FILE" ] || return 0
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in ''|\#*) continue ;; esac
    case "$line" in *=*) ;; *) continue ;; esac
    this_key="$(printf '%s' "${line%%=*}" | tr -d '[:space:]')"
    [ "$this_key" = "$key" ] || continue
    value="${line#*=}"
    value="$(printf '%s' "$value" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
    case "$value" in
      \"*\") value="${value#\"}"; value="${value%\"}" ;;
      \'*\') value="${value#\'}"; value="${value%\'}" ;;
    esac
    printf '%s' "$value"
    return 0
  done < "$ENV_FILE"
}

# Set KEY=VALUE in .env, replacing an existing line or appending a new one.
env_set() {
  key="$1"; value="$2"
  tmp="$(mktemp "${TMPDIR:-/tmp}/keystone-env.XXXXXX")"
  found=0
  if [ -f "$ENV_FILE" ]; then
    while IFS= read -r line || [ -n "$line" ]; do
      case "$line" in
        \#*) ;;
        *=*)
          k="$(printf '%s' "${line%%=*}" | tr -d '[:space:]')"
          if [ "$k" = "$key" ]; then
            printf '%s=%s\n' "$key" "$value"
            found=1
            continue
          fi
          ;;
      esac
      printf '%s\n' "$line"
    done < "$ENV_FILE" > "$tmp"
  fi
  [ "$found" -eq 1 ] || printf '%s=%s\n' "$key" "$value" >> "$tmp"
  mv "$tmp" "$ENV_FILE"
}

generate_password() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 24
  else
    python3 -c 'import secrets; print(secrets.token_hex(24))'
  fi
}

# --- --stop ----------------------------------------------------------------

usage() {
  cat <<'EOF'
Usage: ./dev.sh [options]

  (no options)   set up and run the local stack (FalkorDB + API + web)
  --stop, -s     stop the Docker services; containers and the data volume are kept
  --help, -h     show this help
EOF
}

# Stop the Docker services started for local development. This uses
# `docker compose stop`, which stops containers but never removes them or their
# named volumes, so graph data survives. It deliberately does not run
# `docker compose down -v` (which would delete the volume).
stop_services() {
  command -v docker >/dev/null 2>&1 || die "missing 'docker'. Install it and re-run."
  docker compose version >/dev/null 2>&1 || die "docker compose (v2) is required."

  # `docker compose stop` still interpolates the file, so satisfy its required
  # variables. Prefer real values from the shell or .env; otherwise use harmless
  # placeholders. Stopping never uses these values.
  if [ -z "${FALKORDB_PASSWORD:-}" ]; then
    FALKORDB_PASSWORD="$(env_get FALKORDB_PASSWORD)"
  fi
  : "${FALKORDB_PASSWORD:=stop-only}"
  export FALKORDB_PASSWORD
  if [ -z "${API_DOMAIN:-}" ]; then
    API_DOMAIN="$(env_get API_DOMAIN)"
  fi
  : "${API_DOMAIN:=localhost}"
  export API_DOMAIN

  ( cd "$ROOT" && docker compose stop )

  log "Stopped Docker services. The data volume is kept - no data was deleted."
  log "Start again with: ./dev.sh"
}

case "${1:-}" in
  "") ;;
  --stop|-s) stop_services; exit 0 ;;
  --help|-h) usage; exit 0 ;;
  *) usage >&2; die "unknown argument: $1" ;;
esac

# --- preflight -------------------------------------------------------------

for cmd in docker python3 npm; do
  command -v "$cmd" >/dev/null 2>&1 || die "missing '$cmd'. Install it and re-run."
done
docker compose version >/dev/null 2>&1 || die "docker compose (v2) is required."
docker info >/dev/null 2>&1 || die "the Docker daemon is not running. Start Docker Desktop and re-run."

# --- environment -----------------------------------------------------------

if [ ! -f "$ENV_FILE" ]; then
  if [ -f "$ENV_EXAMPLE" ]; then
    cp "$ENV_EXAMPLE" "$ENV_FILE"
    log "Created .env from .env.example"
  else
    : > "$ENV_FILE"
    log "Created empty .env"
  fi
fi

if [ -z "$(env_get FALKORDB_PASSWORD)" ]; then
  env_set FALKORDB_PASSWORD "$(generate_password)"
  log "Generated FALKORDB_PASSWORD in .env"
fi

# Compose interpolates every service in the file, including caddy's required
# API_DOMAIN, even when only starting falkordb. Supply a local default.
if [ -z "${API_DOMAIN:-}" ] && [ -z "$(env_get API_DOMAIN)" ]; then
  export API_DOMAIN=localhost
fi

# --- dependencies ----------------------------------------------------------

if [ ! -x "$VENV/bin/python" ]; then
  log "Creating Python virtualenv (.venv)..."
  python3 -m venv "$VENV"
fi

stamp="$VENV/.requirements.stamp"
if [ ! -f "$stamp" ] || [ "$ROOT/requirements.txt" -nt "$stamp" ]; then
  log "Installing Python dependencies..."
  "$VENV/bin/python" -m pip install --quiet --disable-pip-version-check --upgrade pip
  "$VENV/bin/python" -m pip install --quiet --disable-pip-version-check -r "$ROOT/requirements.txt"
  touch "$stamp"
fi

if [ ! -d "$ROOT/web/node_modules" ]; then
  log "Installing web dependencies (npm ci)..."
  ( cd "$ROOT/web" && npm ci )
fi

# --- warn about busy ports -------------------------------------------------

if command -v lsof >/dev/null 2>&1; then
  for pair in "API:$API_PORT" "web:$WEB_PORT"; do
    name="${pair%%:*}"; port="${pair#*:}"
    if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
      warn "port $port ($name) is already in use; it may fail to start."
    fi
  done
fi

# --- FalkorDB via Docker ---------------------------------------------------

log "Starting FalkorDB (docker compose up -d falkordb)..."
( cd "$ROOT" && docker compose up -d falkordb )

cid="$( cd "$ROOT" && docker compose ps -q falkordb )"
[ -n "$cid" ] || die "falkordb container was not found after 'docker compose up'."

log "Waiting for FalkorDB to become healthy..."
healthy=0
for _ in $(seq 1 90); do
  status="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$cid" 2>/dev/null || echo unknown)"
  if [ "$status" = healthy ]; then
    healthy=1
    break
  fi
  if [ "$status" = unhealthy ]; then
    die "FalkorDB reported unhealthy. Inspect with: docker compose logs falkordb"
  fi
  sleep 1
done
[ "$healthy" -eq 1 ] || die "timed out waiting for FalkorDB to become healthy."
log "FalkorDB is healthy."

# --- local servers ---------------------------------------------------------

log ""
log "API:      http://localhost:${API_PORT}/docs"
log "Web:      http://localhost:${WEB_PORT}/t/falkordb"
log "FalkorDB: localhost:6379 (browser http://localhost:3000)"
log "Ctrl+C stops the API and web. FalkorDB keeps running: docker compose stop falkordb"
log ""

set -m
api_pid=""
web_pid=""

cleanup() {
  trap - EXIT INT TERM
  for pid in "$api_pid" "$web_pid"; do
    if [ -n "$pid" ]; then
      kill -TERM -- "-$pid" 2>/dev/null || true
    fi
  done
  for pid in "$api_pid" "$web_pid"; do
    if [ -n "$pid" ]; then
      wait "$pid" 2>/dev/null || true
    fi
  done
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

cd "$ROOT"
"$VENV/bin/python" -m uvicorn api.main:app --reload --port "$API_PORT" &
api_pid=$!
( cd "$ROOT/web" && exec npm run dev ) &
web_pid=$!

# macOS ships Bash 3.2, which has no `wait -n`; poll both jobs instead.
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
