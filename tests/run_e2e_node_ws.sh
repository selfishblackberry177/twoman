#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$ROOT/tests/tmp-node-ws"

rm -rf "$TMP_DIR"
mkdir -p "$TMP_DIR" "$ROOT/tests/certs"

if [ ! -d "$ROOT/host/node_selector/node_modules/ws" ]; then
  (
    cd "$ROOT/host/node_selector"
    npm ci >/dev/null
  )
fi

cat > "$TMP_DIR/broker-config.json" <<'JSON'
{
  "client_tokens": ["test-client-token"],
  "agent_tokens": ["test-agent-token"],
  "binary_media_type": "image/webp",
  "peer_ttl_seconds": 90,
  "stream_ttl_seconds": 300,
  "max_lane_bytes": 16777216,
  "max_peer_buffered_bytes": 33554432,
  "base_uri": "/api/v1/telemetry",
  "websocket_public_enabled": true
}
JSON

cat > "$TMP_DIR/agent.json" <<'JSON'
{
  "transport": "http",
  "transport_profile": "managed_host_ws",
  "broker_base_url": "http://127.0.0.1:18095/api/v1/telemetry",
  "agent_token": "test-agent-token",
  "auth_mode": "bearer",
  "legacy_custom_headers_enabled": false,
  "binary_media_type": "image/webp",
  "route_template": "/{lane}/{direction}",
  "health_template": "/health",
  "peer_id": "agent-test",
  "http_timeout_seconds": 10,
  "flush_delay_seconds": 0.01,
  "max_batch_bytes": 65536,
  "verify_tls": true
}
JSON

cat > "$TMP_DIR/helper.json" <<'JSON'
{
  "transport": "http",
  "transport_profile": "managed_host_ws",
  "broker_base_url": "http://127.0.0.1:18095/api/v1/telemetry",
  "client_token": "test-client-token",
  "auth_mode": "bearer",
  "legacy_custom_headers_enabled": false,
  "binary_media_type": "image/webp",
  "route_template": "/{lane}/{direction}",
  "health_template": "/health",
  "peer_id": "helper-test",
  "listen_host": "127.0.0.1",
  "http_listen_port": 0,
  "socks_listen_port": 0,
  "listen_state_path": "helper-listen-state.json",
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
  sleep 0.5
  for pid in "${PIDS[@]:-}"; do
    kill -9 "$pid" >/dev/null 2>&1 || true
  done
  for pid in "${PIDS[@]:-}"; do
    wait "$pid" >/dev/null 2>&1 || true
  done
  rm -rf "$TMP_DIR" "$ROOT/tests/certs" >/dev/null 2>&1 || true
}
trap cleanup EXIT
PIDS=()

PORT=18095 TWOMAN_TRACE=1 TWOMAN_DEBUG_STATS=1 TWOMAN_CONFIG_PATH="$TMP_DIR/broker-config.json" node "$ROOT/host/node_selector/broker.js" \
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

wait_for_listen_state() {
  local path="$1"
  for _ in $(seq 1 50); do
    if python3 - "$path" <<'PY' >/dev/null 2>&1
import json, sys
with open(sys.argv[1], "r", encoding="utf-8") as handle:
    payload = json.load(handle)
assert int(payload["http_port"]) > 0
assert int(payload["socks_port"]) > 0
PY
    then
      return 0
    fi
    sleep 0.2
  done
  echo "Timed out waiting for helper listen state: $path" >&2
  return 1
}

read_listen_port() {
  local path="$1"
  local key="$2"
  python3 - "$path" "$key" <<'PY'
import json, sys
with open(sys.argv[1], "r", encoding="utf-8") as handle:
    payload = json.load(handle)
print(int(payload[sys.argv[2]]))
PY
}

wait_for_port 127.0.0.1 18095 broker
wait_for_port 127.0.0.1 19090 origin
wait_for_port 127.0.0.1 19443 tls-origin

wait_for_listen_state "$TMP_DIR/helper-listen-state.json"
HELPER_HTTP_PORT="$(read_listen_port "$TMP_DIR/helper-listen-state.json" http_port)"
HELPER_SOCKS_PORT="$(read_listen_port "$TMP_DIR/helper-listen-state.json" socks_port)"
wait_for_port 127.0.0.1 "$HELPER_HTTP_PORT" http-helper
wait_for_port 127.0.0.1 "$HELPER_SOCKS_PORT" socks-helper

curl --fail --silent --show-error \
  -H "Authorization: Bearer test-client-token" \
  "http://127.0.0.1:18095/api/v1/telemetry/health" \
  > "$TMP_DIR/health.json"

python3 - "$TMP_DIR/health.json" <<'PY'
import json, sys
with open(sys.argv[1], "r", encoding="utf-8") as handle:
    payload = json.load(handle)
assert payload["capabilities"]["recommended_profile"] == "managed_host_http", payload
assert "managed_host_ws" in payload["capabilities"]["supported_profiles"], payload
PY

curl --fail --silent --show-error \
  --socks5-hostname "127.0.0.1:${HELPER_SOCKS_PORT}" \
  "http://127.0.0.1:19090/socks-test?via=ws-socks" \
  > "$TMP_DIR/socks.json"

python3 - "$TMP_DIR/socks.json" <<'PY'
import json, sys
with open(sys.argv[1], "r", encoding="utf-8") as handle:
    payload = json.load(handle)
assert payload["path"] == "/socks-test?via=ws-socks", payload
assert payload["method"] == "GET", payload
PY

curl --fail --silent --show-error --insecure \
  --proxy "http://127.0.0.1:${HELPER_HTTP_PORT}" \
  "https://127.0.0.1:19443/secure-test?via=ws-http" \
  > "$TMP_DIR/http.txt"

grep -q 'secure:/secure-test?via=ws-http' "$TMP_DIR/http.txt"

curl --fail --silent --show-error \
  -H "Authorization: Bearer test-client-token" \
  "http://127.0.0.1:18095/api/v1/telemetry/health" \
  > "$TMP_DIR/health-post.json"

python3 - "$TMP_DIR/health-post.json" <<'PY'
import json, sys
with open(sys.argv[1], "r", encoding="utf-8") as handle:
    payload = json.load(handle)
metrics = payload["metrics"]
assert metrics["ws_messages_in"]["ctl"] > 0 or metrics["ws_messages_in"]["data"] > 0, payload
assert metrics["ws_messages_out"]["ctl"] > 0 or metrics["ws_messages_out"]["data"] > 0, payload
PY

assert_nonempty() {
  local path="$1"
  if [ ! -s "$path" ]; then
    echo "Expected non-empty file: $path" >&2
    return 1
  fi
}

assert_nonempty "$TMP_DIR/logs/node-broker.log"
assert_nonempty "$TMP_DIR/logs/node-broker-events.ndjson"
assert_nonempty "$TMP_DIR/logs/agent.log"
assert_nonempty "$TMP_DIR/logs/agent-events.ndjson"
assert_nonempty "$TMP_DIR/logs/helper.log"
assert_nonempty "$TMP_DIR/logs/helper-events.ndjson"

echo "TWOMAN NODE WS E2E OK"
