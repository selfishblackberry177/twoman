#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="${TWOMAN_REMOTE_E2E_TMP_DIR:-$ROOT/tests/tmp-remote-e2e}"

require_env() {
  local name="$1"
  if [ -z "${!name:-}" ]; then
    echo "missing required env: ${name}" >&2
    exit 1
  fi
}

require_env TWOMAN_BROKER_BASE_URL
require_env TWOMAN_CLIENT_TOKEN
require_env TWOMAN_AGENT_TOKEN

TWOMAN_HELPER_PEER_ID="${TWOMAN_HELPER_PEER_ID:-helper-remote-e2e}"
TWOMAN_AGENT_PEER_ID="${TWOMAN_AGENT_PEER_ID:-agent-remote-e2e}"
if [ -z "${TWOMAN_ROUTE_TEMPLATE:-}" ]; then
  TWOMAN_ROUTE_TEMPLATE='/{lane}/{direction}'
fi
TWOMAN_HEALTH_TEMPLATE="${TWOMAN_HEALTH_TEMPLATE:-/health}"
TWOMAN_VERIFY_TLS="${TWOMAN_VERIFY_TLS:-true}"
TWOMAN_REMOTE_E2E_KEEP_TMP="${TWOMAN_REMOTE_E2E_KEEP_TMP:-0}"

rm -rf "$TMP_DIR"
mkdir -p "$TMP_DIR" "$ROOT/tests/certs"

cat > "$TMP_DIR/agent.json" <<EOF
{
  "transport": "http",
  "broker_base_url": "${TWOMAN_BROKER_BASE_URL}",
  "agent_token": "${TWOMAN_AGENT_TOKEN}",
  "auth_mode": "bearer",
  "legacy_custom_headers_enabled": false,
  "binary_media_type": "image/webp",
  "route_template": "${TWOMAN_ROUTE_TEMPLATE}",
  "health_template": "${TWOMAN_HEALTH_TEMPLATE}",
  "peer_id": "${TWOMAN_AGENT_PEER_ID}",
  "http_timeout_seconds": 30,
  "flush_delay_seconds": 0.01,
  "max_batch_bytes": 65536,
  "verify_tls": ${TWOMAN_VERIFY_TLS},
  "http2_enabled": {
    "ctl": false,
    "data": false
  }
}
EOF

cat > "$TMP_DIR/helper.json" <<EOF
{
  "transport": "http",
  "broker_base_url": "${TWOMAN_BROKER_BASE_URL}",
  "client_token": "${TWOMAN_CLIENT_TOKEN}",
  "auth_mode": "bearer",
  "legacy_custom_headers_enabled": false,
  "binary_media_type": "image/webp",
  "route_template": "${TWOMAN_ROUTE_TEMPLATE}",
  "health_template": "${TWOMAN_HEALTH_TEMPLATE}",
  "peer_id": "${TWOMAN_HELPER_PEER_ID}",
  "listen_host": "127.0.0.1",
  "http_listen_port": 0,
  "socks_listen_port": 0,
  "listen_state_path": "helper-listen-state.json",
  "http_timeout_seconds": 30,
  "flush_delay_seconds": 0.01,
  "max_batch_bytes": 65536,
  "verify_tls": ${TWOMAN_VERIFY_TLS},
  "http2_enabled": {
    "ctl": false,
    "data": false
  }
}
EOF

openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout "$ROOT/tests/certs/localhost-key.pem" \
  -out "$ROOT/tests/certs/localhost.pem" \
  -subj "/CN=localhost" \
  -days 1 >/dev/null 2>&1

cleanup() {
  local exit_code="${1:-0}"
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
  if [ "$exit_code" -ne 0 ]; then
    for file in agent.log helper.log origin.log tls.log; do
      if [ -f "$TMP_DIR/$file" ]; then
        echo "== $file ==" >&2
        cat "$TMP_DIR/$file" >&2
      fi
    done
  fi
  if [ "$TWOMAN_REMOTE_E2E_KEEP_TMP" != "1" ]; then
    rm -rf "$TMP_DIR" "$ROOT/tests/certs" >/dev/null 2>&1 || true
  fi
}
trap 'cleanup $?' EXIT
PIDS=()

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
  for _ in $(seq 1 100); do
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
  return 1
}

wait_for_listen_state() {
  local path="$1"
  for _ in $(seq 1 100); do
    if python3 - "$path" <<'PY' >/dev/null 2>&1
import json
import sys

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
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as handle:
    payload = json.load(handle)
print(int(payload[sys.argv[2]]))
PY
}

wait_for_remote_peers() {
  local expected_agent_label="$1"
  local health_url="${TWOMAN_BROKER_BASE_URL%/}${TWOMAN_HEALTH_TEMPLATE}"
  local response_file="$TMP_DIR/health.json"
  for _ in $(seq 1 120); do
    if curl --fail --silent --show-error --max-time 10 \
      -H "Authorization: Bearer ${TWOMAN_CLIENT_TOKEN}" \
      "$health_url" > "$response_file" 2>/dev/null
    then
      if python3 - "$response_file" "$expected_agent_label" <<'PY' >/dev/null 2>&1
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as handle:
    payload = json.load(handle)
stats = payload.get("stats") if isinstance(payload.get("stats"), dict) else payload
assert payload.get("ok") is True
assert int(stats.get("peers", 0)) >= 2
assert str(stats.get("agent_peer_label", "")) == sys.argv[2]
PY
      then
        return 0
      fi
    fi
    sleep 0.5
  done
  echo "Timed out waiting for remote peers on ${health_url}" >&2
  if [ -f "$response_file" ]; then
    cat "$response_file" >&2
  fi
  return 1
}

wait_for_port 127.0.0.1 19090 origin
wait_for_port 127.0.0.1 19443 tls-origin
wait_for_listen_state "$TMP_DIR/helper-listen-state.json"
HELPER_HTTP_PORT="$(read_listen_port "$TMP_DIR/helper-listen-state.json" http_port)"
HELPER_SOCKS_PORT="$(read_listen_port "$TMP_DIR/helper-listen-state.json" socks_port)"
wait_for_port 127.0.0.1 "$HELPER_HTTP_PORT" http-helper
wait_for_port 127.0.0.1 "$HELPER_SOCKS_PORT" socks-helper
wait_for_remote_peers "$TWOMAN_AGENT_PEER_ID"

curl --fail --silent --show-error --max-time 30 \
  --socks5-hostname "127.0.0.1:${HELPER_SOCKS_PORT}" \
  "http://127.0.0.1:19090/socks-test?via=socks" \
  > "$TMP_DIR/socks.json"

python3 - "$TMP_DIR/socks.json" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as handle:
    payload = json.load(handle)
assert payload["path"] == "/socks-test?via=socks", payload
assert payload["method"] == "GET", payload
PY

curl --fail --silent --show-error --insecure --max-time 30 \
  --proxy "http://127.0.0.1:${HELPER_HTTP_PORT}" \
  "https://127.0.0.1:19443/secure-test?via=http" \
  > "$TMP_DIR/http.txt"

grep -q 'secure:/secure-test?via=http' "$TMP_DIR/http.txt"

grep -q '"kind":"origin_connect_ok"' "$TMP_DIR/logs/agent-events.ndjson"
grep -q '"kind":"stream_open_ok"' "$TMP_DIR/logs/helper-events.ndjson"

echo "TWOMAN REMOTE HTTP E2E OK"
