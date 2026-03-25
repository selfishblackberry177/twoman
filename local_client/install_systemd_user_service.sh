#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
CONFIG_PATH="${1:-${SCRIPT_DIR}/config.json}"
UNIT_DIR="${HOME}/.config/systemd/user"
UNIT_PATH="${UNIT_DIR}/twoman-helper.service"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3)}"

mkdir -p "${UNIT_DIR}"

cat > "${UNIT_PATH}" <<EOF
[Unit]
Description=Twoman local helper
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${REPO_DIR}
Environment=PYTHONUNBUFFERED=1
ExecStart=${PYTHON_BIN} ${SCRIPT_DIR}/helper.py --config ${CONFIG_PATH}
Restart=always
RestartSec=2
KillMode=control-group

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now twoman-helper.service
systemctl --user status --no-pager twoman-helper.service
