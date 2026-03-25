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
TWOMAN_AGENT_SERVICE_NAME="${TWOMAN_AGENT_SERVICE_NAME:-twoman-agent.service}"

SSH_OPTS=(-p "${TWOMAN_SERVER_PORT}" -o StrictHostKeyChecking=no)
SCP_OPTS=(-P "${TWOMAN_SERVER_PORT}" -o StrictHostKeyChecking=no)
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
  "broker_base_url": "${TWOMAN_BROKER_BASE_URL}",
  "agent_token": "${TWOMAN_AGENT_TOKEN}",
  "http_timeout_seconds": 30,
  "flush_delay_seconds": 0.01,
  "max_batch_bytes": 65536,
  "peer_id": "${TWOMAN_AGENT_PEER_ID}",
  "http2_enabled": {
    "ctl": true,
    "data": false
  }
}
EOF
)"

SERVICE_CONTENT="$(cat <<EOF
[Unit]
Description=Twoman hidden agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${TWOMAN_SERVER_DIR}
Environment=PYTHONUNBUFFERED=1
Environment=TWOMAN_TRACE=0
ExecStart=/usr/bin/python3 ${TWOMAN_SERVER_DIR}/agent.py --config ${TWOMAN_SERVER_DIR}/config.json
Restart=always
RestartSec=2
LimitNOFILE=65536

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
ExecStart=/usr/bin/python3 ${TWOMAN_SERVER_DIR}/agent_watchdog.py --service ${TWOMAN_AGENT_SERVICE_NAME} --fd-threshold 16384 --close-wait-threshold 2048
EOF
)"

echo "Installing remote config and services..."
"${SSH_CMD[@]}" "${TWOMAN_SERVER_USER}@${TWOMAN_SERVER_HOST}" "cat > '${TWOMAN_SERVER_DIR}/config.json' <<'EOF'
${CONFIG_JSON}
EOF
cat > '/etc/systemd/system/${TWOMAN_AGENT_SERVICE_NAME}' <<'EOF'
${SERVICE_CONTENT}
EOF
cat > '/etc/systemd/system/twoman-agent-watchdog.service' <<'EOF'
${WATCHDOG_SERVICE_CONTENT}
EOF
install -m 0644 '${TWOMAN_SERVER_DIR}/twoman-agent-watchdog.timer' /etc/systemd/system/twoman-agent-watchdog.timer
chmod 755 '${TWOMAN_SERVER_DIR}/install_watchdog.sh' '${TWOMAN_SERVER_DIR}/agent_watchdog.py'
python3 -m py_compile '${TWOMAN_SERVER_DIR}/agent.py' '${TWOMAN_SERVER_DIR}/agent_watchdog.py'
systemctl daemon-reload
systemctl enable --now '${TWOMAN_AGENT_SERVICE_NAME}'
systemctl enable --now twoman-agent-watchdog.timer
systemctl restart '${TWOMAN_AGENT_SERVICE_NAME}'
systemctl start twoman-agent-watchdog.service
systemctl is-active '${TWOMAN_AGENT_SERVICE_NAME}'
systemctl is-active twoman-agent-watchdog.timer
"

echo "Hidden server deployment complete."
