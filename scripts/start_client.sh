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

if [ ! -f "${CONFIG_PATH}" ]; then
  require_env TWOMAN_BROKER_BASE_URL
  require_env TWOMAN_CLIENT_TOKEN
  mkdir -p "$(dirname "${CONFIG_PATH}")"
  cat > "${CONFIG_PATH}" <<EOF
{
  "broker_base_url": "${TWOMAN_BROKER_BASE_URL}",
  "client_token": "${TWOMAN_CLIENT_TOKEN}",
  "listen_host": "${LISTEN_HOST}",
  "http_listen_port": ${HTTP_PORT},
  "socks_listen_port": ${SOCKS_PORT},
  "http_timeout_seconds": 30,
  "flush_delay_seconds": 0.01,
  "max_batch_bytes": 65536,
  "http2_enabled": {
    "ctl": false,
    "data": false
  }
}
EOF
fi

python3 -m pip install -r requirements.txt
exec python3 local_client/helper.py --config "${CONFIG_PATH}"
