#!/usr/bin/env bash
set -euo pipefail

require_env() {
  local name="$1"
  if [ -z "${!name:-}" ]; then
    echo "missing required env: ${name}" >&2
    exit 1
  fi
}

require_env TWOMAN_SERVER_HOST
require_env TWOMAN_SERVER_USER
require_env TWOMAN_BROKER_BASE_URL
require_env TWOMAN_AGENT_TOKEN

TWOMAN_SERVER_PORT="${TWOMAN_SERVER_PORT:-22}"
TWOMAN_SERVER_DIR="${TWOMAN_SERVER_DIR:-/opt/twoman}"
TWOMAN_AGENT_PEER_ID="${TWOMAN_AGENT_PEER_ID:-agent-main}"
TWOMAN_SERVER_PASSWORD="${TWOMAN_SERVER_PASSWORD:-}"
TWOMAN_SERVER_SSH_KEY="${TWOMAN_SERVER_SSH_KEY:-}"
TWOMAN_AGENT_SERVICE_NAME="${TWOMAN_AGENT_SERVICE_NAME:-twoman-agent.service}"
TWOMAN_VERIFY_TLS="${TWOMAN_VERIFY_TLS:-true}"
TWOMAN_HTTP2_CTL="${TWOMAN_HTTP2_CTL:-false}"
TWOMAN_HTTP2_DATA="${TWOMAN_HTTP2_DATA:-false}"
TWOMAN_TRANSPORT="${TWOMAN_TRANSPORT:-http}"
TWOMAN_STREAMING_UP_LANES="${TWOMAN_STREAMING_UP_LANES:-}"
TWOMAN_TRACE="${TWOMAN_TRACE:-0}"
TWOMAN_IDLE_REPOLL_CTL="${TWOMAN_IDLE_REPOLL_CTL:-0.05}"
TWOMAN_IDLE_REPOLL_DATA="${TWOMAN_IDLE_REPOLL_DATA:-0.1}"
TWOMAN_DOWN_READ_TIMEOUT_SECONDS="${TWOMAN_DOWN_READ_TIMEOUT_SECONDS:-10}"
TWOMAN_DOWN_STREAM_MAX_SESSION_SECONDS="${TWOMAN_DOWN_STREAM_MAX_SESSION_SECONDS:-60}"
TWOMAN_DATA_UP_MAX_BATCH_BYTES="${TWOMAN_DATA_UP_MAX_BATCH_BYTES:-131072}"
TWOMAN_DATA_UP_FLUSH_DELAY_SECONDS="${TWOMAN_DATA_UP_FLUSH_DELAY_SECONDS:-0.006}"
TWOMAN_OPEN_CONNECT_TIMEOUT_SECONDS="${TWOMAN_OPEN_CONNECT_TIMEOUT_SECONDS:-12}"
TWOMAN_PREFER_IPV4="${TWOMAN_PREFER_IPV4:-true}"
TWOMAN_DISABLE_IPV6_ORIGIN="${TWOMAN_DISABLE_IPV6_ORIGIN:-false}"
TWOMAN_HAPPY_EYEBALLS_DELAY_SECONDS="${TWOMAN_HAPPY_EYEBALLS_DELAY_SECONDS:-0.25}"
TWOMAN_UPSTREAM_PROXY_URL="${TWOMAN_UPSTREAM_PROXY_URL:-}"
TWOMAN_UPSTREAM_PROXY_LABEL="${TWOMAN_UPSTREAM_PROXY_LABEL:-}"
TWOMAN_AUTH_MODE="${TWOMAN_AUTH_MODE:-bearer}"
TWOMAN_LEGACY_CUSTOM_HEADERS_ENABLED="${TWOMAN_LEGACY_CUSTOM_HEADERS_ENABLED:-false}"
TWOMAN_BINARY_MEDIA_TYPE="${TWOMAN_BINARY_MEDIA_TYPE:-image/webp}"
if [ -z "${TWOMAN_ROUTE_TEMPLATE:-}" ]; then
  TWOMAN_ROUTE_TEMPLATE='/{lane}/{direction}'
fi
TWOMAN_HEALTH_TEMPLATE="${TWOMAN_HEALTH_TEMPLATE:-/health}"
TWOMAN_AGENT_SERVICE_USER="${TWOMAN_AGENT_SERVICE_USER:-twoman}"
TWOMAN_AGENT_SERVICE_GROUP="${TWOMAN_AGENT_SERVICE_GROUP:-twoman}"

STREAMING_UP_JSON="[]"
if [ -n "${TWOMAN_STREAMING_UP_LANES}" ]; then
  STREAMING_UP_JSON="$(python3 - <<'PY'
