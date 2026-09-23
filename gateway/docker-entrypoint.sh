#!/bin/sh
set -eu

log() { printf '%s\n' "[entrypoint] $*"; }
fatal() { printf '%s\n' "[entrypoint] FATAL: $*" >&2; exit 1; }
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

: "${PORT:=8000}"
export PORT

log "Checking required runtime configuration"
: "${DATABASE_URL:?DATABASE_URL is required}"
: "${REDIS_URL:?REDIS_URL is required}"
: "${JWT_SECRET:?JWT_SECRET is required}"
: "${API_KEY_SALT:?API_KEY_SALT is required}"

log "Running database migrations"
if ! MIGRATIONS_STRICT=1 alembic upgrade head; then
  fatal "database migrations failed; verify DATABASE_URL, PostgreSQL reachability, SSL mode, and schema permissions"
fi

log "Starting FastAPI on 0.0.0.0:${PORT}"
uvicorn app.main:app --host 0.0.0.0 --port "${PORT}" --workers "${WEB_CONCURRENCY:-1}" &
server_pid=$!

cleanup() {
  kill "$server_pid" 2>/dev/null || true
  wait "$server_pid" 2>/dev/null || true
}
trap cleanup INT TERM EXIT

attempts="${STARTUP_HEALTH_ATTEMPTS:-30}"
delay="${STARTUP_HEALTH_DELAY_SECONDS:-2}"
i=1
while [ "$i" -le "$attempts" ]; do
  if python "$SCRIPT_DIR/container_health.py"; then
    log "Startup health gate passed"
    wait "$server_pid"
    exit $?
  fi
  if ! kill -0 "$server_pid" 2>/dev/null; then
    fatal "FastAPI process exited before passing health gate"
  fi
  log "Startup health gate not ready (attempt ${i}/${attempts}); retrying in ${delay}s"
  i=$((i + 1))
  sleep "$delay"
done

fatal "startup health gate failed after ${attempts} attempts; inspect the failed check above"
