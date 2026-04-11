#!/usr/bin/env bash
set -euo pipefail

require_env() {
  local name="$1"
  if [ -z "${!name:-}" ]; then
    echo "missing required env: ${name}" >&2
    exit 1
  fi
}

upload_file() {
  local source_path="$1"
  local remote_dir="$2"
  local remote_name="$3"
  curl -sk "${CURL_PROXY_ARGS[@]}" --user "${TWOMAN_CPANEL_USERNAME}:${TWOMAN_CPANEL_PASSWORD}" \
    -F "dir=${remote_dir}" \
    -F "overwrite=1" \
    -F "file-1=@${source_path};filename=${remote_name}" \
    "${TWOMAN_CPANEL_BASE_URL}/execute/Fileman/upload_files" >/dev/null
}

upload_content() {
  local remote_dir="$1"
  local remote_name="$2"
  local content="$3"
  curl -sk "${CURL_PROXY_ARGS[@]}" --user "${TWOMAN_CPANEL_USERNAME}:${TWOMAN_CPANEL_PASSWORD}" \
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
  curl -sk "${CURL_PROXY_ARGS[@]}" --user "${TWOMAN_CPANEL_USERNAME}:${TWOMAN_CPANEL_PASSWORD}" \
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
TWOMAN_UPSTREAM_PROXY_URL="${TWOMAN_UPSTREAM_PROXY_URL:-}"

CURL_PROXY_ARGS=()
if [ -n "${TWOMAN_UPSTREAM_PROXY_URL}" ]; then
  CURL_PROXY_ARGS+=(--proxy "${TWOMAN_UPSTREAM_PROXY_URL}")
fi

TWOMAN_PUBLIC_BASE_PATH="${TWOMAN_PUBLIC_BASE_PATH:-/rahkar}"
TWOMAN_BRIDGE_LOCAL_PORT="${TWOMAN_BRIDGE_LOCAL_PORT:-18093}"
TWOMAN_BRIDGE_SESSION_TTL_SECONDS="${TWOMAN_BRIDGE_SESSION_TTL_SECONDS:-300}"
TWOMAN_BRIDGE_MAX_AGENT_IDLE_SECONDS="${TWOMAN_BRIDGE_MAX_AGENT_IDLE_SECONDS:-90}"
TWOMAN_BRIDGE_MAX_STREAMS_PER_PEER_SESSION="${TWOMAN_BRIDGE_MAX_STREAMS_PER_PEER_SESSION:-256}"
TWOMAN_BRIDGE_MAX_OPEN_RATE_PER_PEER_SESSION="${TWOMAN_BRIDGE_MAX_OPEN_RATE_PER_PEER_SESSION:-120}"
TWOMAN_BRIDGE_OPEN_RATE_WINDOW_SECONDS="${TWOMAN_BRIDGE_OPEN_RATE_WINDOW_SECONDS:-10}"
TWOMAN_BRIDGE_MAX_PEER_BUFFERED_BYTES="${TWOMAN_BRIDGE_MAX_PEER_BUFFERED_BYTES:-33554432}"
TWOMAN_BRIDGE_USE_UNIX_SOCKET="${TWOMAN_BRIDGE_USE_UNIX_SOCKET:-false}"
TWOMAN_BRIDGE_LOCAL_SOCKET_PATH="${TWOMAN_BRIDGE_LOCAL_SOCKET_PATH:-${TWOMAN_CPANEL_HOME}/rahkar_runtime/bridge.sock}"
TWOMAN_BRIDGE_PUBLIC_BASE_PATH="${TWOMAN_BRIDGE_PUBLIC_BASE_PATH:-/api/v1/telemetry}"
if [ -z "${TWOMAN_BRIDGE_ROUTE_TEMPLATE:-}" ]; then
  TWOMAN_BRIDGE_ROUTE_TEMPLATE='/{lane}/{direction}'
fi
TWOMAN_BRIDGE_HEALTH_TEMPLATE="${TWOMAN_BRIDGE_HEALTH_TEMPLATE:-/health}"
TWOMAN_BRIDGE_BINARY_MEDIA_TYPE="${TWOMAN_BRIDGE_BINARY_MEDIA_TYPE:-image/webp}"

PUBLIC_BASE_TRIMMED="${TWOMAN_PUBLIC_BASE_PATH#/}"
REMOTE_BASE="${TWOMAN_CPANEL_HOME}/public_html/${PUBLIC_BASE_TRIMMED}"
REMOTE_APP_DIR="${REMOTE_BASE}/app"
REMOTE_RUNTIME_DIR="${REMOTE_BASE}/runtime"
REMOTE_STORAGE_DIR="${REMOTE_BASE}/storage"
REMOTE_OFFLOAD_DIR="${REMOTE_BASE}/offload"
RELATIVE_BASE="public_html/${PUBLIC_BASE_TRIMMED}"

mkdir -p /tmp/twoman_deploy >/dev/null 2>&1 || true

HOST_CONFIG_CONTENT="<?php

return [
    'storage_path' => '${REMOTE_STORAGE_DIR}',
    'public_base_path' => '${TWOMAN_PUBLIC_BASE_PATH}',
    'offload_relative_path' => 'offload',
    'offload_ttl_seconds' => 3600,
    'client_tokens' => [
        '${TWOMAN_CLIENT_TOKEN}',
    ],
    'agent_tokens' => [
        '${TWOMAN_AGENT_TOKEN}',
    ],
    'reverse_keys' => [
        'unused-public-release-placeholder',
    ],
    'max_request_body_bytes' => 8 * 1024 * 1024,
    'poll_wait_ms' => 20000,
    'reverse_wait_ms' => 45000,
    'poll_sleep_us' => 200000,
    'job_lease_seconds' => 30,
    'bridge_local_port' => ${TWOMAN_BRIDGE_LOCAL_PORT},
    'bridge_use_unix_socket' => ${TWOMAN_BRIDGE_USE_UNIX_SOCKET},
    'bridge_local_socket_path' => '${TWOMAN_BRIDGE_LOCAL_SOCKET_PATH}',
    'bridge_public_base_path' => '${TWOMAN_BRIDGE_PUBLIC_BASE_PATH}',
    'bridge_route_template' => '${TWOMAN_BRIDGE_ROUTE_TEMPLATE}',
    'bridge_health_template' => '${TWOMAN_BRIDGE_HEALTH_TEMPLATE}',
    'bridge_binary_media_type' => '${TWOMAN_BRIDGE_BINARY_MEDIA_TYPE}',
    'bridge_session_ttl_seconds' => ${TWOMAN_BRIDGE_SESSION_TTL_SECONDS},
    'bridge_max_agent_idle_seconds' => ${TWOMAN_BRIDGE_MAX_AGENT_IDLE_SECONDS},
    'bridge_max_streams_per_peer_session' => ${TWOMAN_BRIDGE_MAX_STREAMS_PER_PEER_SESSION},
    'bridge_max_open_rate_per_peer_session' => ${TWOMAN_BRIDGE_MAX_OPEN_RATE_PER_PEER_SESSION},
    'bridge_open_rate_window_seconds' => ${TWOMAN_BRIDGE_OPEN_RATE_WINDOW_SECONDS},
    'bridge_max_peer_buffered_bytes' => ${TWOMAN_BRIDGE_MAX_PEER_BUFFERED_BYTES},
];
"

OPS_RESTART_CONTENT="<?php
require_once __DIR__ . '/app/bridge_runtime.php';
\$pid = bridge_runtime_read_pid(bridge_runtime_daemon_pid_path());
if (\$pid > 1) { bridge_runtime_stop_pid(\$pid); }
bridge_runtime_kill_matching_processes();
bridge_runtime_start();
usleep(1500000);
echo bridge_runtime_ping() ? 'ok' : 'fail';
"

OPS_STUB_CONTENT="<?php http_response_code(404); exit;"

echo "Uploading host files..."
ensure_remote_dir "${RELATIVE_BASE}"
ensure_remote_dir "${RELATIVE_BASE}/app"
ensure_remote_dir "${RELATIVE_BASE}/runtime"
ensure_remote_dir "${RELATIVE_BASE}/storage"
ensure_remote_dir "${RELATIVE_BASE}/offload"
upload_file "runtime_diagnostics.py" "${REMOTE_BASE}" "runtime_diagnostics.py"
upload_file "twoman_http.py" "${REMOTE_BASE}" "twoman_http.py"
upload_file "twoman_protocol.py" "${REMOTE_BASE}" "twoman_protocol.py"
upload_content "${REMOTE_APP_DIR}" "bootstrap.php" "$(cat host/app/bootstrap.php)"
upload_content "${REMOTE_APP_DIR}" "bridge_runtime.php" "$(cat host/app/bridge_runtime.php)"
upload_file "host/runtime/http_broker_daemon.py" "${REMOTE_RUNTIME_DIR}" "http_broker_daemon.py"
upload_content "${REMOTE_BASE}" "api.php" "$(cat host/public/api.php)"
upload_content "${REMOTE_BASE}" "health.php" "$(cat host/public/health.php)"
upload_content "${REMOTE_APP_DIR}" "config.php" "${HOST_CONFIG_CONTENT}"

echo "Restarting broker..."
upload_content "${REMOTE_BASE}" "ops.php" "${OPS_RESTART_CONTENT}"
restart_result="$(curl -sk "${CURL_PROXY_ARGS[@]}" --connect-timeout 10 --max-time 20 "${TWOMAN_PUBLIC_ORIGIN}/${PUBLIC_BASE_TRIMMED}/ops.php" || true)"
upload_content "${REMOTE_BASE}" "ops.php" "${OPS_STUB_CONTENT}"
if [ "${restart_result}" != "ok" ]; then
  echo "broker restart failed: ${restart_result}" >&2
  exit 1
fi

echo "Checking health..."
curl -sk "${CURL_PROXY_ARGS[@]}" --connect-timeout 10 --max-time 20 -H "Authorization: Bearer ${TWOMAN_CLIENT_TOKEN}" \
  "${TWOMAN_PUBLIC_ORIGIN}/${PUBLIC_BASE_TRIMMED}/api.php?action=health"
echo
echo "Host deployment complete."
