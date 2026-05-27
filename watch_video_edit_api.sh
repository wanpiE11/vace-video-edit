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
DEEP_CLEANUP_ON_RESTART="${DEEP_CLEANUP_ON_RESTART:-1}"
ENGINE_SOCKET_PATH="${ENGINE_SOCKET_PATH:-/tmp/vace_wan_infer.sock}"
DEEP_CLEANUP_GRACE_SECONDS="${DEEP_CLEANUP_GRACE_SECONDS:-20}"

LOG_DIR="${LOG_DIR:-$ROOT_DIR/logs}"
PID_FILE="${PID_FILE:-$LOG_DIR/video_edit_api.pid}"
WATCHDOG_LOG="${WATCHDOG_LOG:-$LOG_DIR/video_edit_watchdog.log}"
SERVICE_LOG="${SERVICE_LOG:-$LOG_DIR/video_edit_api_watchdog_service.log}"

PROCESS_MARKER="python -m uvicorn video_edit_api:app"
ENGINE_MARKER="run_edit_video_server.py"
JOB_RUNNER_MARKER="run_edit_video.py"

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

log_gpu_processes() {
  if ! command -v nvidia-smi >/dev/null 2>&1; then
    log "gpu process check skipped: nvidia-smi not found"
    return 0
  fi

  log "gpu process snapshot begin"
  nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader,nounits 2>&1 \
    | while IFS= read -r line; do
        [[ -z "$line" ]] && continue
        log "gpu process $line"
      done
  log "gpu process snapshot end"
}

find_processes_by_markers() {
  local first_marker="$1"
  local second_marker="${2:-}"

  ps -eo pid=,args= \
    | awk -v first="$first_marker" -v second="$second_marker" \
        'index($0, first) && (second == "" || index($0, second)) && !index($0, "awk -v first=") { print $1 }'
}

wait_for_pid_exit() {
  local pid="$1"
  local deadline=$((SECONDS + DEEP_CLEANUP_GRACE_SECONDS))

  while kill -0 "$pid" 2>/dev/null; do
    if (( SECONDS >= deadline )); then
      return 1
    fi
    sleep 1
  done

  return 0
}

terminate_process_group_or_pid() {
  local pid="$1"
  local label="$2"
  local pgid
  local self_pgid

  if ! kill -0 "$pid" 2>/dev/null; then
    return 0
  fi

  pgid="$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d ' ' || true)"
  self_pgid="$(ps -o pgid= -p "$$" 2>/dev/null | tr -d ' ' || true)"

  if [[ -n "$pgid" && "$pgid" != "$self_pgid" ]]; then
    log "deep cleanup stopping $label pid=$pid pgid=$pgid signal=TERM"
    kill -TERM "-$pgid" 2>/dev/null || kill "$pid" 2>/dev/null || true
  else
    log "deep cleanup stopping $label pid=$pid signal=TERM"
    kill "$pid" 2>/dev/null || true
  fi

  if wait_for_pid_exit "$pid"; then
    return 0
  fi

  if [[ -n "$pgid" && "$pgid" != "$self_pgid" ]]; then
    log "deep cleanup force killing $label pid=$pid pgid=$pgid signal=KILL"
    kill -KILL "-$pgid" 2>/dev/null || kill -9 "$pid" 2>/dev/null || true
  else
    log "deep cleanup force killing $label pid=$pid signal=KILL"
    kill -9 "$pid" 2>/dev/null || true
  fi
}

cleanup_matching_processes() {
  local label="$1"
  local first_marker="$2"
  local second_marker="$3"
  local pid
  local found=0

  while read -r pid; do
    [[ -z "$pid" ]] && continue
    terminate_process_group_or_pid "$pid" "$label"
    found=1
  done < <(find_processes_by_markers "$first_marker" "$second_marker")

  if (( found == 0 )); then
    log "deep cleanup found no $label processes"
  fi
}

deep_cleanup_runtime() {
  if [[ "$DEEP_CLEANUP_ON_RESTART" != "1" ]]; then
    log "deep cleanup skipped deep_cleanup_on_restart=$DEEP_CLEANUP_ON_RESTART"
    return 0
  fi

  log "deep cleanup starting socket_path=$ENGINE_SOCKET_PATH"
  log_gpu_processes
  cleanup_matching_processes "engine daemon" "$ENGINE_MARKER" "--socket-path $ENGINE_SOCKET_PATH"
  cleanup_matching_processes "job runner" "$JOB_RUNNER_MARKER" "--server-socket $ENGINE_SOCKET_PATH"

  if [[ -S "$ENGINE_SOCKET_PATH" || -e "$ENGINE_SOCKET_PATH" ]]; then
    rm -f "$ENGINE_SOCKET_PATH"
    log "deep cleanup removed socket_path=$ENGINE_SOCKET_PATH"
  fi

  log_gpu_processes
  log "deep cleanup finished"
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
  deep_cleanup_runtime
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
