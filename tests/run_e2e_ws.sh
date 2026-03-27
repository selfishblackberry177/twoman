#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$ROOT/tests/tmp-ws"

rm -rf "$TMP_DIR"
mkdir -p "$TMP_DIR" "$ROOT/tests/certs"

cat > "$TMP_DIR/broker-config.json" <<'JSON'
{
  "client_tokens": ["test-client-token"],
  "agent_tokens": ["test-agent-token"],
  "peer_ttl_seconds": 90,
  "stream_ttl_seconds": 300,
  "max_lane_bytes": 16777216,
  "max_peer_buffered_bytes": 33554432,
  "base_uri": ""
}
JSON

cat > "$TMP_DIR/agent.json" <<'JSON'
{
  "transport": "ws",
  "broker_base_url": "http://127.0.0.1:18094",
  "agent_token": "test-agent-token",
  "peer_id": "agent-test",
  "http_timeout_seconds": 10,
  "flush_delay_seconds": 0.01,
  "max_batch_bytes": 65536,
  "verify_tls": true
}
JSON

cat > "$TMP_DIR/helper.json" <<'JSON'
{
  "transport": "ws",
  "broker_base_url": "http://127.0.0.1:18094",
  "client_token": "test-client-token",
  "peer_id": "helper-test",
  "listen_host": "127.0.0.1",
  "http_listen_port": 28081,
  "socks_listen_port": 21081,
  "http_timeout_seconds": 10,
  "flush_delay_seconds": 0.01,
  "max_batch_bytes": 65536,
  "verify_tls": true
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

(cd "$ROOT/host/node_selector" && npm install --silent ws >/dev/null)

PORT=18094 TWOMAN_CONFIG_PATH="$TMP_DIR/broker-config.json" node "$ROOT/host/node_selector/broker.js" \
  >"$TMP_DIR/broker.log" 2>&1 &
PIDS+=($!)

python3 "$ROOT/tests/origin_server.py" >"$TMP_DIR/origin.log" 2>&1 &
PIDS+=($!)

python3 "$ROOT/tests/tls_origin_server.py" >"$TMP_DIR/tls.log" 2>&1 &
PIDS+=($!)

python3 "$ROOT/hidden_server/agent.py" --config "$TMP_DIR/agent.json" >"$TMP_DIR/agent.log" 2>&1 &
PIDS+=($!)

python3 "$ROOT/local_client/helper.py" --config "$TMP_DIR/helper.json" >"$TMP_DIR/helper.log" 2>&1 &
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
  for file in broker.log agent.log helper.log origin.log tls.log; do
    [ -f "$TMP_DIR/$file" ] && echo "== $file ==" >&2 && cat "$TMP_DIR/$file" >&2 || true
  done
  return 1
}

wait_for_port 127.0.0.1 18094 broker
wait_for_port 127.0.0.1 19090 origin
wait_for_port 127.0.0.1 19443 tls-origin
wait_for_port 127.0.0.1 28081 http-helper
wait_for_port 127.0.0.1 21081 socks-helper

curl --fail --silent --show-error \
  --socks5-hostname "127.0.0.1:21081" \
  "http://127.0.0.1:19090/socks-test?via=socks" \
  > "$TMP_DIR/socks.json"

python3 - "$TMP_DIR/socks.json" <<'PY'
import json, sys
with open(sys.argv[1], "r", encoding="utf-8") as handle:
    payload = json.load(handle)
assert payload["path"] == "/socks-test?via=socks", payload
assert payload["method"] == "GET", payload
PY

curl --fail --silent --show-error --insecure \
  --proxy "http://127.0.0.1:28081" \
  "https://127.0.0.1:19443/secure-test?via=http" \
  > "$TMP_DIR/http.txt"

grep -q 'secure:/secure-test?via=http' "$TMP_DIR/http.txt"

echo "TWOMAN WS E2E OK"
