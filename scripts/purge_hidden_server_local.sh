#!/usr/bin/env bash
set -euo pipefail

require_env() {
  local name="$1"
  if [ -z "${!name:-}" ]; then
    echo "missing required env: ${name}" >&2
    exit 1
  fi
}

stop_disable_unit() {
  local unit_name="$1"
  systemctl stop "${unit_name}" >/dev/null 2>&1 || true
  systemctl disable "${unit_name}" >/dev/null 2>&1 || true
}

remove_unit_file() {
  local unit_name="$1"
  rm -f "/etc/systemd/system/${unit_name}"
}

require_env TWOMAN_INSTALL_ROOT
require_env TWOMAN_AGENT_SERVICE_NAME
require_env TWOMAN_WATCHDOG_SERVICE_NAME
require_env TWOMAN_WATCHDOG_TIMER_NAME

stop_disable_unit "${TWOMAN_WATCHDOG_TIMER_NAME}"
stop_disable_unit "${TWOMAN_WATCHDOG_SERVICE_NAME}"
stop_disable_unit "${TWOMAN_AGENT_SERVICE_NAME}"

remove_unit_file "${TWOMAN_WATCHDOG_TIMER_NAME}"
remove_unit_file "${TWOMAN_WATCHDOG_SERVICE_NAME}"
remove_unit_file "${TWOMAN_AGENT_SERVICE_NAME}"

systemctl daemon-reload
systemctl reset-failed "${TWOMAN_AGENT_SERVICE_NAME}" >/dev/null 2>&1 || true
systemctl reset-failed "${TWOMAN_WATCHDOG_SERVICE_NAME}" >/dev/null 2>&1 || true
systemctl reset-failed "${TWOMAN_WATCHDOG_TIMER_NAME}" >/dev/null 2>&1 || true

rm -rf "${TWOMAN_INSTALL_ROOT}"

echo "Purged hidden install ${TWOMAN_INSTALL_ROOT}"
