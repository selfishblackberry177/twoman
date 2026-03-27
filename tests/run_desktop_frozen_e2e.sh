#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BINARY="${TWOMAN_DESKTOP_BINARY:-$ROOT/desktop_client/dist/linux/twoman-desktop}"
TMP_DIR="$ROOT/tests/tmp-desktop-frozen"

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
  "http_listen_port": 28082,
  "socks_listen_port": 21082,
  "http_timeout_seconds": 10,
  "flush_delay_seconds": 0.01,
  "max_batch_bytes": 65536,
  "http2_enabled": {
    "ctl": false,
    "data": false
  }
}
JSON

cat > "$TMP_DIR/share.json" <<'JSON'
{
  "name": "Frozen share",
  "listen_host": "127.0.0.1",
  "listen_port": 31082,
  "target_host": "127.0.0.1",
  "target_port": 21082,
  "username": "frozen-user",
  "password": "frozen-pass",
  "log_path": "tests/tmp-desktop-frozen/share.log"
}
JSON

openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout "$ROOT/tests/certs/localhost-key.pem" \
  -out "$ROOT/tests/certs/localhost.pem" \
  -subj "/CN=localhost" \
  -days 1 >/dev/null 2>&1

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

"$BINARY" helper-run --config "$TMP_DIR/helper.json" >"$TMP_DIR/helper.out" 2>&1 &
PIDS+=($!)

"$BINARY" gateway-run --config "$TMP_DIR/share.json" >"$TMP_DIR/share.out" 2>&1 &
PIDS+=($!)

wait_for_port() {
  local host="$1"
  local port="$2"
  local label="$3"
  for _ in $(seq 1 50); do
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
    sleep 0.2
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
wait_for_port 127.0.0.1 28082 frozen-http
wait_for_port 127.0.0.1 21082 frozen-socks
wait_for_port 127.0.0.1 31082 frozen-share

curl --fail --silent --show-error \
  --proxy "socks5h://frozen-user:frozen-pass@127.0.0.1:31082" \
  "http://127.0.0.1:19090/socks-test?via=frozen" \
  > "$TMP_DIR/frozen.json"

python3 - "$TMP_DIR/frozen.json" <<'PY'
import json, sys
with open(sys.argv[1], "r", encoding="utf-8") as handle:
    payload = json.load(handle)
assert payload["path"] == "/socks-test?via=frozen", payload
assert payload["method"] == "GET", payload
PY

echo "TWOMAN FROZEN DESKTOP E2E OK"