import json, os
values=[item.strip() for item in os.environ["TWOMAN_STREAMING_UP_LANES"].split(",") if item.strip()]
print(json.dumps(values))
PY
)"
fi

UPSTREAM_PROXY_JSON="null"
if [ -n "${TWOMAN_UPSTREAM_PROXY_URL}" ]; then
  UPSTREAM_PROXY_JSON="$(python3 - <<'PY'
import json, os
print(json.dumps(os.environ["TWOMAN_UPSTREAM_PROXY_URL"]))
PY
)"
fi

SYSTEMD_AFTER="After=network-online.target"
SYSTEMD_WANTS="Wants=network-online.target"
if [ "${TWOMAN_UPSTREAM_PROXY_LABEL}" = "wireproxy" ]; then
  SYSTEMD_AFTER="After=network-online.target wireproxy.service"
  SYSTEMD_WANTS="Wants=network-online.target wireproxy.service"
fi

SSH_OPTS=(-p "${TWOMAN_SERVER_PORT}" -o StrictHostKeyChecking=no)
SCP_OPTS=(-P "${TWOMAN_SERVER_PORT}" -o StrictHostKeyChecking=no)
if [ -n "${TWOMAN_SERVER_SSH_KEY}" ]; then
  SSH_OPTS+=(-i "${TWOMAN_SERVER_SSH_KEY}")
  SCP_OPTS+=(-i "${TWOMAN_SERVER_SSH_KEY}")
fi
SCP_CMD=(scp "${SCP_OPTS[@]}")
SSH_CMD=(ssh "${SSH_OPTS[@]}")
if [ -n "${TWOMAN_SERVER_PASSWORD}" ]; then
  SCP_CMD=(sshpass -p "${TWOMAN_SERVER_PASSWORD}" "${SCP_CMD[@]}")
  SSH_CMD=(sshpass -p "${TWOMAN_SERVER_PASSWORD}" "${SSH_CMD[@]}")
fi

echo "Creating remote directory..."
"${SSH_CMD[@]}" "${TWOMAN_SERVER_USER}@${TWOMAN_SERVER_HOST}" "mkdir -p '${TWOMAN_SERVER_DIR}/systemd'"

echo "Uploading agent files..."
"${SCP_CMD[@]}" \
  requirements.txt \
  runtime_diagnostics.py \
  twoman_crypto.py \
  twoman_dns.py \
  twoman_http.py \
  twoman_protocol.py \
  twoman_transport.py \
  hidden_server/agent.py \
  hidden_server/agent_watchdog.py \
  hidden_server/install_watchdog.sh \
  hidden_server/systemd/twoman-agent-watchdog.service \
  hidden_server/systemd/twoman-agent-watchdog.timer \
  "${TWOMAN_SERVER_USER}@${TWOMAN_SERVER_HOST}:${TWOMAN_SERVER_DIR}/"

CONFIG_JSON="$(cat <<EOF
{
  "transport": "${TWOMAN_TRANSPORT}",
  "transport_profile": "auto",
  "broker_base_url": "${TWOMAN_BROKER_BASE_URL}",
  "upstream_proxy_url": ${UPSTREAM_PROXY_JSON},
  "agent_token": "${TWOMAN_AGENT_TOKEN}",
  "auth_mode": "${TWOMAN_AUTH_MODE}",
  "legacy_custom_headers_enabled": ${TWOMAN_LEGACY_CUSTOM_HEADERS_ENABLED},
  "binary_media_type": "${TWOMAN_BINARY_MEDIA_TYPE}",
  "route_template": "${TWOMAN_ROUTE_TEMPLATE}",
  "health_template": "${TWOMAN_HEALTH_TEMPLATE}",
  "http_timeout_seconds": 30,
  "heartbeat_interval_seconds": 15,
  "interval_jitter_ratio": 0.2,
  "down_read_timeout_seconds": ${TWOMAN_DOWN_READ_TIMEOUT_SECONDS},
  "down_stream_max_session_seconds": ${TWOMAN_DOWN_STREAM_MAX_SESSION_SECONDS},
  "backoff_initial_delay_seconds": 0.1,
  "backoff_max_delay_seconds": 5,
  "flush_delay_seconds": 0.01,
  "max_batch_bytes": 65536,
  "verify_tls": ${TWOMAN_VERIFY_TLS},
  "peer_id": "${TWOMAN_AGENT_PEER_ID}",
  "open_connect_timeout_seconds": ${TWOMAN_OPEN_CONNECT_TIMEOUT_SECONDS},
  "prefer_ipv4": ${TWOMAN_PREFER_IPV4},
  "disable_ipv6_origin": ${TWOMAN_DISABLE_IPV6_ORIGIN},
  "happy_eyeballs_delay_seconds": ${TWOMAN_HAPPY_EYEBALLS_DELAY_SECONDS},
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
)"

