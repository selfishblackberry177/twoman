#!/usr/bin/env bash
set -euo pipefail

require_env() {
  local name="$1"
  if [ -z "${!name:-}" ]; then
    echo "missing required env: ${name}" >&2
    exit 1
  fi
}

api_get() {
  local endpoint="$1"
  shift
  curl -sk --user "${TWOMAN_CPANEL_USERNAME}:${TWOMAN_CPANEL_PASSWORD}" \
    --get \
    "$@" \
    "${TWOMAN_CPANEL_BASE_URL}/execute/${endpoint}"
}

api_post() {
  local endpoint="$1"
  shift
  curl -sk --user "${TWOMAN_CPANEL_USERNAME}:${TWOMAN_CPANEL_PASSWORD}" \
    "$@" \
    "${TWOMAN_CPANEL_BASE_URL}/execute/${endpoint}"
}

upload_file() {
  local source_path="$1"
  local remote_dir="$2"
  local remote_name="$3"
  curl -sk --user "${TWOMAN_CPANEL_USERNAME}:${TWOMAN_CPANEL_PASSWORD}" \
    -F "dir=${remote_dir}" \
    -F "overwrite=1" \
    -F "file-1=@${source_path};filename=${remote_name}" \
    "${TWOMAN_CPANEL_BASE_URL}/execute/Fileman/upload_files" >/dev/null
}

upload_content() {
  local remote_dir="$1"
  local remote_name="$2"
  local content="$3"
  curl -sk --user "${TWOMAN_CPANEL_USERNAME}:${TWOMAN_CPANEL_PASSWORD}" \
    --data-urlencode "dir=${remote_dir}" \
    --data-urlencode "file=${remote_name}" \
    --data-urlencode "content=${content}" \
    --data-urlencode "from_charset=UTF-8" \
    --data-urlencode "to_charset=UTF-8" \
    --data-urlencode "fallback=1" \
    "${TWOMAN_CPANEL_BASE_URL}/execute/Fileman/save_file_content" >/dev/null
}

mkdir_api() {
  local parent_path="$1"
  local dir_name="$2"
  curl -sk --user "${TWOMAN_CPANEL_USERNAME}:${TWOMAN_CPANEL_PASSWORD}" \
    --get \
    --data-urlencode "cpanel_jsonapi_user=${TWOMAN_CPANEL_USERNAME}" \
    --data-urlencode "cpanel_jsonapi_apiversion=2" \
    --data-urlencode "cpanel_jsonapi_module=Fileman" \
    --data-urlencode "cpanel_jsonapi_func=mkdir" \
    --data-urlencode "path=${parent_path}" \
    --data-urlencode "name=${dir_name}" \
    "${TWOMAN_CPANEL_BASE_URL}/json-api/cpanel" >/dev/null || true
}

ensure_remote_dir() {
  local relative_path="$1"
  local current=""
  local part
  IFS='/' read -r -a parts <<< "${relative_path}"
  for part in "${parts[@]}"; do
    [ -n "${part}" ] || continue
    if [ -z "${current}" ]; then
      current="${part}"
      continue
    fi
    mkdir_api "${current}" "${part}"
    current="${current}/${part}"
  done
}

require_env TWOMAN_CPANEL_BASE_URL
require_env TWOMAN_CPANEL_USERNAME
require_env TWOMAN_CPANEL_PASSWORD
require_env TWOMAN_CPANEL_HOME
require_env TWOMAN_PUBLIC_ORIGIN
require_env TWOMAN_CLIENT_TOKEN
require_env TWOMAN_AGENT_TOKEN

TWOMAN_PUBLIC_BASE_PATH="${TWOMAN_PUBLIC_BASE_PATH:-/twoman}"
TWOMAN_APP_NAME="${TWOMAN_APP_NAME:-twoman_py}"
TWOMAN_APP_ROOT="${TWOMAN_APP_ROOT:-${TWOMAN_CPANEL_HOME}/twoman_passenger}"
if [ -z "${TWOMAN_ROUTE_TEMPLATE:-}" ]; then
  TWOMAN_ROUTE_TEMPLATE='/{lane}/{direction}'
fi
TWOMAN_HEALTH_TEMPLATE="${TWOMAN_HEALTH_TEMPLATE:-/health}"

APP_RELATIVE="${TWOMAN_APP_ROOT#${TWOMAN_CPANEL_HOME}/}"
REMOTE_TMP_DIR="${TWOMAN_APP_ROOT}/tmp"
REMOTE_LOG_DIR="${TWOMAN_APP_ROOT}/logs"

