#!/usr/bin/env bash

set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ACTIVATE_SCRIPT="$ROOT_DIR/activate_vace.sh"

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8880}"
BASE_URL="${BASE_URL:-http://127.0.0.1:$PORT}"
CHECK_INTERVAL_SECONDS="${CHECK_INTERVAL_SECONDS:-30}"
HEALTH_TIMEOUT_SECONDS="${HEALTH_TIMEOUT_SECONDS:-5}"
MAX_CONSECUTIVE_FAILURES="${MAX_CONSECUTIVE_FAILURES:-2}"
RESTART_ON_ENGINE_FAILED="${RESTART_ON_ENGINE_FAILED:-1}"
RESTART_GRACE_SECONDS="${RESTART_GRACE_SECONDS:-30}"
AUTO_LOAD_ENGINE_AFTER_START="${AUTO_LOAD_ENGINE_AFTER_START:-1}"
API_STARTUP_TIMEOUT_SECONDS="${API_STARTUP_TIMEOUT_SECONDS:-60}"

LOG_DIR="${LOG_DIR:-$ROOT_DIR/logs}"
PID_FILE="${PID_FILE:-$LOG_DIR/video_edit_api.pid}"
WATCHDOG_LOG="${WATCHDOG_LOG:-$LOG_DIR/video_edit_watchdog.log}"
SERVICE_LOG="${SERVICE_LOG:-$LOG_DIR/video_edit_api_watchdog_service.log}"

PROCESS_MARKER="python -m uvicorn video_edit_api:app"

mkdir -p "$LOG_DIR"

timestamp() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

log() {
  printf '%s %s\n' "$(timestamp)" "$*" | tee -a "$WATCHDOG_LOG"
}

read_pid_file() {
  if [[ ! -f "$PID_FILE" ]]; then
    return 1
  fi

  local pid
  pid="$(<"$PID_FILE")"
  if [[ "$pid" =~ ^[0-9]+$ ]]; then
    printf '%s\n' "$pid"
    return 0
  fi

  return 1
}

is_managed_api_pid() {
  local pid="$1"
  local args

  if ! kill -0 "$pid" 2>/dev/null; then
    return 1
  fi

  args="$(ps -p "$pid" -o args= 2>/dev/null || true)"
  [[ "$args" == *"$PROCESS_MARKER"* && "$args" == *"--port $PORT"* ]]
}

managed_pid() {
  local pid

  if pid="$(read_pid_file)"; then
    if is_managed_api_pid "$pid"; then
      printf '%s\n' "$pid"
      return 0
    fi
  fi

  return 1
}

find_api_pids() {
  ps -eo pid=,args= \
    | awk -v marker="$PROCESS_MARKER" -v port="--port $PORT" \
        'index($0, marker) && index($0, port) && !index($0, "awk -v marker=") { print $1 }'
}

first_api_pid() {
  local pid

  while read -r pid; do
    [[ -z "$pid" ]] && continue
    printf '%s\n' "$pid"
    return 0
  done < <(find_api_pids)

  return 1
}

start_api() {
  local pid

  if pid="$(managed_pid)"; then
    log "api already running pid=$pid"
    load_engine_after_api_start || true
    return 0
  fi

  if pid="$(first_api_pid)"; then
    printf '%s\n' "$pid" >"$PID_FILE"
    log "api already running pid=$pid adopted_existing_process=1"
    load_engine_after_api_start || true
    return 0
  fi

  if [[ ! -f "$ACTIVATE_SCRIPT" ]]; then
    log "cannot start api: missing $ACTIVATE_SCRIPT"
    return 1
  fi

  log "starting api host=$HOST port=$PORT service_log=$SERVICE_LOG"
  (
    cd "$ROOT_DIR" || exit 1
    # shellcheck disable=SC1090
    source "$ACTIVATE_SCRIPT"
    cd "$ROOT_DIR" || exit 1
    exec python -m uvicorn video_edit_api:app --host "$HOST" --port "$PORT"
  ) >>"$SERVICE_LOG" 2>&1 &

  pid="$!"
  printf '%s\n' "$pid" >"$PID_FILE"
  log "api started pid=$pid"
  load_engine_after_api_start || true
}

api_health_endpoint_ready() {
  local body
  local status

  body="$(curl --noproxy '*' -fsS --max-time "$HEALTH_TIMEOUT_SECONDS" "$BASE_URL/healthz" 2>&1)"
  status="$?"
  if [[ "$status" -ne 0 ]]; then
    printf 'curl failed status=%s output=%s\n' "$status" "$body"
    return 1
  fi

  HEALTH_BODY="$body" python - <<'PY'
import json
import os

try:
    payload = json.loads(os.environ["HEALTH_BODY"])
except Exception as exc:
    print(f"invalid json: {exc}")
    raise SystemExit(1)

data = payload.get("data") if isinstance(payload, dict) else None
if payload.get("ok") is not True or not isinstance(data, dict) or data.get("status") != "ok":
    print("health response is not ok")
    raise SystemExit(1)

print(f"ready engine_state={data.get('engine_state')}")
PY
}