SERVICE_CONTENT="$(cat <<EOF
[Unit]
Description=Twoman hidden agent
${SYSTEMD_AFTER}
${SYSTEMD_WANTS}

[Service]
Type=simple
WorkingDirectory=${TWOMAN_SERVER_DIR}
User=${TWOMAN_AGENT_SERVICE_USER}
Group=${TWOMAN_AGENT_SERVICE_GROUP}
Environment=PYTHONUNBUFFERED=1
Environment=TWOMAN_TRACE=${TWOMAN_TRACE}
ExecStart=${TWOMAN_SERVER_DIR}/.venv/bin/python ${TWOMAN_SERVER_DIR}/agent.py --config ${TWOMAN_SERVER_DIR}/config.json
Restart=always
RestartSec=2
LimitNOFILE=65536
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=${TWOMAN_SERVER_DIR}
UMask=0077
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
)"

WATCHDOG_SERVICE_CONTENT="$(cat <<EOF
[Unit]
Description=Twoman agent watchdog
After=network-online.target

[Service]
Type=oneshot
ExecStart=${TWOMAN_SERVER_DIR}/.venv/bin/python ${TWOMAN_SERVER_DIR}/agent_watchdog.py --service ${TWOMAN_AGENT_SERVICE_NAME} --fd-threshold 16384 --close-wait-threshold 2048
EOF
)"

echo "Installing remote config and services..."
"${SSH_CMD[@]}" "${TWOMAN_SERVER_USER}@${TWOMAN_SERVER_HOST}" "cat > '${TWOMAN_SERVER_DIR}/config.json' <<'EOF'
${CONFIG_JSON}
EOF
getent group '${TWOMAN_AGENT_SERVICE_GROUP}' >/dev/null 2>&1 || groupadd --system '${TWOMAN_AGENT_SERVICE_GROUP}'
id -u '${TWOMAN_AGENT_SERVICE_USER}' >/dev/null 2>&1 || useradd --system --gid '${TWOMAN_AGENT_SERVICE_GROUP}' --home-dir '${TWOMAN_SERVER_DIR}' --shell /usr/sbin/nologin '${TWOMAN_AGENT_SERVICE_USER}'
chown -R '${TWOMAN_AGENT_SERVICE_USER}:${TWOMAN_AGENT_SERVICE_GROUP}' '${TWOMAN_SERVER_DIR}'
cat > '/etc/systemd/system/${TWOMAN_AGENT_SERVICE_NAME}' <<'EOF'
${SERVICE_CONTENT}
EOF
cat > '/etc/systemd/system/twoman-agent-watchdog.service' <<'EOF'
${WATCHDOG_SERVICE_CONTENT}
EOF
install -m 0644 '${TWOMAN_SERVER_DIR}/twoman-agent-watchdog.timer' /etc/systemd/system/twoman-agent-watchdog.timer
chmod 755 '${TWOMAN_SERVER_DIR}/install_watchdog.sh' '${TWOMAN_SERVER_DIR}/agent_watchdog.py'
if ! python3 -m venv --help >/dev/null 2>&1; then
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y python3-venv
  else
    echo 'unable to bootstrap python virtual environments automatically' >&2
    exit 1
  fi
fi
if [ ! -d '${TWOMAN_SERVER_DIR}/.venv' ]; then
  python3 -m venv '${TWOMAN_SERVER_DIR}/.venv'
fi
'${TWOMAN_SERVER_DIR}/.venv/bin/python' -m pip install --no-input -r '${TWOMAN_SERVER_DIR}/requirements.txt'
'${TWOMAN_SERVER_DIR}/.venv/bin/python' -m py_compile '${TWOMAN_SERVER_DIR}/agent.py' '${TWOMAN_SERVER_DIR}/agent_watchdog.py'
systemctl daemon-reload
systemctl enable --now '${TWOMAN_AGENT_SERVICE_NAME}'
systemctl enable --now twoman-agent-watchdog.timer
systemctl restart '${TWOMAN_AGENT_SERVICE_NAME}'
systemctl start twoman-agent-watchdog.service
systemctl is-active '${TWOMAN_AGENT_SERVICE_NAME}'
systemctl is-active twoman-agent-watchdog.timer
"

echo "Hidden server deployment complete."