CONFIG_JSON="$(cat <<EOF
{
  "client_tokens": ["${TWOMAN_CLIENT_TOKEN}"],
  "agent_tokens": ["${TWOMAN_AGENT_TOKEN}"],
  "base_uri": "${TWOMAN_PUBLIC_BASE_PATH}",
  "binary_media_type": "image/webp",
  "route_template": "${TWOMAN_ROUTE_TEMPLATE}",
  "health_template": "${TWOMAN_HEALTH_TEMPLATE}",
  "down_wait_ms": {
    "ctl": 100,
    "data": 100
  },
  "streaming_ctl_down_helper": false,
  "streaming_data_down_helper": false,
  "peer_ttl_seconds": 90,
  "stream_ttl_seconds": 300,
  "max_lane_bytes": 16777216,
  "max_streams_per_peer_session": 256,
  "max_open_rate_per_peer_session": 120,
  "open_rate_window_seconds": 10,
  "max_peer_buffered_bytes": 33554432
}
EOF
)"

echo "Uploading Passenger host app files..."
ensure_remote_dir "${APP_RELATIVE}"
ensure_remote_dir "${APP_RELATIVE}/tmp"
ensure_remote_dir "${APP_RELATIVE}/logs"
ensure_remote_dir "${APP_RELATIVE}/runtime"
upload_file "runtime_diagnostics.py" "${TWOMAN_APP_ROOT}" "runtime_diagnostics.py"
upload_file "twoman_http.py" "${TWOMAN_APP_ROOT}" "twoman_http.py"
upload_file "twoman_protocol.py" "${TWOMAN_APP_ROOT}" "twoman_protocol.py"
upload_file "host/runtime/http_broker_daemon.py" "${TWOMAN_APP_ROOT}" "http_broker_daemon.py"
upload_content "${TWOMAN_APP_ROOT}" "broker_app.py" "$(cat host/passenger_python/broker_app.py)"
upload_content "${TWOMAN_APP_ROOT}" "passenger_proxy.py" "$(cat host/passenger_python/passenger_proxy.py)"
upload_content "${TWOMAN_APP_ROOT}" "passenger_wsgi.py" "$(cat host/passenger_python/passenger_wsgi.py)"
upload_content "${TWOMAN_APP_ROOT}" "config.json" "${CONFIG_JSON}"

HOST_DOMAIN="${TWOMAN_PUBLIC_ORIGIN#https://}"
HOST_DOMAIN="${HOST_DOMAIN#http://}"
HOST_DOMAIN="${HOST_DOMAIN%%/*}"

echo "Refreshing Passenger app registration..."
api_get "PassengerApps/unregister_application" \
  --data-urlencode "name=${TWOMAN_APP_NAME}" \
  --data-urlencode "path=${TWOMAN_APP_ROOT}" \
  --data-urlencode "domain=${HOST_DOMAIN}" \
  --data-urlencode "base_uri=${TWOMAN_PUBLIC_BASE_PATH}" >/dev/null || true

register_result="$(api_get "PassengerApps/register_application" \
  --data-urlencode "name=${TWOMAN_APP_NAME}" \
  --data-urlencode "path=${TWOMAN_APP_ROOT}" \
  --data-urlencode "domain=${HOST_DOMAIN}" \
  --data-urlencode "deployment_mode=production" \
  --data-urlencode "base_uri=${TWOMAN_PUBLIC_BASE_PATH}" \
  --data-urlencode "app_type=python" \
  --data-urlencode "startupfile=passenger_wsgi.py")"
echo "Passenger register response: ${register_result}"

echo "Checking Passenger broker health..."
sleep 3
health_result="$(curl -sk -H "Authorization: Bearer ${TWOMAN_CLIENT_TOKEN}" \
  "${TWOMAN_PUBLIC_ORIGIN}${TWOMAN_PUBLIC_BASE_PATH}${TWOMAN_HEALTH_TEMPLATE}")"
echo "${health_result}" | python3 - <<'PY'
import json,sys
data=json.load(sys.stdin)
if not data.get("ok"):
    raise SystemExit("Passenger health failed: %s" % (data,))
stats = data.get("stats") if isinstance(data.get("stats"), dict) else data
print(json.dumps({
    "ok": data.get("ok"),
    "peers": stats.get("peers"),
    "streams": stats.get("streams"),
    "agent_peer_label": stats.get("agent_peer_label"),
    "log_paths": stats.get("log_paths"),
}))
PY
echo
echo "Passenger host deployment complete."