load_engine_after_api_start() {
  local deadline
  local output
  local status

  if [[ "$AUTO_LOAD_ENGINE_AFTER_START" != "1" ]]; then
    log "auto engine load skipped auto_load_engine_after_start=$AUTO_LOAD_ENGINE_AFTER_START"
    return 0
  fi

  deadline=$((SECONDS + API_STARTUP_TIMEOUT_SECONDS))
  while true; do
    output="$(api_health_endpoint_ready 2>&1)"
    status="$?"
    if [[ "$status" -eq 0 ]]; then
      log "api ready before engine load output=$output"
      break
    fi

    if (( SECONDS >= deadline )); then
      log "engine load skipped: api did not become ready within ${API_STARTUP_TIMEOUT_SECONDS}s output=$output"
      return 1
    fi

    sleep 1
  done

  output="$(curl --noproxy '*' -fsS --max-time "$HEALTH_TIMEOUT_SECONDS" -X POST "$BASE_URL/api/v1/video-editing/engine/load" 2>&1)"
  status="$?"
  if [[ "$status" -eq 0 ]]; then
    log "engine load requested output=$output"
    return 0
  fi

  log "engine load request failed status=$status output=$output"
  return 1
}

stop_pid() {
  local pid="$1"
  local deadline

  if ! kill -0 "$pid" 2>/dev/null; then
    return 0
  fi

  log "stopping api pid=$pid signal=TERM"
  kill "$pid" 2>/dev/null || true

  deadline=$((SECONDS + RESTART_GRACE_SECONDS))
  while kill -0 "$pid" 2>/dev/null; do
    if (( SECONDS >= deadline )); then
      log "api did not stop within ${RESTART_GRACE_SECONDS}s; signal=KILL pid=$pid"
      kill -9 "$pid" 2>/dev/null || true
      break
    fi
    sleep 1
  done
}

stop_api() {
  local pid
  local found=0

  if pid="$(managed_pid)"; then
    stop_pid "$pid"
    found=1
  fi

  while read -r pid; do
    [[ -z "$pid" ]] && continue
    stop_pid "$pid"
    found=1
  done < <(find_api_pids)

  rm -f "$PID_FILE"

  if (( found == 0 )); then
    log "no api process found to stop"
  fi
}

health_check() {
  local body
  local status

  body="$(curl --noproxy '*' -fsS --max-time "$HEALTH_TIMEOUT_SECONDS" "$BASE_URL/healthz" 2>&1)"
  status="$?"

  if [[ "$status" -ne 0 ]]; then
    printf 'curl failed status=%s output=%s\n' "$status" "$body"
    return 1
  fi

  HEALTH_BODY="$body" python - "$RESTART_ON_ENGINE_FAILED" <<'PY'
import json
import os
import sys

restart_on_engine_failed = sys.argv[1] == "1"

try:
    payload = json.loads(os.environ["HEALTH_BODY"])
except Exception as exc:
    print(f"invalid json: {exc}")
    raise SystemExit(1)

data = payload.get("data") if isinstance(payload, dict) else None
if payload.get("ok") is not True or not isinstance(data, dict) or data.get("status") != "ok":
    print("health response is not ok")
    raise SystemExit(1)

engine_state = data.get("engine_state")
if restart_on_engine_failed and engine_state == "failed":
    print("engine_state=failed")
    raise SystemExit(1)

print(f"ok engine_state={engine_state}")
PY
}

restart_api() {
  local reason="$1"

  log "restarting api reason=$reason"
  stop_api
  start_api
}

handle_signal() {
  log "watchdog received stop signal"
  exit 0
}

trap handle_signal INT TERM

log "watchdog starting base_url=$BASE_URL interval=${CHECK_INTERVAL_SECONDS}s max_failures=$MAX_CONSECUTIVE_FAILURES restart_on_engine_failed=$RESTART_ON_ENGINE_FAILED auto_load_engine_after_start=$AUTO_LOAD_ENGINE_AFTER_START"
start_api

consecutive_failures=0

while true; do
  health_output="$(health_check 2>&1)"
  health_status="$?"

  if [[ "$health_status" -eq 0 ]]; then
    if (( consecutive_failures > 0 )); then
      log "health recovered output=$health_output"
    else
      log "health ok output=$health_output"
    fi
    consecutive_failures=0
  else
    consecutive_failures=$((consecutive_failures + 1))
    log "health failed count=$consecutive_failures output=$health_output"

    if (( consecutive_failures >= MAX_CONSECUTIVE_FAILURES )); then
      restart_api "health_failed_${consecutive_failures}_times"
      consecutive_failures=0
    fi
  fi

  sleep "$CHECK_INTERVAL_SECONDS"
done
