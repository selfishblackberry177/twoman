#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXE="${TWOMAN_WINDOWS_DESKTOP_EXE:-$ROOT/desktop_client/dist/windows/twoman-desktop.exe}"
TMP_DIR="$ROOT/tests/tmp-desktop-windows-frozen"

rm -rf "$TMP_DIR"
mkdir -p "$TMP_DIR" "$ROOT/tests/certs"

cat > "$TMP_DIR/broker-config.json" <<'JSON'
{
  "client_tokens": ["test-client-token"],
  "agent_tokens": ["test-agent-token"],
  "session_ttl_seconds": 300,
  "peer_ttl_seconds": 90,
  "stream_ttl_seconds": 300,
  "max_lane_bytes": 16777216
}
JSON

cat > "$TMP_DIR/agent.json" <<'JSON'
{
  "broker_base_url": "http://127.0.0.1:18093",
  "agent_token": "test-agent-token",
  "peer_id": "agent-test",
  "http_timeout_seconds": 10,
  "flush_delay_seconds": 0.01,
  "max_batch_bytes": 65536,
  "http2_enabled": {
    "ctl": false,
    "data": false
  }
}
JSON

cat > "$TMP_DIR/helper.json" <<'JSON'
{
  "broker_base_url": "http://127.0.0.1:18093",
  "client_token": "test-client-token",
  "listen_host": "127.0.0.1",
  "http_listen_port": 28083,
  "socks_listen_port": 21083,
  "http_timeout_seconds": 10,
  "flush_delay_seconds": 0.01,
  "max_batch_bytes": 65536,
  "http2_enabled": {
    "ctl": false,
    "data": false
  }
}
JSON

openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout "$ROOT/tests/certs/localhost-key.pem" \
  -out "$ROOT/tests/certs/localhost.pem" \
  -subj "/CN=localhost" \
  -days 1 >/dev/null 2>&1

TMP_DIR_WIN="$(winepath -w "$TMP_DIR")"
HELPER_CONFIG_WIN="$(winepath -w "$TMP_DIR/helper.json")"
EXE_WIN="$(winepath -w "$EXE")"
HELPER_OUT_WIN="${TMP_DIR_WIN}\\helper.out"
SHARE_OUT_WIN="${TMP_DIR_WIN}\\share.out"
HELPER_CMD_WIN="${TMP_DIR_WIN}\\run-helper.cmd"
SHARE_CMD_WIN="${TMP_DIR_WIN}\\run-share.cmd"

cat > "$TMP_DIR/share.json" <<JSON
{
  "name": "Windows frozen share",
  "listen_host": "127.0.0.1",
  "listen_port": 31083,
  "target_host": "127.0.0.1",
  "target_port": 21083,
  "username": "wine-user",
  "password": "wine-pass",
  "log_path": "${TMP_DIR_WIN//\\/\\\\}\\\\share.log"
}
JSON

SHARE_CONFIG_WIN="$(winepath -w "$TMP_DIR/share.json")"

cat > "$TMP_DIR/run-helper.cmd" <<CMD
@echo off
"$EXE_WIN" helper-run --config "$HELPER_CONFIG_WIN" > "$HELPER_OUT_WIN" 2>&1
CMD

cat > "$TMP_DIR/run-share.cmd" <<CMD
@echo off
"$EXE_WIN" gateway-run --config "$SHARE_CONFIG_WIN" > "$SHARE_OUT_WIN" 2>&1
CMD

cleanup() {
  for pid in "${PIDS[@]:-}"; do
    kill "$pid" >/dev/null 2>&1 || true
  done
  rm -rf "$TMP_DIR" "$ROOT/tests/certs"
}
trap cleanup EXIT
PIDS=()

PYTHONPATH="$ROOT" python3 "$ROOT/host/runtime/http_broker_daemon.py" \
  --listen-host 127.0.0.1 \
  --listen-port 18093 \
  --config "$TMP_DIR/broker-config.json" \
  >"$TMP_DIR/broker.log" 2>&1 &
PIDS+=($!)

python3 "$ROOT/tests/origin_server.py" >"$TMP_DIR/origin.log" 2>&1 &
PIDS+=($!)

python3 "$ROOT/tests/tls_origin_server.py" >"$TMP_DIR/tls.log" 2>&1 &
PIDS+=($!)

python3 "$ROOT/hidden_server/agent.py" --config "$TMP_DIR/agent.json" >"$TMP_DIR/agent.log" 2>&1 &
PIDS+=($!)

wine cmd /c "$HELPER_CMD_WIN" &
PIDS+=($!)

wine cmd /c "$SHARE_CMD_WIN" &
PIDS+=($!)

wait_for_port() {
  local host="$1"
  local port="$2"
  local label="$3"
  for _ in $(seq 1 80); do
    if python3 - "$host" "$port" <<'PY' >/dev/null 2>&1
import socket
import sys
sock = socket.socket()
sock.settimeout(0.2)
try:
    sock.connect((sys.argv[1], int(sys.argv[2])))
finally:
    sock.close()
PY
    then
      return 0
    fi
    sleep 0.25
  done
  echo "Timed out waiting for $label on $host:$port" >&2
  for file in broker.log agent.log origin.log tls.log helper.out share.out; do
    [ -f "$TMP_DIR/$file" ] && echo "---- $file ----" >&2 && cat "$TMP_DIR/$file" >&2 || true
  done
  return 1
}

wait_for_port 127.0.0.1 18093 broker
wait_for_port 127.0.0.1 19090 origin
wait_for_port 127.0.0.1 19443 tls-origin
wait_for_port 127.0.0.1 28083 win-http
wait_for_port 127.0.0.1 21083 win-socks
wait_for_port 127.0.0.1 31083 win-share

curl --fail --silent --show-error \
  --proxy "socks5h://wine-user:wine-pass@127.0.0.1:31083" \
  "http://127.0.0.1:19090/socks-test?via=windows-frozen" \
  > "$TMP_DIR/windows-frozen.json"

python3 - "$TMP_DIR/windows-frozen.json" <<'PY'
import json, sys
with open(sys.argv[1], "r", encoding="utf-8") as handle:
    payload = json.load(handle)
assert payload["path"] == "/socks-test?via=windows-frozen", payload
assert payload["method"] == "GET", payload
PY

echo "TWOMAN WINDOWS FROZEN DESKTOP E2E OK"
