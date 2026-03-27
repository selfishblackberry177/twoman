#!/usr/bin/env bash
set -euo pipefail

require_env() {
  local name="$1"
  if [ -z "${!name:-}" ]; then
    echo "missing required env: ${name}" >&2
    exit 1
  fi
}

CONFIG_PATH="${1:-local_client/config.json}"
LISTEN_HOST="${TWOMAN_LISTEN_HOST:-127.0.0.1}"
HTTP_PORT="${TWOMAN_HTTP_PORT:-18092}"
SOCKS_PORT="${TWOMAN_SOCKS_PORT:-11092}"
VERIFY_TLS="${TWOMAN_VERIFY_TLS:-true}"
TWOMAN_HTTP2_CTL="${TWOMAN_HTTP2_CTL:-true}"
TWOMAN_HTTP2_DATA="${TWOMAN_HTTP2_DATA:-false}"
TWOMAN_TRANSPORT="${TWOMAN_TRANSPORT:-http}"
TWOMAN_STREAMING_UP_LANES="${TWOMAN_STREAMING_UP_LANES:-}"
TWOMAN_TRACE="${TWOMAN_TRACE:-0}"
TWOMAN_LOG_DIR="${TWOMAN_LOG_DIR:-local_client/logs}"
TWOMAN_LOG_PATH="${TWOMAN_LOG_PATH:-${TWOMAN_LOG_DIR%/}/helper.log}"
TWOMAN_IDLE_REPOLL_CTL="${TWOMAN_IDLE_REPOLL_CTL:-0.05}"
TWOMAN_IDLE_REPOLL_DATA="${TWOMAN_IDLE_REPOLL_DATA:-0.1}"
TWOMAN_DATA_UP_MAX_BATCH_BYTES="${TWOMAN_DATA_UP_MAX_BATCH_BYTES:-65536}"
TWOMAN_DATA_UP_FLUSH_DELAY_SECONDS="${TWOMAN_DATA_UP_FLUSH_DELAY_SECONDS:-0.004}"

STREAMING_UP_JSON="[]"
if [ -n "${TWOMAN_STREAMING_UP_LANES}" ]; then
  STREAMING_UP_JSON="$(python3 - <<'PY'
import json, os
values=[item.strip() for item in os.environ["TWOMAN_STREAMING_UP_LANES"].split(",") if item.strip()]
print(json.dumps(values))
PY
)"
fi

if [ ! -f "${CONFIG_PATH}" ]; then
  require_env TWOMAN_BROKER_BASE_URL
  require_env TWOMAN_CLIENT_TOKEN
  mkdir -p "$(dirname "${CONFIG_PATH}")"
  mkdir -p "$(dirname "${TWOMAN_LOG_PATH}")"
  cat > "${CONFIG_PATH}" <<EOF
{
  "transport": "${TWOMAN_TRANSPORT}",
  "broker_base_url": "${TWOMAN_BROKER_BASE_URL}",
  "client_token": "${TWOMAN_CLIENT_TOKEN}",
  "listen_host": "${LISTEN_HOST}",
  "http_listen_port": ${HTTP_PORT},
  "socks_listen_port": ${SOCKS_PORT},
  "http_timeout_seconds": 30,
  "flush_delay_seconds": 0.01,
  "max_batch_bytes": 65536,
  "log_path": "${TWOMAN_LOG_PATH}",
  "verify_tls": ${VERIFY_TLS},
  "upload_profiles": {
    "data": {
      "max_batch_bytes": ${TWOMAN_DATA_UP_MAX_BATCH_BYTES},
      "flush_delay_seconds": ${TWOMAN_DATA_UP_FLUSH_DELAY_SECONDS}
    }
  },
  "streaming_up_lanes": ${STREAMING_UP_JSON},
  "idle_repoll_delay_seconds": {
    "ctl": ${TWOMAN_IDLE_REPOLL_CTL},
    "data": ${TWOMAN_IDLE_REPOLL_DATA}
  },
  "http2_enabled": {
    "ctl": ${TWOMAN_HTTP2_CTL},
    "data": ${TWOMAN_HTTP2_DATA}
  }
}
EOF
fi

python3 -m pip install -r requirements.txt
mkdir -p "$(dirname "${TWOMAN_LOG_PATH}")"
echo "Twoman helper log: ${TWOMAN_LOG_PATH}" >&2
exec env \
  TWOMAN_TRACE="${TWOMAN_TRACE}" \
  TWOMAN_LOG_PATH="${TWOMAN_LOG_PATH}" \
  python3 local_client/helper.py --config "${CONFIG_PATH}"
